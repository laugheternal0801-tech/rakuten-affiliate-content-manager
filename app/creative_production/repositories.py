from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.creative_production.models import (
    CreativeApprovalRow,
    CreativeAssetRow,
    CreativeBriefRow,
    CreativeCampaignRow,
    CreativeCandidateRow,
    CreativeCostRow,
    CreativeEvaluationRow,
    CreativeJobRow,
    CreativePackageRow,
    CreativePerformanceRow,
)
from app.creative_production.schemas import (
    ApprovalDecision,
    AssetRecord,
    CampaignPlan,
    ContentBrief,
    ContentCandidate,
    ContentPackage,
    ContentPerformance,
    CostEntry,
    CreativeProductionRequest,
    CreativeScore,
    CreativeStatus,
    CriticReport,
)


def _json(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def create_campaign(
    session: Session,
    campaign: CampaignPlan,
    request: CreativeProductionRequest,
    configuration_snapshot: dict[str, Any],
) -> None:
    session.add(
        CreativeCampaignRow(
            id=campaign.campaign_id,
            research_run_id=campaign.research_run_id,
            status=CreativeStatus.PLANNING.value,
            quality_level=request.quality_level.value,
            plan_json=_json(campaign),
            request_json=_json(request),
            configuration_snapshot=configuration_snapshot,
            degradation_reasons=[],
            error_json=None,
        )
    )
    session.add(
        CreativeJobRow(
            id=f"JOB-{campaign.campaign_id}",
            campaign_id=campaign.campaign_id,
            status=CreativeStatus.PLANNING.value,
            stage="campaign_planning",
            progress=0.05,
            details_json={},
            error_json=None,
        )
    )
    session.flush()


def save_brief(session: Session, brief: ContentBrief) -> None:
    session.add(
        CreativeBriefRow(
            id=brief.content_brief_id,
            campaign_id=brief.campaign_id,
            research_run_id=brief.research_run_id,
            brief_json=_json(brief),
        )
    )
    session.flush()


def update_job(
    session: Session,
    campaign_id: str,
    status: CreativeStatus,
    stage: str,
    progress: float,
    *,
    details: dict[str, Any] | None = None,
    degradation_reasons: list[str] | None = None,
) -> None:
    campaign = session.get(CreativeCampaignRow, campaign_id)
    job = session.get(CreativeJobRow, f"JOB-{campaign_id}")
    if campaign is None or job is None:
        raise LookupError(f"Creative campaign {campaign_id} was not found.")
    campaign.status = status.value
    campaign.updated_at = datetime.now(UTC)
    if degradation_reasons is not None:
        campaign.degradation_reasons = degradation_reasons
    job.status = status.value
    job.stage = stage
    job.progress = max(0.0, min(1.0, progress))
    job.details_json = details or {}
    job.updated_at = datetime.now(UTC)
    session.flush()


def save_candidates(
    session: Session,
    campaign_id: str,
    candidates: list[ContentCandidate],
    final_ids: set[str],
) -> None:
    for candidate in candidates:
        session.add(
            CreativeCandidateRow(
                id=candidate.candidate_id,
                campaign_id=campaign_id,
                content_brief_id=candidate.content_brief_id,
                platform=candidate.platform.value,
                provider=candidate.provider,
                model=candidate.model,
                revision_round=candidate.revision_round,
                candidate_json=_json(candidate),
                is_final=candidate.candidate_id in final_ids,
                created_at=candidate.created_at,
            )
        )
    session.flush()


def save_evaluations(
    session: Session,
    campaign_id: str,
    scores: list[CreativeScore],
    critics: list[CriticReport],
) -> None:
    for score in scores:
        session.add(
            CreativeEvaluationRow(
                id=score.score_id,
                campaign_id=campaign_id,
                candidate_id=score.candidate_id,
                evaluator=score.judge_name,
                evaluation_type="score",
                overall_score=score.overall,
                evaluation_json=_json(score),
            )
        )
    for critic in critics:
        session.add(
            CreativeEvaluationRow(
                id=critic.critic_report_id,
                campaign_id=campaign_id,
                candidate_id=critic.candidate_id,
                evaluator=critic.critic_name,
                evaluation_type="critic",
                overall_score=None,
                evaluation_json=_json(critic),
            )
        )
    session.flush()


def save_assets(session: Session, assets: list[AssetRecord]) -> None:
    for asset in assets:
        session.add(
            CreativeAssetRow(
                id=asset.asset_id,
                campaign_id=asset.campaign_id,
                content_brief_id=asset.content_brief_id,
                kind=asset.kind.value,
                provider=asset.provider,
                model=asset.model,
                file_path=asset.file_path,
                mime_type=asset.mime_type,
                parent_asset_id=asset.parent_asset_id,
                revision_number=asset.revision_number,
                is_placeholder=asset.is_placeholder,
                asset_json=_json(asset),
                created_at=asset.created_at,
            )
        )
    session.flush()


def save_costs(session: Session, costs: list[CostEntry]) -> None:
    for cost in costs:
        session.add(
            CreativeCostRow(
                id=cost.cost_id,
                campaign_id=cost.campaign_id,
                content_id=cost.content_id,
                provider=cost.provider,
                model=cost.model,
                operation=cost.operation,
                quantity=cost.quantity,
                estimated_cost=cost.estimated_cost,
                currency=cost.currency,
                created_at=cost.created_at,
            )
        )
    session.flush()


def save_package(session: Session, package: ContentPackage) -> None:
    session.add(
        CreativePackageRow(
            id=package.package_id,
            campaign_id=package.campaign.campaign_id,
            status=package.status.value,
            version=package.version,
            degraded_quality_mode=package.degraded_quality_mode,
            package_json=_json(package),
            created_at=package.created_at,
            updated_at=package.updated_at,
        )
    )
    session.flush()


def list_packages(session: Session, limit: int = 50) -> list[CreativePackageRow]:
    statement = (
        select(CreativePackageRow).order_by(CreativePackageRow.created_at.desc()).limit(limit)
    )
    return list(session.scalars(statement))


def get_package(session: Session, package_id: str) -> ContentPackage | None:
    row = session.get(CreativePackageRow, package_id)
    return ContentPackage.model_validate(row.package_json) if row else None


def record_approval(
    session: Session,
    package_id: str,
    decision: ApprovalDecision,
    reviewer: str,
    feedback: str,
) -> ContentPackage:
    row = session.get(CreativePackageRow, package_id)
    if row is None:
        raise LookupError(f"Content package {package_id} was not found.")
    package = ContentPackage.model_validate(row.package_json)
    if decision is ApprovalDecision.APPROVE and any(
        "最終画像はDRAFT PLACEHOLDER" in reason or "実Video Provider未接続" in reason
        for reason in package.degradation_reasons
    ):
        raise ValueError(
            "Placeholder画像・Storyboardは公開承認できません。実Assetへ差し替えてください。"
        )
    status = {
        ApprovalDecision.APPROVE: CreativeStatus.APPROVED,
        ApprovalDecision.REQUEST_REVISION: CreativeStatus.REVISING,
        ApprovalDecision.REJECT: CreativeStatus.REJECTED,
    }[decision]
    updated = package.model_copy(
        update={
            "status": status,
            "updated_at": datetime.now(UTC),
            "version": package.version
            + (1 if decision is ApprovalDecision.REQUEST_REVISION else 0),
        }
    )
    row.status = status.value
    row.version = updated.version
    row.package_json = _json(updated)
    row.updated_at = updated.updated_at
    campaign = session.get(CreativeCampaignRow, package.campaign.campaign_id)
    if campaign:
        campaign.status = status.value
    session.add(
        CreativeApprovalRow(
            package_id=package_id,
            decision=decision.value,
            reviewer=reviewer,
            feedback=feedback,
        )
    )
    session.flush()
    return updated


def save_performance(session: Session, performance: ContentPerformance) -> None:
    session.add(
        CreativePerformanceRow(
            id=performance.performance_id,
            package_id=performance.package_id,
            platform=performance.platform.value,
            provider=performance.provider,
            model=performance.model,
            metrics_json=_json(performance),
            captured_at=performance.captured_at,
        )
    )
    session.flush()


def list_performance(session: Session, package_id: str | None = None) -> list[ContentPerformance]:
    statement = select(CreativePerformanceRow).order_by(CreativePerformanceRow.captured_at.desc())
    if package_id:
        statement = statement.where(CreativePerformanceRow.package_id == package_id)
    return [
        ContentPerformance.model_validate(row.metrics_json) for row in session.scalars(statement)
    ]


def asset_lineage(session: Session, asset_id: str) -> list[AssetRecord]:
    lineage: list[AssetRecord] = []
    seen: set[str] = set()
    current_id: str | None = asset_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        row = session.get(CreativeAssetRow, current_id)
        if row is None:
            break
        lineage.append(AssetRecord.model_validate(row.asset_json))
        current_id = row.parent_asset_id
    return lineage
