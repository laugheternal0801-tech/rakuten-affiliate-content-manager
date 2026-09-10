from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.config import Settings
from app.creative_production.schemas import CreativePlatform
from app.publishing.controls import ensure_control
from app.publishing.orchestrator import PublishingOrchestrator
from app.publishing.performance import (
    LearningService,
    PerformanceAnalysisAgent,
    PerformanceCollector,
    PerformanceNormalizer,
    campaign_learning_signals,
    research_learning_signals,
)
from app.publishing.publishers.mock_publisher import MockPublishingProvider
from app.publishing.registry import PublisherRegistry
from app.publishing.repositories import (
    list_performance_snapshots,
    list_publications,
    save_control,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    ContentPerformanceSnapshot,
    PublishingControl,
    PublishingPlatform,
    PublishingStatus,
)
from tests.publishing_support import PublishingFactory


def _settings(**updates: Any) -> Settings:
    defaults: dict[str, Any] = {
        "publishing_enabled": True,
        "publishing_external_api_enabled": True,
        "publishing_dry_run": False,
    }
    defaults.update(updates)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def _metric_snapshot(**updates: Any) -> ContentPerformanceSnapshot:
    values: dict[str, Any] = {
        "publication_id": "PUB-test",
        "campaign_id": "CAM-test",
        "platform": PublishingPlatform.X,
        "age_hours": 24,
        "window": "24h",
    }
    values.update(updates)
    return ContentPerformanceSnapshot(**values)


def test_normalizer_preserves_unknown_metrics_and_real_zero() -> None:
    unknown = PerformanceNormalizer.normalize(
        _metric_snapshot(impressions=100, likes=None, comments=None)
    )
    zero = PerformanceNormalizer.normalize(
        _metric_snapshot(impressions=100, likes=0, comments=0, shares=0, saves=0)
    )

    assert unknown.engagement_rate is None
    assert zero.engagement_rate == 0.0
    assert zero.ctr is None


def test_analysis_separates_observation_from_hypothesis() -> None:
    baseline = _metric_snapshot(
        publication_id="PUB-base",
        completion_rate=0.25,
    )
    current = _metric_snapshot(completion_rate=0.5, clicks=50, impressions=1000)

    insights = PerformanceAnalysisAgent().analyze(current, [baseline, current])

    assert "50.0%" in insights[0].observation
    assert "可能性" not in insights[0].observation
    assert "可能性" in insights[0].interpretation
    assert insights[0].confidence < 1


def test_collector_is_idempotent_and_preserves_provider_nulls(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _settings()
    save_control(
        publishing_factory.session,
        PublishingControl(
            publishing_enabled=True,
            dry_run=False,
            updated_by="performance-test",
        ),
    )
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    snapshot = publishing_factory.approve(
        package,
        [CreativePlatform.X],
        {CreativePlatform.X: account},
    )[0]
    _, job = PublishingScheduler(settings).create_approved_schedule(
        publishing_factory.session,
        snapshot.snapshot_id,
        datetime.now(UTC),
        "Asia/Tokyo",
        "performance-test",
    )
    provider = MockPublishingProvider(
        PublishingPlatform.X,
        metrics={"impressions": 1000, "likes": 0, "comments": None},
    )
    registry = PublisherRegistry([provider])
    result = asyncio.run(
        PublishingOrchestrator(settings, registry).dispatch(publishing_factory.session, job.job_id)
    )
    assert result.status is PublishingStatus.PUBLISHED
    publications = list_publications(
        publishing_factory.session, campaign_id=package.campaign.campaign_id
    )

    collector = PerformanceCollector(settings, registry)
    first = asyncio.run(
        collector.collect(
            publishing_factory.session,
            publications[0].publication_id,
            "24h",
        )
    )
    second = asyncio.run(
        collector.collect(
            publishing_factory.session,
            publications[0].publication_id,
            "24h",
        )
    )

    assert first is not None
    assert second is not None
    assert first.performance_snapshot_id == second.performance_snapshot_id
    assert first.likes == 0
    assert first.comments is None
    assert len(list_performance_snapshots(publishing_factory.session)) == 1


def test_three_real_high_performers_create_reusable_but_labelled_signal(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _settings()
    ensure_control(publishing_factory.session, settings)
    save_control(
        publishing_factory.session,
        PublishingControl(
            publishing_enabled=True,
            dry_run=False,
            updated_by="learning-test",
        ),
    )
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    provider = MockPublishingProvider(
        PublishingPlatform.X,
        metrics={
            "impressions": 1000,
            "likes": 100,
            "comments": 5,
            "shares": 5,
            "saves": 5,
            "clicks": 50,
            "completion_rate": 0.5,
        },
    )
    registry = PublisherRegistry([provider])
    orchestrator = PublishingOrchestrator(settings, registry)
    collector = PerformanceCollector(settings, registry)
    learning = LearningService()
    final_pattern = None

    for index in range(3):
        package, _ = publishing_factory.package(
            [CreativePlatform.X],
            content=f"確認済みEvidenceから判断基準を整理します。検証{index}",
        )
        snapshot = publishing_factory.approve(
            package,
            [CreativePlatform.X],
            {CreativePlatform.X: account},
        )[0]
        _, job = PublishingScheduler(settings).create_approved_schedule(
            publishing_factory.session,
            snapshot.snapshot_id,
            datetime.now(UTC),
            "Asia/Tokyo",
            "learning-test",
        )
        result = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
        assert result.status is PublishingStatus.PUBLISHED
        publication = list_publications(
            publishing_factory.session,
            campaign_id=package.campaign.campaign_id,
        )[0]
        performance = asyncio.run(
            collector.collect(
                publishing_factory.session,
                publication.publication_id,
                "24h",
            )
        )
        assert performance is not None
        record, learnings, final_pattern = learning.ingest(publishing_factory.session, performance)
        assert record.provider == "local-editorial"
        assert record.judge_score == pytest.approx(89)
        assert learnings[0].observation

    assert final_pattern is not None
    assert final_pattern.sample_size == 3
    signals = campaign_learning_signals(
        publishing_factory.session,
        "AI副業",
        [PublishingPlatform.X],
    )
    assert any(signal.startswith("Performance observation:") for signal in signals)
    assert any(signal.startswith("Winning pattern") for signal in signals)
    research_signals = research_learning_signals(
        publishing_factory.session,
        "AI副業",
    )
    assert research_signals
    assert all(signal.startswith("NOT market evidence:") for signal in research_signals)
