from __future__ import annotations

import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.creative_production.planning import BrandVoiceGuard, ClaimGuard
from app.creative_production.schemas import (
    BrandVoiceProfile,
    ContentBrief,
    ContentCandidate,
    CreativePlatform,
    CreativeQualityLevel,
    CreativeScore,
    CriticReport,
    PlatformTask,
)
from app.creative_production.text_providers import TextCreativeProvider, TextProviderRegistry

QUALITY_THRESHOLDS = {
    CreativeQualityLevel.DRAFT: 55.0,
    CreativeQualityLevel.STANDARD: 70.0,
    CreativeQualityLevel.PREMIUM: 80.0,
    CreativeQualityLevel.FLAGSHIP: 88.0,
}

VARIANTS = [
    "information",
    "opinion",
    "discussion",
    "story",
    "contrarian",
    "thread",
    "education",
    "comparison",
]


@dataclass(frozen=True)
class ArenaResult:
    candidates: list[ContentCandidate]
    scores: list[CreativeScore]
    critic_reports: list[CriticReport]
    winner: ContentCandidate
    degraded: bool
    degradation_reasons: tuple[str, ...]


class GenerationArena:
    def __init__(self, registry: TextProviderRegistry, max_workers: int = 6) -> None:
        self._registry = registry
        self._max_workers = max_workers

    def generate(
        self,
        brief: ContentBrief,
        task: PlatformTask,
        candidate_count: int,
    ) -> tuple[list[ContentCandidate], bool, list[str]]:
        providers = [
            provider
            for provider in self._registry.providers
            if provider.health_check().status.value == "available"
        ]
        if not providers:
            raise RuntimeError("利用可能なText Providerがありません。")
        assignments = [
            (providers[index % len(providers)], VARIANTS[index % len(VARIANTS)])
            for index in range(candidate_count)
        ]
        candidates: list[ContentCandidate] = []
        errors: list[str] = []
        with ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(assignments)),
            thread_name_prefix="creative-writer",
        ) as executor:
            futures = {
                executor.submit(provider.generate, brief, task, variant=variant): (
                    provider,
                    variant,
                )
                for provider, variant in assignments
            }
            for future in as_completed(futures):
                provider, variant = futures[future]
                try:
                    candidates.append(future.result())
                except Exception as exc:
                    errors.append(f"{provider.key}/{variant}: {type(exc).__name__}")
        if not candidates:
            raise RuntimeError("すべてのText Provider生成に失敗しました。")
        external_models = {candidate.model for candidate in candidates if candidate.is_external}
        degraded = (
            brief.quality_level
            in {
                CreativeQualityLevel.PREMIUM,
                CreativeQualityLevel.FLAGSHIP,
            }
            and len(external_models) < 2
        )
        if degraded:
            errors.append("Premium/Flagshipに必要な独立外部モデル数を満たしていません。")
        return candidates, degraded, errors


class ObjectiveContentJudge:
    name = "Objective QA"

    def score(
        self,
        candidate: ContentCandidate,
        brief: ContentBrief,
        task: PlatformTask,
    ) -> CreativeScore:
        claim_checks = ClaimGuard(brief.allowed_claims).check(candidate)
        brand_violations = BrandVoiceGuard().check(candidate.content, BrandVoiceProfile())
        evidence = 100.0 if not ClaimGuard.blocks(claim_checks) else 20.0
        structure_hits = sum(
            requirement.casefold().split()[0] in candidate.content.casefold()
            or requirement in str(candidate.structured_content)
            for requirement in task.requirements
        )
        structure = 55 + 45 * structure_hits / max(1, len(task.requirements))
        readability = max(40.0, 100.0 - max(0, len(candidate.content) - 8_000) / 100)
        rubric = {
            "evidence": evidence,
            "structure": min(100.0, structure),
            "readability": min(100.0, readability),
            "brand_safety": 100.0 if not brand_violations else 45.0,
        }
        blocking = [
            check.reason
            for check in claim_checks
            if check.status.value in {"UNSUPPORTED", "CONTRADICTED"}
        ]
        return CreativeScore(
            candidate_id=candidate.candidate_id,
            judge_name=self.name,
            rubric=rubric,
            overall=round(statistics.fmean(rubric.values()), 2),
            reasons=["決定論的な形式・Evidence・Brand検査です。"],
            blocking_issues=[*blocking, *brand_violations],
        )


class PlatformJudge:
    name = "Platform Judge"

    def score(
        self,
        candidate: ContentCandidate,
        brief: ContentBrief,
        task: PlatformTask,
    ) -> CreativeScore:
        content = candidate.content
        hook = 90.0 if content.splitlines() and len(content.splitlines()[0]) <= 80 else 60.0
        platform_fit = 85.0
        if candidate.platform is CreativePlatform.X and len(content) > 560:
            platform_fit = 55.0
        if candidate.platform is CreativePlatform.INSTAGRAM_CAROUSEL:
            slides = candidate.structured_content.get("slides", [])
            platform_fit = 95.0 if len(slides) == 8 else 50.0
        if candidate.platform in {
            CreativePlatform.TIKTOK,
            CreativePlatform.INSTAGRAM_REEL,
            CreativePlatform.YOUTUBE_SHORTS,
        }:
            segments = candidate.structured_content.get("segments", [])
            platform_fit = 92.0 if segments and segments[0].get("end") == 2 else 55.0
        rubric = {
            "hook": hook,
            "platform_fit": platform_fit,
            "clarity": 82.0 if content.strip() else 0.0,
            "cta": 90.0 if brief.cta in content else 55.0,
        }
        return CreativeScore(
            candidate_id=candidate.candidate_id,
            judge_name=self.name,
            rubric=rubric,
            overall=round(statistics.fmean(rubric.values()), 2),
            reasons=[f"{candidate.platform.value}固有要件で評価しました。"],
        )


class EditorialJudge:
    name = "Editorial Judge"

    def score(
        self,
        candidate: ContentCandidate,
        brief: ContentBrief,
        task: PlatformTask,
    ) -> CreativeScore:
        unique_ratio = len(set(candidate.content.split())) / max(1, len(candidate.content.split()))
        rubric = {
            "concept": 82.0,
            "target_fit": 88.0 if brief.target_problem in candidate.content else 72.0,
            "originality": min(95.0, 55 + unique_ratio * 45),
            "execution": 86.0 if len(candidate.content) >= 80 else 60.0,
        }
        return CreativeScore(
            candidate_id=candidate.candidate_id,
            judge_name=self.name,
            rubric=rubric,
            overall=round(statistics.fmean(rubric.values()), 2),
            reasons=["構成、対象適合、表現の反復、完成度を評価しました。"],
        )


class CreativeCritic:
    def critique(
        self,
        candidate: ContentCandidate,
        brief: ContentBrief,
        scores: list[CreativeScore],
    ) -> CriticReport:
        claim_checks = ClaimGuard(brief.allowed_claims).check(candidate)
        brand_violations = BrandVoiceGuard().check(candidate.content, BrandVoiceProfile())
        blocking = [issue for score in scores for issue in score.blocking_issues]
        lowest = min(
            ((criterion, value) for score in scores for criterion, value in score.rubric.items()),
            key=lambda pair: pair[1],
        )
        instructions = [f"{lowest[0]}を具体化して80点以上へ改善する。"]
        if blocking:
            instructions.append("Blocking issueを削除し、Evidence付き表現へ置換する。")
        if brand_violations:
            instructions.append("Brand Voice違反を除去する。")
        aggregate = statistics.fmean(score.overall for score in scores)
        return CriticReport(
            candidate_id=candidate.candidate_id,
            critic_name="Independent Creative Critic",
            strengths=["BriefのCore messageとEvidenceを維持しています。"],
            weaknesses=[*blocking, f"最弱評価軸: {lowest[0]} ({lowest[1]:.1f})"],
            revision_instructions=instructions,
            claim_checks=claim_checks,
            brand_violations=brand_violations,
            platform_violations=[
                issue
                for score in scores
                if score.judge_name == "Platform Judge"
                for issue in score.blocking_issues
            ],
            pass_threshold=not blocking and aggregate >= QUALITY_THRESHOLDS[brief.quality_level],
        )


class CreativeJury:
    def __init__(self) -> None:
        self._judges: list[ObjectiveContentJudge | PlatformJudge | EditorialJudge] = [
            ObjectiveContentJudge(),
            PlatformJudge(),
            EditorialJudge(),
        ]

    def evaluate(
        self,
        candidates: list[ContentCandidate],
        brief: ContentBrief,
        task: PlatformTask,
    ) -> tuple[list[CreativeScore], list[CriticReport], ContentCandidate]:
        scores: list[CreativeScore] = []
        reports: list[CriticReport] = []
        aggregate: dict[str, list[float]] = defaultdict(list)
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        for candidate in candidates:
            candidate_scores = [judge.score(candidate, brief, task) for judge in self._judges]
            scores.extend(candidate_scores)
            reports.append(CreativeCritic().critique(candidate, brief, candidate_scores))
            aggregate[candidate.candidate_id] = [score.overall for score in candidate_scores]
        winner_id = max(
            aggregate,
            key=lambda candidate_id: (
                statistics.median(aggregate[candidate_id]),
                statistics.fmean(aggregate[candidate_id]),
            ),
        )
        return scores, reports, candidate_by_id[winner_id]


class RevisionLoop:
    def __init__(self, registry: TextProviderRegistry, jury: CreativeJury) -> None:
        self._registry = registry
        self._jury = jury

    def run(
        self,
        winner: ContentCandidate,
        brief: ContentBrief,
        task: PlatformTask,
        max_rounds: int,
    ) -> tuple[list[ContentCandidate], list[CreativeScore], list[CriticReport], ContentCandidate]:
        revisions: list[ContentCandidate] = []
        all_scores: list[CreativeScore] = []
        all_reports: list[CriticReport] = []
        current = winner
        for _ in range(max_rounds):
            scores, reports, _ = self._jury.evaluate([current], brief, task)
            all_scores.extend(scores)
            all_reports.extend(reports)
            report = reports[0]
            if report.pass_threshold:
                break
            provider: TextCreativeProvider = self._registry.get(current.provider)
            current = provider.rewrite(
                current,
                brief,
                task,
                report.revision_instructions,
            )
            revisions.append(current)
        final_scores, final_reports, _ = self._jury.evaluate([current], brief, task)
        all_scores.extend(final_scores)
        all_reports.extend(final_reports)
        return revisions, all_scores, all_reports, current


def run_text_arena(
    registry: TextProviderRegistry,
    brief: ContentBrief,
    task: PlatformTask,
    *,
    candidate_count: int,
    max_revision_rounds: int,
    max_workers: int = 6,
) -> ArenaResult:
    initial, degraded, reasons = GenerationArena(registry, max_workers).generate(
        brief, task, candidate_count
    )
    jury = CreativeJury()
    initial_scores, initial_reports, winner = jury.evaluate(initial, brief, task)
    revisions, revision_scores, revision_reports, final = RevisionLoop(registry, jury).run(
        winner,
        brief,
        task,
        max_revision_rounds,
    )
    return ArenaResult(
        candidates=[*initial, *revisions],
        scores=[*initial_scores, *revision_scores],
        critic_reports=[*initial_reports, *revision_reports],
        winner=final,
        degraded=degraded,
        degradation_reasons=tuple(reasons),
    )
