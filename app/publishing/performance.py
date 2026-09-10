from __future__ import annotations

from statistics import median

from sqlalchemy.orm import Session

from app.config import Settings
from app.creative_production.repositories import get_package
from app.publishing.registry import PublisherRegistry
from app.publishing.repositories import (
    get_publication,
    get_snapshot,
    list_creative_performance,
    list_learning,
    list_performance_snapshots,
    list_winning_patterns,
    save_audit,
    save_creative_performance,
    save_learning,
    save_performance_snapshot,
    save_winning_pattern,
)
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    ContentPerformanceSnapshot,
    CreativePerformanceRecord,
    LearningRecord,
    NormalizedPerformance,
    PerformanceInsight,
    PublishingPlatform,
    WinningPattern,
)


def _ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _known_sum(*values: int | None) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


class PerformanceNormalizer:
    @staticmethod
    def normalize(snapshot: ContentPerformanceSnapshot) -> NormalizedPerformance:
        exposure = snapshot.reach
        if exposure is None:
            exposure = snapshot.impressions
        if exposure is None:
            exposure = snapshot.views
        engagement = _known_sum(
            snapshot.likes,
            snapshot.comments,
            snapshot.shares,
            snapshot.saves,
        )
        return NormalizedPerformance(
            performance_snapshot_id=snapshot.performance_snapshot_id,
            platform=snapshot.platform,
            engagement_rate=_ratio(engagement, exposure),
            view_to_like_rate=_ratio(snapshot.likes, snapshot.views),
            comment_rate=_ratio(snapshot.comments, exposure),
            share_rate=_ratio(snapshot.shares, exposure),
            save_rate=_ratio(snapshot.saves, exposure),
            ctr=(
                snapshot.ctr
                if snapshot.ctr is not None
                else _ratio(snapshot.clicks, snapshot.impressions)
            ),
            completion_rate=snapshot.completion_rate,
            conversion_rate=_ratio(snapshot.conversions, snapshot.clicks),
        )


class PerformanceAnalysisAgent:
    """Separates measured observations from explicitly labelled hypotheses."""

    def analyze(
        self,
        snapshot: ContentPerformanceSnapshot,
        baseline: list[ContentPerformanceSnapshot],
    ) -> list[PerformanceInsight]:
        comparable = [item for item in baseline if item.platform is snapshot.platform]
        insights: list[PerformanceInsight] = []
        completion_values = [
            item.completion_rate
            for item in comparable
            if item.completion_rate is not None
            and item.performance_snapshot_id != snapshot.performance_snapshot_id
        ]
        if snapshot.completion_rate is not None:
            if completion_values:
                base = median(completion_values)
                delta = snapshot.completion_rate - base
                observation = (
                    f"{snapshot.platform.value} completion rateは"
                    f"{snapshot.completion_rate:.1%}、同Platform基準{base:.1%}、差{delta:+.1%}です。"
                )
                interpretation = (
                    "Retention設計が寄与した可能性があります。Hook・尺との因果は追加検証が必要です。"
                    if delta > 0
                    else "Retentionが弱い可能性があります。冒頭離脱と尺を追加検証してください。"
                )
                confidence = min(0.85, 0.45 + len(completion_values) * 0.05)
            else:
                observation = (
                    f"{snapshot.platform.value} completion rateは"
                    f"{snapshot.completion_rate:.1%}です。比較標本はありません。"
                )
                interpretation = "初期観測であり、成功要因はまだ判断できません。"
                confidence = 0.25
            insights.append(
                PerformanceInsight(
                    publication_id=snapshot.publication_id,
                    campaign_id=snapshot.campaign_id,
                    platform=snapshot.platform,
                    observation=observation,
                    interpretation=interpretation,
                    confidence=confidence,
                    supporting_snapshot_ids=[snapshot.performance_snapshot_id]
                    + [item.performance_snapshot_id for item in comparable[-10:]],
                )
            )

        normalized = PerformanceNormalizer.normalize(snapshot)
        if normalized.ctr is not None:
            insights.append(
                PerformanceInsight(
                    publication_id=snapshot.publication_id,
                    campaign_id=snapshot.campaign_id,
                    platform=snapshot.platform,
                    observation=f"CTRは{normalized.ctr:.2%}です。",
                    interpretation="CTAまたは遷移先との適合を示す可能性があります。因果は未確定です。",
                    confidence=0.35,
                    supporting_snapshot_ids=[snapshot.performance_snapshot_id],
                )
            )
        if not insights:
            insights.append(
                PerformanceInsight(
                    publication_id=snapshot.publication_id,
                    campaign_id=snapshot.campaign_id,
                    platform=snapshot.platform,
                    observation="分析可能なCTR・Completion Rateがまだありません。",
                    interpretation="Metric取得範囲を増やしてから解釈してください。",
                    confidence=0.1,
                    supporting_snapshot_ids=[snapshot.performance_snapshot_id],
                )
            )
        return insights


class PerformanceCollector:
    def __init__(self, settings: Settings, registry: PublisherRegistry) -> None:
        self._settings = settings
        self._registry = registry

    async def collect(
        self,
        session: Session,
        publication_id: str,
        window: str,
    ) -> ContentPerformanceSnapshot | None:
        if window not in self._settings.effective_publishing_performance_windows:
            raise ValueError(f"Unsupported performance window: {window}")
        existing = [
            item
            for item in list_performance_snapshots(session, publication_id=publication_id)
            if item.window == window
        ]
        if existing:
            return existing[0]
        publication = get_publication(session, publication_id)
        if publication is None:
            raise LookupError(f"Publication {publication_id} was not found.")
        provider = self._registry.get(publication.platform)
        snapshot = await provider.fetch_metrics(publication, window)
        if snapshot is None:
            return None
        save_performance_snapshot(session, snapshot)
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.PERFORMANCE_COLLECTED,
                actor="system",
                campaign_id=publication.campaign_id,
                snapshot_id=publication.snapshot_id,
                publication_id=publication.publication_id,
                platform=publication.platform,
                target_account_id=publication.target_account_id,
                provider=publication.provider,
                request_id=publication.request_id,
                status="performance_collected",
                metadata={"window": window},
            ),
        )
        return snapshot


class LearningService:
    def ingest(
        self,
        session: Session,
        performance: ContentPerformanceSnapshot,
    ) -> tuple[CreativePerformanceRecord, list[LearningRecord], WinningPattern | None]:
        publication = get_publication(session, performance.publication_id)
        if publication is None:
            raise LookupError("External Publicationがありません。")
        snapshot = get_snapshot(session, publication.snapshot_id)
        if snapshot is None:
            raise LookupError("Approved Snapshotがありません。")
        package = get_package(session, snapshot.content_package_id)
        if package is None:
            raise LookupError("Content Packageがありません。")
        candidate = next(
            (
                item
                for item in package.candidates
                if item.candidate_id == snapshot.source_candidate_id
            ),
            None,
        )
        if candidate is None:
            raise LookupError("Source Candidateがありません。")
        normalized = PerformanceNormalizer.normalize(performance)
        score_values = [
            score.overall
            for score in package.scores
            if score.candidate_id == candidate.candidate_id
        ]
        judge_score = median(score_values) if score_values else None
        hook = str(candidate.structured_content.get("hook", "")) or candidate.content[:120]
        hook_type = "question" if "?" in hook or "？" in hook else "statement"
        high_performer = any(
            value is not None and value >= threshold
            for value, threshold in (
                (normalized.engagement_rate, 0.08),
                (normalized.ctr, 0.04),
                (normalized.completion_rate, 0.40),
            )
        )
        metrics = normalized.model_dump(
            mode="json", exclude={"platform", "performance_snapshot_id"}
        )
        record = CreativePerformanceRecord(
            campaign_id=package.campaign.campaign_id,
            publication_id=publication.publication_id,
            market=package.content_brief.market,
            audience=package.content_brief.target_audience,
            platform=publication.platform,
            content_type=str(
                snapshot.metadata.get("creative_platform", publication.platform.value)
            ),
            content_variant=candidate.variant,
            hook_type=hook_type,
            message_angle=package.campaign.primary_message,
            visual_style=package.brand_visual_profile.image_style,
            cta_type=package.content_brief.cta,
            content_length=len(snapshot.text or snapshot.caption),
            posting_time=publication.published_at or performance.measured_at,
            provider=candidate.provider,
            model=candidate.model,
            judge_score=judge_score,
            performance_metrics=metrics,
            high_performer=high_performer,
        )
        save_creative_performance(session, record)

        insights = PerformanceAnalysisAgent().analyze(
            performance,
            list_performance_snapshots(session, campaign_id=performance.campaign_id),
        )
        learnings = [
            LearningRecord(
                campaign_id=performance.campaign_id,
                market=record.market,
                audience=record.audience,
                platform=performance.platform,
                category="creative_performance",
                observation=insight.observation,
                interpretation=insight.interpretation,
                recommendation=(
                    f"次回は{hook_type} hookを探索枠で再検証してください。"
                    if high_performer
                    else "Hook・CTA・投稿時間を一要素ずつ変更して再検証してください。"
                ),
                evidence_snapshot_ids=insight.supporting_snapshot_ids,
                confidence=insight.confidence,
            )
            for insight in insights
        ]
        for learning in learnings:
            save_learning(session, learning)

        comparable = [
            item
            for item in list_creative_performance(session)
            if item.market == record.market
            and item.platform is record.platform
            and item.high_performer
        ]
        pattern: WinningPattern | None = None
        if len(comparable) >= 3:
            existing_patterns = [
                item
                for item in list_winning_patterns(session, record.market)
                if item.platform is record.platform
                and item.hook_type == record.hook_type
                and item.cta_type == record.cta_type
            ]
            if not existing_patterns:
                pattern = WinningPattern(
                    market=record.market,
                    audience=record.audience,
                    platform=record.platform,
                    hook_type=record.hook_type,
                    content_type=record.content_type,
                    visual_style=record.visual_style,
                    cta_type=record.cta_type,
                    sample_size=len(comparable),
                    percentile=0.9,
                    metrics=record.performance_metrics,
                )
                save_winning_pattern(session, pattern)
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.LEARNING_CREATED,
                actor="system",
                campaign_id=record.campaign_id,
                publication_id=record.publication_id,
                platform=record.platform,
                status="analyzed",
                metadata={
                    "learning_ids": [item.learning_id for item in learnings],
                    "pattern_id": pattern.pattern_id if pattern else None,
                },
            ),
        )
        return record, learnings, pattern


def campaign_learning_signals(
    session: Session,
    market: str,
    platforms: list[PublishingPlatform],
    *,
    limit: int = 10,
) -> list[str]:
    platform_set = set(platforms)
    learnings = [
        item
        for item in list_learning(session, market=market)
        if item.platform is None or item.platform in platform_set
    ]
    patterns = [
        item for item in list_winning_patterns(session, market) if item.platform in platform_set
    ]
    signals = [
        f"Performance observation: {item.observation} Recommendation: {item.recommendation}"
        for item in learnings[:limit]
    ]
    signals.extend(
        f"Winning pattern ({item.platform.value}, n={item.sample_size}): "
        f"hook={item.hook_type}, CTA={item.cta_type}"
        for item in patterns[:limit]
    )
    return signals[:limit]


def research_learning_signals(
    session: Session,
    market: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Returns labelled hypotheses for Research, never market-evidence claims."""

    learnings = [item for item in list_learning(session, market=market) if item.confidence >= 0.35]
    patterns = list_winning_patterns(session, market)
    signals = [
        "NOT market evidence: Historical content response — "
        f"{item.observation} Hypothesis: {item.interpretation} "
        f"Next validation: {item.recommendation}"
        for item in learnings[:limit]
    ]
    signals.extend(
        "NOT market evidence: Historical winning pattern — "
        f"{item.platform.value}, n={item.sample_size}, hook={item.hook_type}, "
        f"CTA={item.cta_type}"
        for item in patterns[:limit]
    )
    return signals[:limit]
