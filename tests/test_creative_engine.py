from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.creative_production.engine import CreativeProductionEngine
from app.creative_production.exporter import package_as_json, package_as_markdown, package_as_zip
from app.creative_production.models import (
    CreativeAssetRow,
    CreativeCampaignRow,
    CreativeCandidateRow,
    CreativePackageRow,
)
from app.creative_production.performance import PerformanceAgent, RuleBasedModelRouter
from app.creative_production.repositories import (
    asset_lineage,
    get_package,
    record_approval,
)
from app.creative_production.schemas import (
    ApprovalDecision,
    ContentPerformance,
    CreativePlatform,
    CreativeProductionRequest,
    CreativeQualityLevel,
    CreativeStatus,
)
from app.market_intelligence.repositories import save_report
from app.market_intelligence.schemas import (
    AvailabilityStatus,
    Claim,
    ClaimVerification,
    ConfidenceBreakdown,
    QuantitativeSummary,
    ResearchReport,
    SourceName,
)
from app.models import Base


def market_report() -> ResearchReport:
    confidence = ConfidenceBreakdown(
        evidence_volume=0.8,
        source_diversity=0.7,
        platform_diversity=0.6,
        temporal_consistency=0.7,
        model_agreement=0.8,
        data_quality=0.8,
        counter_evidence=0.9,
        score=0.76,
        label="High",
    )
    claim = Claim(
        research_run_id="MI-creative-test",
        claim="初心者の比較時の迷いが複数標本で確認されています。",
        type="pain_point",
        confidence=confidence,
        evidence_ids=["EV-creative-1"],
        platforms=[SourceName.X, SourceName.REDDIT],
        verification=ClaimVerification.SUPPORTED,
        agent_name="Consumer Agent",
    )
    counts = {
        source: (3 if source in {SourceName.X, SourceName.REDDIT} else 0) for source in SourceName
    }
    distribution = {source: (0.5 if counts[source] else 0.0) for source in SourceName}
    quantitative = QuantitativeSummary(
        total_items=6,
        platform_counts=counts,
        total_engagement=30,
        keyword_frequency={"比較": 4},
        competitor_mentions={},
        platform_distribution=distribution,
        daily_mentions={"2026-08-25": 6},
        source_quality_average=0.8,
    )
    return ResearchReport(
        research_run_id="MI-creative-test",
        title="Creative test report",
        executive_summary="Evidence-linked summary",
        scope={"market": "AI副業", "target_demographic": "初心者"},
        source_coverage={source: AvailabilityStatus.AVAILABLE for source in SourceName},
        sample_size=6,
        key_findings=[claim],
        emerging_trends=[],
        consumer_needs=[],
        pain_points=[claim],
        competitor_landscape=[],
        content_trends=[],
        opportunities=[claim],
        risks=[],
        counter_evidence=[],
        methodology=[],
        data_limitations=[],
        quantitative=quantitative,
        evidence_ids=["EV-creative-1"],
    )


def test_creative_engine_persists_package_assets_and_lineage(tmp_path: Path) -> None:
    db = create_engine("sqlite://")
    Base.metadata.create_all(db)
    settings = Settings(
        _env_file=None,
        creative_asset_dir=str(tmp_path),
        creative_candidates_draft=2,
    )
    request = CreativeProductionRequest(
        research_run_id="MI-creative-test",
        platforms=[
            CreativePlatform.X,
            CreativePlatform.INSTAGRAM_CAROUSEL,
            CreativePlatform.TIKTOK,
        ],
        quality_level=CreativeQualityLevel.DRAFT,
        include_images=True,
        include_video_storyboards=True,
        allow_external_api=False,
    )

    with Session(db) as session:
        save_report(session, market_report())
        package = CreativeProductionEngine(settings).run(session, request)
        session.commit()

        assert package.status is CreativeStatus.AWAITING_APPROVAL
        assert package.degraded_quality_mode is True
        assert set(package.final_content) == {
            CreativePlatform.X,
            CreativePlatform.INSTAGRAM_CAROUSEL,
            CreativePlatform.TIKTOK,
        }
        assert package.evidence_ids == ["EV-creative-1"]
        assert all(candidate.evidence_ids for candidate in package.final_content.values())
        assert session.scalar(select(func.count()).select_from(CreativeCampaignRow)) == 1
        assert session.scalar(select(func.count()).select_from(CreativeCandidateRow)) >= 6
        assert session.scalar(select(func.count()).select_from(CreativeAssetRow)) > 0
        assert session.scalar(select(func.count()).select_from(CreativePackageRow)) == 1
        loaded = get_package(session, package.package_id)
        assert loaded is not None

        composite = next(asset for asset in package.assets if asset.parent_asset_id)
        lineage = asset_lineage(session, composite.asset_id)
        assert [asset.asset_id for asset in lineage] == [
            composite.asset_id,
            composite.parent_asset_id,
        ]

        with pytest.raises(ValueError, match="Placeholder"):
            record_approval(
                session,
                package.package_id,
                ApprovalDecision.APPROVE,
                "reviewer",
                "",
            )

        revised = record_approval(
            session,
            package.package_id,
            ApprovalDecision.REQUEST_REVISION,
            "reviewer",
            "Replace placeholder assets",
        )
        assert revised.status is CreativeStatus.REVISING
        assert revised.version == 2

        assert package.package_id in package_as_json(package)
        assert "Human approval" in package_as_markdown(package)
        archive_data = package_as_zip(package)
        with zipfile.ZipFile(BytesIO(archive_data)) as archive:
            assert {"package.json", "content.md", "manifest.json"} <= set(archive.namelist())


def test_performance_agent_and_router_prioritize_real_results() -> None:
    rows = [
        ContentPerformance(
            package_id=f"PKG-{index}",
            platform=CreativePlatform.X,
            provider="provider-a",
            model="model-a",
            impressions=100,
            clicks=10 + index,
            conversions=1,
        )
        for index in range(3)
    ]
    summaries = PerformanceAgent().analyze(rows)

    selected = RuleBasedModelRouter().select(
        CreativePlatform.X,
        summaries,
        "fallback",
        "fallback-model",
    )

    assert selected[:2] == ("provider-a", "model-a")
    assert selected[2] == "実Performanceを優先"
