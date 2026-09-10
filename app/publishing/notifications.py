from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.hashing import canonical_hash
from app.publishing.repositories import (
    get_publication_by_key,
    list_notifications,
    save_audit,
    save_notification,
    update_notification,
)
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    NotificationEvent,
    NotificationSeverity,
    NotificationStatus,
    PublicationJob,
    PublishingStatus,
    PublishResult,
)


class NotificationProvider(Protocol):
    """Future email/Slack/etc. adapter. Implementations must not receive secrets in events."""

    key: str

    async def send(self, event: NotificationEvent) -> str: ...


class NotificationOutboxService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def enqueue_job_result(
        self,
        session: Session,
        job: PublicationJob,
        result: PublishResult,
    ) -> NotificationEvent:
        severity = _severity(result.status)
        publication = get_publication_by_key(session, job.idempotency_key)
        dedupe_key = canonical_hash(
            [
                "publication_job_result",
                job.job_id,
                result.status.value,
                job.attempt_count,
                result.error_code,
            ]
        )
        event = NotificationEvent(
            dedupe_key=dedupe_key,
            event_type=f"publication_{result.status.value}",
            severity=severity,
            title=f"{job.platform.value}: {result.status.value}",
            message=(
                result.error
                or (
                    "Dry Run payload生成が完了しました。外部送信はありません。"
                    if result.status is PublishingStatus.DRY_RUN_COMPLETED
                    else "Publishing Jobの状態が更新されました。"
                )
            ),
            campaign_id=job.campaign_id,
            job_id=job.job_id,
            publication_id=publication.publication_id if publication else "",
            payload={
                "platform": job.platform.value,
                "status": result.status.value,
                "request_id": result.request_id,
                "remote_post_id": result.remote_post_id,
                "error_code": result.error_code,
            },
        )
        saved = save_notification(session, event)
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.NOTIFICATION_ENQUEUED,
                actor="system",
                campaign_id=job.campaign_id,
                publication_id=saved.publication_id,
                platform=job.platform,
                target_account_id=job.target_account_id,
                request_id=result.request_id,
                status=saved.status.value,
                metadata={
                    "notification_id": saved.notification_id,
                    "event_type": saved.event_type,
                    "severity": saved.severity.value,
                },
            ),
        )
        return saved

    def enqueue_reapproval(
        self,
        session: Session,
        *,
        package_id: str,
        campaign_id: str,
        reason: str,
    ) -> NotificationEvent:
        event = NotificationEvent(
            dedupe_key=canonical_hash(["reapproval_required", package_id, reason]),
            event_type="reapproval_required",
            severity=NotificationSeverity.WARNING,
            title="Contentの再承認が必要です",
            message=reason,
            campaign_id=campaign_id,
            payload={"content_package_id": package_id},
        )
        return save_notification(session, event)

    async def deliver_pending(
        self,
        session: Session,
        provider: NotificationProvider,
        *,
        limit: int = 20,
    ) -> list[NotificationEvent]:
        now = datetime.now(UTC)
        delivered: list[NotificationEvent] = []
        for event in list_notifications(
            session,
            NotificationStatus.PENDING,
            limit=limit,
        ):
            if event.target != provider.key:
                continue
            if event.next_attempt_at and event.next_attempt_at > now:
                continue
            try:
                provider_message_id = await provider.send(event)
            except Exception as exc:  # Defensive boundary around optional adapters.
                attempts = event.attempt_count + 1
                terminal = attempts >= self._settings.publishing_notification_max_attempts
                updated = event.model_copy(
                    update={
                        "status": (
                            NotificationStatus.FAILED if terminal else NotificationStatus.PENDING
                        ),
                        "attempt_count": attempts,
                        "next_attempt_at": (
                            None
                            if terminal
                            else now
                            + timedelta(
                                seconds=self._settings.publishing_retry_base_seconds
                                * (2 ** max(0, attempts - 1))
                            )
                        ),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                updated = event.model_copy(
                    update={
                        "status": NotificationStatus.DELIVERED,
                        "attempt_count": event.attempt_count + 1,
                        "delivered_at": now,
                        "next_attempt_at": None,
                        "error": "",
                        "payload": {
                            **event.payload,
                            "provider_message_id": provider_message_id,
                        },
                    }
                )
                delivered.append(updated)
            update_notification(session, updated)
        return delivered


def _severity(status: PublishingStatus) -> NotificationSeverity:
    if status is PublishingStatus.RECONCILIATION_REQUIRED:
        return NotificationSeverity.CRITICAL
    if status in {PublishingStatus.FAILED, PublishingStatus.MISSED_SCHEDULE}:
        return NotificationSeverity.ERROR
    if status in {PublishingStatus.BLOCKED, PublishingStatus.PARTIAL_FAILURE}:
        return NotificationSeverity.WARNING
    return NotificationSeverity.INFO
