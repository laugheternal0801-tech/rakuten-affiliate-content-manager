from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.creative_production.schemas import CreativePlatform
from app.models import Base
from app.publishing.controls import ensure_control
from app.publishing.notifications import NotificationOutboxService
from app.publishing.registry import build_publisher_registry
from app.publishing.repositories import (
    acquire_job_lease,
    get_job,
    get_worker_heartbeat,
    list_notifications,
    list_worker_leases,
    renew_job_lease,
    save_control,
    save_job,
    save_notification,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    NotificationEvent,
    NotificationStatus,
    PublishingControl,
    PublishingStatus,
    WorkerState,
)
from app.publishing.worker import PublishingWorker
from tests.publishing_support import PublishingFactory


def _settings(**updates: Any) -> Settings:
    return Settings(_env_file=None, **updates)  # type: ignore[call-arg]


def _database(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _due_job(factory: PublishingFactory, settings: Settings) -> str:
    package, _ = factory.package([CreativePlatform.X])
    account = factory.account_for_creative(CreativePlatform.X)
    snapshot = factory.approve(
        package,
        [CreativePlatform.X],
        {CreativePlatform.X: account},
    )[0]
    _, job = PublishingScheduler(settings).create_approved_schedule(
        factory.session,
        snapshot.snapshot_id,
        datetime.now(UTC),
        "Asia/Tokyo",
        "worker-test",
    )
    return job.job_id


def test_worker_processes_due_dry_run_with_lease_and_notification(tmp_path: Path) -> None:
    settings = _settings()
    sessions = _database(tmp_path)
    with sessions() as session:
        factory = PublishingFactory(session, tmp_path)
        ensure_control(session, settings)
        job_id = _due_job(factory, settings)
        session.commit()

    result = asyncio.run(
        PublishingWorker(
            settings,
            build_publisher_registry(settings),
            sessions,
        ).run_once("worker-a")
    )

    with sessions() as session:
        job = get_job(session, job_id)
        heartbeat = get_worker_heartbeat(session, "worker-a")
        notifications = list_notifications(session)
        assert job is not None
        assert heartbeat is not None
        assert job.status is PublishingStatus.DRY_RUN_COMPLETED
        assert heartbeat.state is WorkerState.IDLE
        assert heartbeat.processed_count == 1
        assert list_worker_leases(session) == []
        assert notifications[0].status is NotificationStatus.PENDING
        assert notifications[0].event_type == "publication_dry_run_completed"
    assert result.claimed_job_ids == [job_id]


def test_worker_global_pause_leaves_due_job_queued(tmp_path: Path) -> None:
    settings = _settings()
    sessions = _database(tmp_path)
    with sessions() as session:
        factory = PublishingFactory(session, tmp_path)
        job_id = _due_job(factory, settings)
        save_control(
            session,
            PublishingControl(
                globally_paused=True,
                dry_run=True,
                updated_by="operator",
            ),
        )
        session.commit()

    result = asyncio.run(
        PublishingWorker(
            settings,
            build_publisher_registry(settings),
            sessions,
        ).run_once("worker-paused")
    )

    with sessions() as session:
        job = get_job(session, job_id)
        heartbeat = get_worker_heartbeat(session, "worker-paused")
        assert job is not None
        assert heartbeat is not None
        assert job.status is PublishingStatus.QUEUED
        assert heartbeat.state is WorkerState.PAUSED
        assert list_notifications(session) == []
    assert result.skipped_reason == "GLOBAL PUBLISHING PAUSE"


def test_expired_worker_lease_can_be_reclaimed(tmp_path: Path) -> None:
    settings = _settings()
    sessions = _database(tmp_path)
    with sessions() as session:
        factory = PublishingFactory(session, tmp_path)
        job_id = _due_job(factory, settings)
        session.commit()
    now = datetime.now(UTC)
    with sessions() as session:
        assert acquire_job_lease(session, job_id, "worker-a", 30, now=now) is True
        session.commit()
    with sessions() as session:
        assert acquire_job_lease(session, job_id, "worker-b", 30, now=now) is False
        session.commit()
    with sessions() as session:
        assert (
            renew_job_lease(
                session,
                job_id,
                "worker-a",
                30,
                now=now + timedelta(seconds=20),
            )
            is True
        )
        session.commit()
    with sessions() as session:
        assert (
            acquire_job_lease(
                session,
                job_id,
                "worker-b",
                30,
                now=now + timedelta(seconds=31),
            )
            is False
        )
        session.commit()
    with sessions() as session:
        assert (
            acquire_job_lease(
                session,
                job_id,
                "worker-b",
                30,
                now=now + timedelta(seconds=51),
            )
            is True
        )


def test_stale_publishing_job_is_quarantined_without_resend(tmp_path: Path) -> None:
    settings = _settings(
        publishing_worker_lease_seconds=10,
        publishing_worker_stale_seconds=10,
    )
    sessions = _database(tmp_path)
    with sessions() as session:
        factory = PublishingFactory(session, tmp_path)
        ensure_control(session, settings)
        job_id = _due_job(factory, settings)
        job = get_job(session, job_id)
        assert job is not None
        save_job(
            session,
            job.model_copy(
                update={
                    "status": PublishingStatus.PUBLISHING,
                    "attempt_count": 1,
                    "updated_at": datetime.now(UTC) - timedelta(minutes=5),
                }
            ),
        )
        session.commit()

    result = asyncio.run(
        PublishingWorker(
            settings,
            build_publisher_registry(settings),
            sessions,
        ).run_once("worker-recovery")
    )

    with sessions() as session:
        recovered = get_job(session, job_id)
        notifications = list_notifications(session)
        assert recovered is not None
        assert recovered.status is PublishingStatus.RECONCILIATION_REQUIRED
        assert recovered.next_attempt_at is None
        assert recovered.error_code == "STALE_PUBLISHING_ATTEMPT"
        assert notifications[0].event_type == "publication_reconciliation_required"
    assert result.claimed_job_ids == []


class _MemoryNotificationProvider:
    key = "memory"

    async def send(self, event: NotificationEvent) -> str:
        return f"message-{event.notification_id}"


def test_notification_delivery_adapter_boundary(tmp_path: Path) -> None:
    settings = _settings()
    sessions = _database(tmp_path)
    with sessions() as session:
        event = NotificationEvent(
            dedupe_key="a" * 64,
            event_type="test",
            title="Test",
            message="Safe event",
            target="memory",
        )
        save_notification(session, event)
        delivered = asyncio.run(
            NotificationOutboxService(settings).deliver_pending(
                session,
                _MemoryNotificationProvider(),
            )
        )
        session.commit()

    assert delivered[0].status is NotificationStatus.DELIVERED
    assert delivered[0].payload["provider_message_id"].startswith("message-")
