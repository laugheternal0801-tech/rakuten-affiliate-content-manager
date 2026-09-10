from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.controls import effective_control
from app.publishing.preflight import PublishingPreflightValidator
from app.publishing.publishers.base import PublisherError
from app.publishing.reconciliation import mark_outcome_unknown, unknown_result
from app.publishing.registry import PublisherRegistry
from app.publishing.repositories import (
    get_account,
    get_job,
    get_publication,
    get_publication_by_key,
    get_schedule,
    get_snapshot,
    list_jobs,
    save_audit,
    save_job,
    save_publication,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    ExternalPublication,
    GeneratedMediaDisclosure,
    PreflightStatus,
    PublicationJob,
    PublishingStatus,
    PublishPayload,
    PublishResult,
)

SUCCESS_STATES = {
    PublishingStatus.SUBMITTED,
    PublishingStatus.PROCESSING,
    PublishingStatus.PUBLISHED,
    PublishingStatus.DRY_RUN_COMPLETED,
}


class PublishingOrchestrator:
    def __init__(self, settings: Settings, registry: PublisherRegistry) -> None:
        self._settings = settings
        self._registry = registry
        self._preflight = PublishingPreflightValidator(settings)
        self._scheduler = PublishingScheduler(settings)

    async def dispatch(self, session: Session, job_id: str) -> PublishResult:
        job = get_job(session, job_id)
        if job is None:
            raise LookupError(f"Publication Job {job_id} was not found.")
        if job.status is PublishingStatus.RECONCILIATION_REQUIRED:
            return unknown_result(job, self._registry.get(job.platform).key)
        if job.status in SUCCESS_STATES:
            existing = get_publication_by_key(session, job.idempotency_key)
            if existing:
                return self._existing_result(existing)
            if job.status is PublishingStatus.DRY_RUN_COMPLETED:
                return PublishResult(
                    platform=job.platform,
                    provider=self._registry.get(job.platform).key,
                    snapshot_id=job.snapshot_id,
                    target_account_id=job.target_account_id,
                    status=PublishingStatus.DRY_RUN_COMPLETED,
                    request_id=job.request_id or f"dry-{job.job_id}",
                    response_metadata={"idempotent_replay": True, "dry_run": True},
                )
        if job.status is not PublishingStatus.QUEUED:
            raise ValueError(f"Job status {job.status.value} cannot be dispatched.")
        now = datetime.now(UTC)
        schedule = get_schedule(session, job.schedule_id)
        if schedule is None:
            return self._block(session, job, "SCHEDULE_MISSING")
        if schedule.latest_publish_at and now > schedule.latest_publish_at.astimezone(UTC):
            missed = job.model_copy(
                update={
                    "status": PublishingStatus.MISSED_SCHEDULE,
                    "error_code": "POSTING_WINDOW_CLOSED",
                    "error": "Latest publishを過ぎたため投稿しません。再確認が必要です。",
                    "updated_at": now,
                }
            )
            save_job(session, missed)
            save_audit(
                session,
                self._audit(
                    missed,
                    AuditEventType.PUBLISH_FAILED,
                    "system",
                    error=missed.error,
                ),
            )
            return PublishResult(
                platform=missed.platform,
                provider=self._registry.get(missed.platform).key,
                snapshot_id=missed.snapshot_id,
                target_account_id=missed.target_account_id,
                status=PublishingStatus.MISSED_SCHEDULE,
                request_id=missed.job_id,
                error_code=missed.error_code,
                error=missed.error,
            )
        due_at = max(
            job.scheduled_at.astimezone(UTC),
            (job.next_attempt_at or job.scheduled_at).astimezone(UTC),
            (schedule.earliest_publish_at or job.scheduled_at).astimezone(UTC),
        )
        if due_at > now:
            return PublishResult(
                platform=job.platform,
                provider=self._registry.get(job.platform).key,
                snapshot_id=job.snapshot_id,
                target_account_id=job.target_account_id,
                status=PublishingStatus.QUEUED,
                request_id=job.job_id,
                response_metadata={"not_due": True, "due_at": due_at.isoformat()},
            )

        snapshot = get_snapshot(session, job.snapshot_id)
        account = get_account(session, job.target_account_id)
        if snapshot is None or account is None:
            return self._block(session, job, "SNAPSHOT_OR_ACCOUNT_MISSING")
        control = effective_control(session, self._settings)
        payload = PublishPayload(
            job_id=job.job_id,
            snapshot_id=snapshot.snapshot_id,
            campaign_id=snapshot.campaign_id,
            platform=snapshot.platform,
            target_account_id=snapshot.target_account_id,
            credential_reference=account.credential_reference,
            idempotency_key=job.idempotency_key,
            text=snapshot.text,
            caption=snapshot.caption,
            title=snapshot.title,
            description=snapshot.description,
            assets=snapshot.assets,
            hashtags=snapshot.hashtags,
            links=snapshot.links,
            platform_metadata={
                **snapshot.metadata,
                "target_external_account_id": account.account_id,
                "account_metadata": account.metadata,
            },
            disclosure=GeneratedMediaDisclosure(
                ai_generated=True,
                providers=_metadata_list(snapshot.metadata, "providers"),
                models=_metadata_list(snapshot.metadata, "models"),
            ),
            dry_run=control.dry_run,
        )
        provider = self._registry.get(job.platform)
        preflight_job = job.model_copy(
            update={"status": PublishingStatus.PREFLIGHT, "updated_at": datetime.now(UTC)}
        )
        save_job(session, preflight_job)
        preflight = await self._preflight.validate(
            session, preflight_job, provider, control, payload
        )
        preflight_job = preflight_job.model_copy(
            update={"preflight": preflight.model_dump(mode="json")}
        )
        if preflight.status is PreflightStatus.BLOCKED:
            blocked = preflight_job.model_copy(
                update={
                    "status": PublishingStatus.BLOCKED,
                    "error_code": "PREFLIGHT_BLOCKED",
                    "error": " / ".join(
                        check.message
                        for check in preflight.checks
                        if check.result.value in {"fail", "manual_review_required"}
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            save_job(session, blocked)
            save_audit(
                session,
                self._audit(
                    blocked,
                    AuditEventType.PREFLIGHT_BLOCKED,
                    "system",
                    error=blocked.error,
                ),
            )
            return PublishResult(
                platform=blocked.platform,
                provider=provider.key,
                snapshot_id=blocked.snapshot_id,
                target_account_id=blocked.target_account_id,
                status=PublishingStatus.BLOCKED,
                request_id=blocked.job_id,
                error_code=blocked.error_code,
                error=blocked.error,
            )

        started = time.perf_counter()
        publishing = preflight_job.model_copy(
            update={
                "status": PublishingStatus.PUBLISHING,
                "attempt_count": preflight_job.attempt_count + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        save_job(session, publishing)
        save_audit(
            session,
            self._audit(publishing, AuditEventType.PUBLISH_REQUESTED, "system"),
        )
        # A live external side effect must never happen while the intent exists only
        # in an uncommitted transaction. If the process stops after this point, the
        # durable PUBLISHING state is quarantined instead of being replayed.
        if not payload.dry_run:
            session.commit()
        try:
            result = await provider.publish(payload)
        except PublisherError as exc:
            return self._handle_failure(session, publishing, exc, started)
        except Exception as exc:  # Defensive boundary around external providers.
            wrapped = PublisherError(
                f"Unexpected provider error: {type(exc).__name__}",
                code="UNEXPECTED_PROVIDER_ERROR",
                retryable=False,
                outcome_unknown=not payload.dry_run,
            )
            return self._handle_failure(session, publishing, wrapped, started)

        if result.status not in SUCCESS_STATES:
            error = PublisherError(
                result.error or f"Unexpected result status: {result.status.value}",
                code=result.error_code or "UNEXPECTED_RESULT",
                retryable=result.retryable,
                outcome_unknown=(
                    not payload.dry_run
                    and result.status is PublishingStatus.RECONCILIATION_REQUIRED
                ),
            )
            return self._handle_failure(session, publishing, error, started)
        completed = publishing.model_copy(
            update={
                "status": result.status,
                "request_id": result.request_id,
                "next_attempt_at": None,
                "error_code": "",
                "error": "",
                "updated_at": datetime.now(UTC),
            }
        )
        save_job(session, completed)
        if result.status is not PublishingStatus.DRY_RUN_COMPLETED:
            if not result.remote_post_id:
                raise RuntimeError("Live publish result lost remote_post_id.")
            existing_publication = get_publication_by_key(session, completed.idempotency_key)
            publication_values = {
                "job_id": completed.job_id,
                "campaign_id": completed.campaign_id,
                "snapshot_id": completed.snapshot_id,
                "platform": completed.platform,
                "target_account_id": completed.target_account_id,
                "provider": result.provider,
                "idempotency_key": completed.idempotency_key,
                "remote_post_id": result.remote_post_id,
                "remote_url": result.remote_url,
                "status": result.status,
                "request_id": result.request_id,
                "published_at": result.published_at,
                "verified_at": None,
                "response_metadata": result.response_metadata,
            }
            if existing_publication:
                publication = existing_publication.model_copy(update=publication_values)
            else:
                publication = ExternalPublication(
                    job_id=completed.job_id,
                    campaign_id=completed.campaign_id,
                    snapshot_id=completed.snapshot_id,
                    platform=completed.platform,
                    target_account_id=completed.target_account_id,
                    provider=result.provider,
                    idempotency_key=completed.idempotency_key,
                    remote_post_id=result.remote_post_id,
                    remote_url=result.remote_url,
                    status=result.status,
                    request_id=result.request_id,
                    published_at=result.published_at,
                    response_metadata=result.response_metadata,
                )
            save_publication(session, publication)
            # Persist the provider identity before audit/notification work. A later
            # failure can then replay the saved result instead of posting again.
            session.commit()
        event = (
            AuditEventType.DRY_RUN_COMPLETED
            if result.status is PublishingStatus.DRY_RUN_COMPLETED
            else AuditEventType.PUBLISH_SUCCESS
        )
        save_audit(
            session,
            self._audit(
                completed,
                event,
                "system",
                duration_ms=int((time.perf_counter() - started) * 1000),
                metadata={
                    "remote_post_id": result.remote_post_id,
                    "dry_run": result.status is PublishingStatus.DRY_RUN_COMPLETED,
                },
            ),
        )
        return result

    async def process_due(self, session: Session) -> dict[str, PublishResult]:
        results: dict[str, PublishResult] = {}
        for job in self._scheduler.due_jobs(session):
            results[job.job_id] = await self.dispatch(session, job.job_id)
        return results

    async def poll_status(self, session: Session, publication_id: str) -> ExternalPublication:
        publication = get_publication(session, publication_id)
        if publication is None:
            raise LookupError(f"Publication {publication_id} was not found.")
        provider = self._registry.get(publication.platform)
        account = get_account(session, publication.target_account_id)
        current = PublishResult(
            platform=publication.platform,
            provider=publication.provider,
            snapshot_id=publication.snapshot_id,
            target_account_id=publication.target_account_id,
            remote_post_id=publication.remote_post_id,
            remote_url=publication.remote_url,
            published_at=publication.published_at,
            status=publication.status,
            request_id=publication.request_id,
            response_metadata=publication.response_metadata,
            credential_reference=account.credential_reference if account else "",
        )
        result = await provider.get_publish_status(current)
        updated = publication.model_copy(
            update={
                "status": result.status,
                "remote_url": result.remote_url or publication.remote_url,
                "published_at": result.published_at or publication.published_at,
                "response_metadata": {
                    **publication.response_metadata,
                    **result.response_metadata,
                },
            }
        )
        save_publication(session, updated)
        return updated

    @staticmethod
    def campaign_status(session: Session, campaign_id: str) -> PublishingStatus:
        jobs = list_jobs(session, campaign_id)
        if not jobs:
            return PublishingStatus.DRAFT
        statuses = {job.status for job in jobs}
        success = statuses & SUCCESS_STATES
        outcome_unknown = PublishingStatus.RECONCILIATION_REQUIRED in statuses
        failures = statuses & {
            PublishingStatus.FAILED,
            PublishingStatus.BLOCKED,
            PublishingStatus.MISSED_SCHEDULE,
        }
        if success and (failures or outcome_unknown):
            return PublishingStatus.PARTIAL_FAILURE
        if outcome_unknown:
            return PublishingStatus.RECONCILIATION_REQUIRED
        if statuses <= SUCCESS_STATES:
            return (
                PublishingStatus.PUBLISHED
                if PublishingStatus.PUBLISHED in statuses
                else PublishingStatus.DRY_RUN_COMPLETED
            )
        if failures and not success:
            return PublishingStatus.FAILED
        return PublishingStatus.SCHEDULED

    def _handle_failure(
        self,
        session: Session,
        job: PublicationJob,
        error: PublisherError,
        started: float,
    ) -> PublishResult:
        if error.outcome_unknown:
            unknown, _ = mark_outcome_unknown(
                session,
                job,
                provider=self._registry.get(job.platform).key,
                error_code=error.code,
                error=str(error),
                request_id=error.request_id,
                remote_post_id=error.remote_post_id,
                metadata=error.metadata,
            )
            session.commit()
            return unknown_result(unknown, self._registry.get(job.platform).key)
        now = datetime.now(UTC)
        delay = self._settings.publishing_retry_base_seconds * (2 ** max(0, job.attempt_count - 1))
        next_attempt = now + timedelta(seconds=delay)
        latest_allowed = job.scheduled_at.astimezone(UTC) + timedelta(
            minutes=self._settings.publishing_max_schedule_lateness_minutes
        )
        retry = error.retryable and job.attempt_count < job.max_attempts
        if retry and next_attempt <= latest_allowed:
            status = PublishingStatus.QUEUED
        elif error.retryable and next_attempt > latest_allowed:
            status = PublishingStatus.MISSED_SCHEDULE
            retry = False
        else:
            status = PublishingStatus.FAILED
        failed = job.model_copy(
            update={
                "status": status,
                "next_attempt_at": next_attempt if retry else None,
                "error_code": error.code,
                "error": str(error),
                "updated_at": now,
            }
        )
        save_job(session, failed)
        save_audit(
            session,
            self._audit(
                failed,
                AuditEventType.PUBLISH_FAILED,
                "system",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(error),
                metadata={
                    "retryable": error.retryable,
                    "next_attempt_at": next_attempt.isoformat() if retry else None,
                },
            ),
        )
        return PublishResult(
            platform=failed.platform,
            provider=self._registry.get(failed.platform).key,
            snapshot_id=failed.snapshot_id,
            target_account_id=failed.target_account_id,
            status=failed.status,
            request_id=failed.job_id,
            error_code=failed.error_code,
            error=failed.error,
            retryable=retry,
        )

    def _block(self, session: Session, job: PublicationJob, code: str) -> PublishResult:
        blocked = job.model_copy(
            update={
                "status": PublishingStatus.BLOCKED,
                "error_code": code,
                "error": "Approved SnapshotまたはTarget Accountがありません。",
                "updated_at": datetime.now(UTC),
            }
        )
        save_job(session, blocked)
        return PublishResult(
            platform=blocked.platform,
            provider=self._registry.get(blocked.platform).key,
            snapshot_id=blocked.snapshot_id,
            target_account_id=blocked.target_account_id,
            status=PublishingStatus.BLOCKED,
            request_id=blocked.job_id,
            error_code=code,
            error=blocked.error,
        )

    @staticmethod
    def _existing_result(publication: ExternalPublication) -> PublishResult:
        return PublishResult(
            platform=publication.platform,
            provider=publication.provider,
            snapshot_id=publication.snapshot_id,
            target_account_id=publication.target_account_id,
            remote_post_id=publication.remote_post_id,
            remote_url=publication.remote_url,
            published_at=publication.published_at,
            status=publication.status,
            request_id=publication.request_id,
            response_metadata={"idempotent_replay": True},
        )

    @staticmethod
    def _audit(
        job: PublicationJob,
        event: AuditEventType,
        actor: str,
        *,
        duration_ms: int = 0,
        error: str = "",
        metadata: dict[str, object] | None = None,
    ) -> AuditLog:
        return AuditLog(
            event_type=event,
            actor=actor,
            campaign_id=job.campaign_id,
            snapshot_id=job.snapshot_id,
            platform=job.platform,
            target_account_id=job.target_account_id,
            provider="",
            request_id=job.request_id,
            status=job.status.value,
            retry_count=job.attempt_count,
            duration_ms=duration_ms,
            error=error,
            metadata=metadata or {},
        )


def _metadata_list(metadata: dict[str, object], key: str) -> list[str]:
    value = metadata.get(key, [])
    if isinstance(value, list):
        return [str(item) for item in value]
    return []
