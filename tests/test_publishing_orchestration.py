from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config import Settings
from app.creative_production.schemas import CreativePlatform
from app.publishing.controls import ensure_control
from app.publishing.orchestrator import PublishingOrchestrator
from app.publishing.publishers.base import PublisherError
from app.publishing.publishers.mock_publisher import MockPublishingProvider
from app.publishing.reconciliation import resolve_outcome
from app.publishing.registry import PublisherRegistry, build_publisher_registry
from app.publishing.repositories import (
    get_job,
    list_publications,
    save_account,
    save_control,
    save_job,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    PublicationJob,
    PublishingControl,
    PublishingPlatform,
    PublishingSchedule,
    PublishingStatus,
    SocialAccountConnection,
)
from tests.publishing_support import PublishingFactory


def _schedule(
    factory: PublishingFactory,
    settings: Settings,
    platform: CreativePlatform,
    *,
    account: SocialAccountConnection | None = None,
    with_assets: bool = False,
    metadata: dict[str, object] | None = None,
    scheduled_at: datetime | None = None,
) -> tuple[PublishingSchedule, PublicationJob]:
    package, assets = factory.package([platform], with_assets=with_assets)
    target = account or factory.account_for_creative(platform)
    snapshots = factory.approve(
        package,
        [platform],
        {platform: target},
        assets,
        {platform: metadata or {}},
    )
    return PublishingScheduler(settings).create_approved_schedule(
        factory.session,
        snapshots[0].snapshot_id,
        scheduled_at or datetime.now(UTC),
        "Asia/Tokyo",
        "schedule-reviewer",
    )


def _settings(**updates: Any) -> Settings:
    return Settings(_env_file=None, **updates)  # type: ignore[call-arg]


def _production_settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "publishing_enabled": True,
        "publishing_external_api_enabled": True,
        "publishing_dry_run": False,
        "publishing_retry_base_seconds": 1,
    }
    values.update(updates)
    return _settings(**values)


def _enable_production(factory: PublishingFactory) -> None:
    save_control(
        factory.session,
        PublishingControl(
            publishing_enabled=True,
            dry_run=False,
            updated_by="test-reviewer",
        ),
    )


def test_dry_run_builds_payload_without_external_publication(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _settings()
    ensure_control(publishing_factory.session, settings)
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)

    result = asyncio.run(
        PublishingOrchestrator(settings, build_publisher_registry(settings)).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.DRY_RUN_COMPLETED
    assert result.remote_post_id is None
    assert list_publications(publishing_factory.session) == []
    assert result.response_metadata["payload_hash"]


def test_live_mock_publish_stores_remote_post_id_and_is_idempotent(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    provider = MockPublishingProvider(PublishingPlatform.X)
    orchestrator = PublishingOrchestrator(settings, PublisherRegistry([provider]))

    first = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
    second = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))

    publications = list_publications(publishing_factory.session)
    assert first.status is PublishingStatus.PUBLISHED
    assert first.remote_post_id
    assert second.remote_post_id == first.remote_post_id
    assert provider.publish_calls == 1
    assert len(publications) == 1
    assert publications[0].remote_post_id == first.remote_post_id


def test_same_schedule_is_idempotent(publishing_factory: PublishingFactory) -> None:
    settings = _settings()
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    snapshot = publishing_factory.approve(
        package, [CreativePlatform.X], {CreativePlatform.X: account}
    )[0]
    scheduled_at = datetime.now(UTC) + timedelta(minutes=5)
    scheduler = PublishingScheduler(settings)

    first_schedule, first_job = scheduler.create_approved_schedule(
        publishing_factory.session,
        snapshot.snapshot_id,
        scheduled_at,
        "Asia/Tokyo",
        "reviewer",
    )
    second_schedule, second_job = scheduler.create_approved_schedule(
        publishing_factory.session,
        snapshot.snapshot_id,
        scheduled_at,
        "Asia/Tokyo",
        "reviewer",
    )

    assert second_schedule.schedule_id == first_schedule.schedule_id
    assert second_job.job_id == first_job.job_id


def test_direct_dispatch_cannot_bypass_future_schedule(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    _, job = _schedule(
        publishing_factory,
        settings,
        CreativePlatform.X,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    provider = MockPublishingProvider(PublishingPlatform.X)

    result = asyncio.run(
        PublishingOrchestrator(settings, PublisherRegistry([provider])).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.QUEUED
    assert result.response_metadata["not_due"] is True
    assert provider.publish_calls == 0


def test_duplicate_content_is_blocked_across_different_packages(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    _, first_job = _schedule(publishing_factory, settings, CreativePlatform.X, account=account)
    _, second_job = _schedule(
        publishing_factory,
        settings,
        CreativePlatform.X,
        account=account,
        scheduled_at=datetime.now(UTC),
    )
    provider = MockPublishingProvider(PublishingPlatform.X)
    orchestrator = PublishingOrchestrator(settings, PublisherRegistry([provider]))

    first = asyncio.run(orchestrator.dispatch(publishing_factory.session, first_job.job_id))
    second = asyncio.run(orchestrator.dispatch(publishing_factory.session, second_job.job_id))

    assert first.status is PublishingStatus.PUBLISHED
    assert second.status is PublishingStatus.BLOCKED
    assert "同一Content Hash" in second.error
    assert provider.publish_calls == 1


def test_retryable_failure_uses_backoff_then_succeeds(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings(publishing_max_retries=2)
    _enable_production(publishing_factory)
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    provider = MockPublishingProvider(
        PublishingPlatform.X,
        outcomes=[
            PublisherError("temporary", code="TIMEOUT", retryable=True),
            PublishingStatus.PUBLISHED,
        ],
    )
    orchestrator = PublishingOrchestrator(settings, PublisherRegistry([provider]))

    first = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
    queued = get_job(publishing_factory.session, job.job_id)
    assert queued is not None
    assert first.status is PublishingStatus.QUEUED
    assert queued.next_attempt_at is not None
    save_job(
        publishing_factory.session,
        queued.model_copy(update={"next_attempt_at": datetime.now(UTC) - timedelta(seconds=1)}),
    )
    second = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))

    assert second.status is PublishingStatus.PUBLISHED
    assert provider.publish_calls == 2


def test_unknown_publish_outcome_is_quarantined_until_human_reconciliation(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    provider = MockPublishingProvider(
        PublishingPlatform.X,
        outcomes=[
            PublisherError(
                "response lost after send",
                code="X_TIMEOUT",
                retryable=False,
                outcome_unknown=True,
                request_id="request-unknown-1",
            )
        ],
    )
    orchestrator = PublishingOrchestrator(settings, PublisherRegistry([provider]))

    first = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
    replay = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
    saved = get_job(publishing_factory.session, job.job_id)

    assert saved is not None
    assert first.status is PublishingStatus.RECONCILIATION_REQUIRED
    assert replay.status is PublishingStatus.RECONCILIATION_REQUIRED
    assert first.retryable is False
    assert saved.next_attempt_at is None
    assert saved.request_id == "request-unknown-1"
    assert provider.publish_calls == 1

    reconciled, publication = resolve_outcome(
        publishing_factory.session,
        job.job_id,
        actor="operator-a",
        published=True,
        provider=provider.key,
        remote_post_id="remote-confirmed-1",
        remote_url="https://x.com/i/web/status/remote-confirmed-1",
    )

    assert reconciled.status is PublishingStatus.PUBLISHED
    assert publication is not None
    assert publication.remote_post_id == "remote-confirmed-1"
    with pytest.raises(ValueError, match="結果不明"):
        resolve_outcome(
            publishing_factory.session,
            job.job_id,
            actor="operator-a",
            published=False,
            provider=provider.key,
        )


def test_confirmed_not_published_can_be_requeued_explicitly(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    provider = MockPublishingProvider(
        PublishingPlatform.X,
        outcomes=[
            PublisherError(
                "ambiguous server response",
                code="X_UNAVAILABLE",
                retryable=False,
                outcome_unknown=True,
            ),
            PublishingStatus.PUBLISHED,
        ],
    )
    orchestrator = PublishingOrchestrator(settings, PublisherRegistry([provider]))
    first = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
    assert first.status is PublishingStatus.RECONCILIATION_REQUIRED

    requeued, publication = resolve_outcome(
        publishing_factory.session,
        job.job_id,
        actor="operator-b",
        published=False,
        provider=provider.key,
    )
    assert requeued.status is PublishingStatus.QUEUED
    assert publication is None

    second = asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))
    assert second.status is PublishingStatus.PUBLISHED
    assert provider.publish_calls == 2


def test_permission_failure_is_not_retried(publishing_factory: PublishingFactory) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    provider = MockPublishingProvider(
        PublishingPlatform.X,
        outcomes=[PublisherError("denied", code="PERMISSION_DENIED", retryable=False)],
    )

    result = asyncio.run(
        PublishingOrchestrator(settings, PublisherRegistry([provider])).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.FAILED
    assert result.retryable is False
    assert provider.publish_calls == 1


def test_cancelled_schedule_cannot_publish(publishing_factory: PublishingFactory) -> None:
    settings = _settings()
    schedule, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    PublishingScheduler(settings).cancel(
        publishing_factory.session, schedule.schedule_id, "reviewer"
    )

    with pytest.raises(ValueError, match="cancelled"):
        asyncio.run(
            PublishingOrchestrator(settings, build_publisher_registry(settings)).dispatch(
                publishing_factory.session, job.job_id
            )
        )


def test_paused_schedule_cannot_be_dispatched(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _settings()
    schedule, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    PublishingScheduler(settings).pause(publishing_factory.session, schedule.schedule_id)

    with pytest.raises(ValueError, match="waiting cannot be dispatched"):
        asyncio.run(
            PublishingOrchestrator(settings, build_publisher_registry(settings)).dispatch(
                publishing_factory.session, job.job_id
            )
        )


def test_global_pause_blocks_even_dry_run(publishing_factory: PublishingFactory) -> None:
    settings = _settings()
    save_control(
        publishing_factory.session,
        PublishingControl(globally_paused=True, dry_run=True, updated_by="operator"),
    )
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)

    result = asyncio.run(
        PublishingOrchestrator(settings, build_publisher_registry(settings)).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.BLOCKED
    assert "GLOBAL PUBLISHING PAUSE" in result.error


def test_expired_token_is_blocked_before_provider_call(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _settings()
    account = publishing_factory.account(PublishingPlatform.X)
    expired = account.model_copy(update={"token_expiry": datetime.now(UTC) - timedelta(minutes=1)})
    save_account(publishing_factory.session, expired)
    _, job = _schedule(
        publishing_factory,
        settings,
        CreativePlatform.X,
        account=expired,
    )
    provider = MockPublishingProvider(PublishingPlatform.X)

    result = asyncio.run(
        PublishingOrchestrator(settings, PublisherRegistry([provider])).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.BLOCKED
    assert "期限切れ" in result.error
    assert provider.publish_calls == 0


def test_closed_posting_window_becomes_missed_schedule(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    snapshot = publishing_factory.approve(
        package,
        [CreativePlatform.X],
        {CreativePlatform.X: account},
    )[0]
    now = datetime.now(UTC)
    _, job = PublishingScheduler(settings).create_approved_schedule(
        publishing_factory.session,
        snapshot.snapshot_id,
        now - timedelta(seconds=5),
        "Asia/Tokyo",
        "window-reviewer",
        earliest_publish_at=now - timedelta(seconds=30),
        latest_publish_at=now - timedelta(seconds=1),
    )
    provider = MockPublishingProvider(PublishingPlatform.X)

    result = asyncio.run(
        PublishingOrchestrator(settings, PublisherRegistry([provider])).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.MISSED_SCHEDULE
    assert result.error_code == "POSTING_WINDOW_CLOSED"
    assert provider.publish_calls == 0


def test_kill_switch_blocks_live_publish(publishing_factory: PublishingFactory) -> None:
    settings = _settings(
        publishing_enabled=False,
        publishing_external_api_enabled=False,
        publishing_dry_run=False,
    )
    save_control(
        publishing_factory.session,
        PublishingControl(publishing_enabled=True, dry_run=False, updated_by="operator"),
    )
    _, job = _schedule(publishing_factory, settings, CreativePlatform.X)
    provider = MockPublishingProvider(PublishingPlatform.X)

    result = asyncio.run(
        PublishingOrchestrator(settings, PublisherRegistry([provider])).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.BLOCKED
    assert "gateがOFF" in result.error
    assert provider.publish_calls == 0


def test_tiktok_unaudited_public_post_is_blocked_without_privacy_downgrade(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _settings()
    _, job = _schedule(
        publishing_factory,
        settings,
        CreativePlatform.TIKTOK,
        with_assets=True,
        metadata={"privacy_level": "PUBLIC_TO_EVERYONE"},
    )

    result = asyncio.run(
        PublishingOrchestrator(settings, build_publisher_registry(settings)).dispatch(
            publishing_factory.session, job.job_id
        )
    )

    assert result.status is PublishingStatus.BLOCKED
    assert "PUBLIC_POST_UNAVAILABLE" in result.error


def test_partial_campaign_failure_does_not_mark_all_failed(
    publishing_factory: PublishingFactory,
) -> None:
    settings = _production_settings()
    _enable_production(publishing_factory)
    platforms = [CreativePlatform.X, CreativePlatform.INSTAGRAM_FEED]
    package, assets = publishing_factory.package(platforms, with_assets=True)
    accounts = {
        platform: publishing_factory.account_for_creative(platform) for platform in platforms
    }
    snapshots = publishing_factory.approve(package, platforms, accounts, assets)
    scheduler = PublishingScheduler(settings)
    jobs = [
        scheduler.create_approved_schedule(
            publishing_factory.session,
            snapshot.snapshot_id,
            datetime.now(UTC),
            "Asia/Tokyo",
            "reviewer",
        )[1]
        for snapshot in snapshots
    ]
    registry = PublisherRegistry(
        [
            MockPublishingProvider(PublishingPlatform.X),
            MockPublishingProvider(
                PublishingPlatform.INSTAGRAM,
                outcomes=[PublisherError("permission", code="PERMISSION_DENIED", retryable=False)],
            ),
        ]
    )
    orchestrator = PublishingOrchestrator(settings, registry)
    for job in jobs:
        asyncio.run(orchestrator.dispatch(publishing_factory.session, job.job_id))

    assert (
        orchestrator.campaign_status(publishing_factory.session, package.campaign.campaign_id)
        is PublishingStatus.PARTIAL_FAILURE
    )
