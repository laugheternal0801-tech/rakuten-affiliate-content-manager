from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.controls import effective_control
from app.publishing.notifications import NotificationOutboxService
from app.publishing.orchestrator import PublishingOrchestrator
from app.publishing.reconciliation import (
    mark_outcome_unknown,
    recover_stale_publishing_jobs,
    unknown_result,
)
from app.publishing.registry import PublisherRegistry
from app.publishing.repositories import (
    acquire_job_lease,
    get_job,
    get_worker_heartbeat,
    release_job_lease,
    renew_job_lease,
    save_worker_heartbeat,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    PublishingStatus,
    WorkerHeartbeat,
    WorkerRunResult,
    WorkerState,
)

SessionFactory = Callable[[], Session]


class PublishingWorker:
    """DB-backed worker boundary that can later be hosted by a service or queue runner."""

    def __init__(
        self,
        settings: Settings,
        registry: PublisherRegistry,
        session_factory: SessionFactory,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._registry = registry
        self._scheduler = PublishingScheduler(settings)
        self._orchestrator = PublishingOrchestrator(settings, registry)
        self._notifications = NotificationOutboxService(settings)

    async def run_once(self, worker_id: str) -> WorkerRunResult:
        started_at = datetime.now(UTC)
        heartbeat = self._heartbeat(worker_id, WorkerState.STARTING)
        with self._session_factory() as session:
            save_worker_heartbeat(session, heartbeat)
            recovered = recover_stale_publishing_jobs(
                session,
                stale_after_seconds=max(
                    self._settings.publishing_worker_lease_seconds,
                    self._settings.publishing_worker_stale_seconds,
                ),
            )
            for recovered_job in recovered:
                provider_key = self._registry.get(recovered_job.platform).key
                self._notifications.enqueue_job_result(
                    session,
                    recovered_job,
                    unknown_result(recovered_job, provider_key),
                )
            control = effective_control(session, self._settings)
            if control.globally_paused:
                paused = heartbeat.model_copy(
                    update={
                        "state": WorkerState.PAUSED,
                        "last_seen_at": datetime.now(UTC),
                        "message": "GLOBAL PUBLISHING PAUSE",
                    }
                )
                save_worker_heartbeat(session, paused)
                session.commit()
                return WorkerRunResult(
                    worker_id=worker_id,
                    skipped_reason="GLOBAL PUBLISHING PAUSE",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
            due_job_ids = [
                job.job_id
                for job in self._scheduler.due_jobs(session)[
                    : self._settings.publishing_worker_batch_size
                ]
            ]
            session.commit()

        claimed: list[str] = []
        results: dict[str, str] = {}
        processed_delta = 0
        failed_delta = 0
        for job_id in due_job_ids:
            if not self._claim(job_id, worker_id):
                continue
            claimed.append(job_id)
            lease_stop = asyncio.Event()
            lease_maintenance = asyncio.create_task(
                self._maintain_lease(job_id, worker_id, lease_stop)
            )
            try:
                with self._session_factory() as session:
                    running = self._heartbeat(
                        worker_id,
                        WorkerState.RUNNING,
                        current_job_id=job_id,
                    )
                    save_worker_heartbeat(session, running)
                    result = await self._orchestrator.dispatch(session, job_id)
                    updated_job = get_job(session, job_id)
                    if updated_job is None:
                        raise LookupError(f"Publication Job {job_id} disappeared.")
                    self._notifications.enqueue_job_result(session, updated_job, result)
                    session.commit()
                    results[job_id] = result.status.value
                    processed_delta += 1
                    if result.error:
                        failed_delta += 1
            except Exception as exc:  # Defensive boundary for a single leased job.
                results[job_id] = f"worker_error:{type(exc).__name__}"
                failed_delta += 1
                # If the live boundary was durably entered but no terminal result
                # was saved, quarantine the attempt. It must never fall back to an
                # automatic retry after an unknown external side effect.
                with self._session_factory() as recovery_session:
                    persisted = get_job(recovery_session, job_id)
                    if persisted and persisted.status is PublishingStatus.PUBLISHING:
                        provider_key = self._registry.get(persisted.platform).key
                        unknown, _ = mark_outcome_unknown(
                            recovery_session,
                            persisted,
                            provider=provider_key,
                            error_code="WORKER_INTERRUPTED_AFTER_PUBLISH_START",
                            error=(
                                "投稿開始後にWorker処理が中断されました。外部側を確認するまで"
                                "自動再送しません。"
                            ),
                            metadata={"exception_type": type(exc).__name__},
                        )
                        result = unknown_result(unknown, provider_key)
                        self._notifications.enqueue_job_result(
                            recovery_session,
                            unknown,
                            result,
                        )
                        recovery_session.commit()
                        results[job_id] = result.status.value
            finally:
                lease_stop.set()
                await lease_maintenance
                with self._session_factory() as session:
                    release_job_lease(session, job_id, worker_id)
                    session.commit()

        with self._session_factory() as session:
            previous = get_worker_heartbeat(session, worker_id) or heartbeat
            idle = previous.model_copy(
                update={
                    "state": WorkerState.IDLE,
                    "last_seen_at": datetime.now(UTC),
                    "processed_count": previous.processed_count + processed_delta,
                    "failed_count": previous.failed_count + failed_delta,
                    "current_job_id": "",
                    "message": (f"claimed={len(claimed)} processed={processed_delta}"),
                }
            )
            save_worker_heartbeat(session, idle)
            session.commit()
        return WorkerRunResult(
            worker_id=worker_id,
            claimed_job_ids=claimed,
            results=results,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

    async def run_forever(self, worker_id: str, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once(worker_id)
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._settings.publishing_worker_poll_seconds,
                )
            except TimeoutError:
                continue
        with self._session_factory() as session:
            stopped = self._heartbeat(
                worker_id,
                WorkerState.STOPPED,
                message="Worker stop requested",
            )
            save_worker_heartbeat(session, stopped)
            session.commit()

    def _claim(self, job_id: str, worker_id: str) -> bool:
        with self._session_factory() as session:
            claimed = acquire_job_lease(
                session,
                job_id,
                worker_id,
                self._settings.publishing_worker_lease_seconds,
            )
            session.commit()
            return claimed

    async def _maintain_lease(
        self,
        job_id: str,
        worker_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        interval = max(
            1,
            min(30, self._settings.publishing_worker_lease_seconds // 3),
        )
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                try:
                    with self._session_factory() as session:
                        renewed = renew_job_lease(
                            session,
                            job_id,
                            worker_id,
                            self._settings.publishing_worker_lease_seconds,
                        )
                        if renewed:
                            previous = get_worker_heartbeat(session, worker_id)
                            if previous is not None:
                                save_worker_heartbeat(
                                    session,
                                    previous.model_copy(
                                        update={
                                            "state": WorkerState.RUNNING,
                                            "last_seen_at": datetime.now(UTC),
                                            "current_job_id": job_id,
                                            "message": "lease renewed",
                                        }
                                    ),
                                )
                        session.commit()
                    if not renewed:
                        return
                except Exception:  # A failed renewal is visible as an expiring lease.
                    return

    def _heartbeat(
        self,
        worker_id: str,
        state: WorkerState,
        *,
        current_job_id: str = "",
        message: str = "",
    ) -> WorkerHeartbeat:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            previous = get_worker_heartbeat(session, worker_id)
        return WorkerHeartbeat(
            worker_id=worker_id,
            state=state,
            started_at=previous.started_at if previous else now,
            last_seen_at=now,
            processed_count=previous.processed_count if previous else 0,
            failed_count=previous.failed_count if previous else 0,
            current_job_id=current_job_id,
            message=message,
        )
