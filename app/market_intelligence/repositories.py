from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_intelligence.models import (
    MIAgentOutput,
    MIClaim,
    MIEvidence,
    MINormalizedSocialItem,
    MIRawSocialItem,
    MIReport,
    MIResearchRun,
    MISourceQuery,
)
from app.market_intelligence.schemas import (
    AgentOutput,
    Claim,
    DataMode,
    EvidenceRecord,
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    ResearchStatus,
    SocialItem,
    SourceName,
    SourceRunResult,
    StructuredError,
)


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def create_run(
    session: Session,
    *,
    run_id: str,
    request: ResearchRequest,
    capability_snapshot: dict[str, Any],
    configuration_snapshot: dict[str, Any],
) -> MIResearchRun:
    now = datetime.now(UTC)
    row = MIResearchRun(
        id=run_id,
        status=ResearchStatus.QUEUED.value,
        market=request.market,
        request_json=_json(request),
        plan_json=None,
        capability_snapshot=capability_snapshot,
        configuration_snapshot=configuration_snapshot,
        progress_json={"stage": ResearchStatus.QUEUED.value, "message": "受付済み"},
        is_mock=request.mock_mode,
        error_json=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def update_run(
    session: Session,
    run_id: str,
    status: ResearchStatus,
    *,
    progress: dict[str, Any] | None = None,
    plan: ResearchPlan | None = None,
    error: StructuredError | None = None,
) -> None:
    row = session.get(MIResearchRun, run_id)
    if row is None:
        raise LookupError(f"Research run {run_id} was not found.")
    now = datetime.now(UTC)
    row.status = status.value
    row.updated_at = now
    if row.started_at is None and status is not ResearchStatus.QUEUED:
        row.started_at = now
    if status in {ResearchStatus.COMPLETED, ResearchStatus.PARTIAL, ResearchStatus.FAILED}:
        row.completed_at = now
    if progress is not None:
        row.progress_json = progress
    if plan is not None:
        row.plan_json = _json(plan)
    if error is not None:
        row.error_json = _json(error)
    session.flush()


def save_source_results(
    session: Session,
    run_id: str,
    plan: ResearchPlan,
    results: dict[SourceName, SourceRunResult],
) -> None:
    for source, source_plan in plan.source_plans.items():
        result = results[source]
        for query in source_plan.queries:
            digest = hashlib.sha256(
                f"{source.value}:{query}:{source_plan.parameters}".encode()
            ).hexdigest()
            session.add(
                MISourceQuery(
                    research_run_id=run_id,
                    source=source.value,
                    query=query,
                    parameters_json=source_plan.parameters,
                    cache_key=digest,
                    status=result.status.value,
                    item_count=len(result.items),
                    duration_ms=result.duration_ms,
                    error_json=_json(result.error) if result.error else None,
                )
            )
        for item in result.items:
            session.add(
                MIRawSocialItem(
                    raw_id=item.raw_id,
                    research_run_id=item.research_run_id,
                    platform=item.platform.value,
                    source_id=item.source_id,
                    source_url=item.source_url,
                    query=item.query,
                    retrieved_at=item.retrieved_at,
                    raw_payload=item.raw_payload,
                    data_mode=item.data_mode.value,
                    is_mock=item.is_mock,
                )
            )
    session.flush()


def save_normalized_items(session: Session, items: list[SocialItem]) -> None:
    for item in items:
        session.add(
            MINormalizedSocialItem(
                id=item.id,
                raw_id=item.raw_id,
                research_run_id=item.research_run_id,
                platform=item.platform.value,
                source_id=item.source_id,
                source_url=item.source_url,
                author_id=item.author_id,
                author_name=item.author_name,
                created_at=item.created_at,
                retrieved_at=item.retrieved_at,
                text=item.text,
                title=item.title,
                language=item.language,
                likes=item.likes,
                comments=item.comments,
                shares=item.shares,
                views=item.views,
                score=item.score,
                hashtags=item.hashtags,
                keywords=item.keywords,
                query=item.query,
                market=item.market,
                raw_payload=item.raw_payload,
                data_mode=item.data_mode.value,
                is_mock=item.is_mock,
                spam_score=item.spam_score,
                bot_score=item.bot_score,
                relevance_score=item.relevance_score,
                quality_score=item.quality_score,
                duplicate_group=item.duplicate_group,
                advertisement_likelihood=item.advertisement_likelihood,
            )
        )
    session.flush()


def save_evidence(session: Session, records: list[EvidenceRecord]) -> None:
    for record in records:
        session.add(
            MIEvidence(
                evidence_id=record.evidence_id,
                research_run_id=record.research_run_id,
                platform=(
                    record.platform.value
                    if isinstance(record.platform, SourceName)
                    else record.platform
                ),
                source_item_ids=record.source_item_ids,
                claim=record.claim,
                sample_size=record.sample_size,
                query=record.query,
                date_from=record.date_from.isoformat(),
                date_to=record.date_to.isoformat(),
                metrics_json=record.metrics,
                support_score=record.support_score,
                counter_evidence_ids=record.counter_evidence_ids,
                created_at=record.created_at,
            )
        )
    session.flush()


def save_agent_outputs(session: Session, outputs: list[AgentOutput]) -> None:
    for output in outputs:
        session.add(
            MIAgentOutput(
                agent_run_id=output.agent_run_id,
                research_run_id=output.research_run_id,
                agent_name=output.agent_name,
                provider=output.provider,
                model=output.model,
                prompt_version=output.prompt_version,
                output_json=_json(output),
                duration_ms=output.duration_ms,
                input_tokens=output.input_tokens,
                output_tokens=output.output_tokens,
                estimated_cost=output.estimated_cost,
                error_json=_json(output.error) if output.error else None,
            )
        )
    session.flush()


def save_claims(session: Session, claims: list[Claim]) -> None:
    for claim in claims:
        session.add(
            MIClaim(
                claim_id=claim.claim_id,
                research_run_id=claim.research_run_id,
                claim=claim.claim,
                claim_type=claim.type,
                confidence=claim.confidence.score,
                confidence_json=_json(claim.confidence),
                evidence_ids=claim.evidence_ids,
                counter_evidence_ids=claim.counter_evidence_ids,
                platforms=[platform.value for platform in claim.platforms],
                verification=claim.verification.value,
                agent_name=claim.agent_name,
            )
        )
    session.flush()


def save_report(session: Session, report: ResearchReport) -> None:
    session.add(
        MIReport(
            research_run_id=report.research_run_id,
            report_json=_json(report),
            markdown=report.markdown,
            generated_at=report.generated_at,
        )
    )
    session.flush()


def list_runs(session: Session, limit: int = 50) -> list[MIResearchRun]:
    statement = select(MIResearchRun).order_by(MIResearchRun.created_at.desc()).limit(limit)
    return list(session.scalars(statement))


def get_run(session: Session, run_id: str) -> MIResearchRun | None:
    return session.get(MIResearchRun, run_id)


def get_report(session: Session, run_id: str) -> ResearchReport | None:
    row = session.get(MIReport, run_id)
    return ResearchReport.model_validate(row.report_json) if row else None


def get_evidence(session: Session, run_id: str) -> list[EvidenceRecord]:
    statement = select(MIEvidence).where(MIEvidence.research_run_id == run_id)
    rows = session.scalars(statement)
    return [
        EvidenceRecord(
            evidence_id=row.evidence_id,
            research_run_id=row.research_run_id,
            platform=(
                "cross_source" if row.platform == "cross_source" else SourceName(row.platform)
            ),
            source_item_ids=row.source_item_ids,
            claim=row.claim,
            sample_size=row.sample_size,
            query=row.query,
            date_from=date.fromisoformat(row.date_from),
            date_to=date.fromisoformat(row.date_to),
            metrics=row.metrics_json,
            support_score=row.support_score,
            counter_evidence_ids=row.counter_evidence_ids,
            created_at=row.created_at,
        )
        for row in rows
    ]


def get_items(session: Session, run_id: str) -> list[SocialItem]:
    statement = select(MINormalizedSocialItem).where(
        MINormalizedSocialItem.research_run_id == run_id
    )
    rows = session.scalars(statement)
    return [
        SocialItem(
            id=row.id,
            raw_id=row.raw_id,
            research_run_id=row.research_run_id,
            platform=SourceName(row.platform),
            source_id=row.source_id,
            source_url=row.source_url,
            author_id=row.author_id,
            author_name=row.author_name,
            created_at=row.created_at,
            retrieved_at=row.retrieved_at,
            text=row.text,
            title=row.title,
            language=row.language,
            likes=row.likes,
            comments=row.comments,
            shares=row.shares,
            views=row.views,
            score=row.score,
            hashtags=row.hashtags,
            keywords=row.keywords,
            query=row.query,
            market=row.market,
            raw_payload=row.raw_payload,
            data_mode=DataMode(row.data_mode),
            is_mock=row.is_mock,
            spam_score=row.spam_score,
            bot_score=row.bot_score,
            relevance_score=row.relevance_score,
            quality_score=row.quality_score,
            duplicate_group=row.duplicate_group,
            advertisement_likelihood=row.advertisement_likelihood,
        )
        for row in rows
    ]
