from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.publishing.repositories import (
    get_job,
    get_publication_by_key,
    list_jobs,
    list_worker_leases,
    save_audit,
    save_job,
    save_publication,
)
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    ExternalPublication,
    PublicationJob,
    PublishingStatus,
    PublishResult,
)

RECONCILIABLE_STATUSES = {
    PublishingStatus.PUBLISHING,
    PublishingStatus.RECONCILIATION_REQUIRED,
}


def mark_outcome_unknown(
    session: Session,
    job: PublicationJob,
    *,
    provider: str,
    error_code: str,
    error: str,
    request_id: str = "",
    remote_post_id: str = "",
    metadata: dict[str, Any] | None = None,
    actor: str = "system",
    now: datetime | None = None,
) -> tuple[PublicationJob, ExternalPublication | None]:
    """Quarantine an ambiguous external side effect so it cannot be retried automatically."""

    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    safe_metadata = dict(metadata or {})
    reconciliation = {
        **job.reconciliation,
        "outcome": "unknown",
        "observed_at": observed_at.isoformat(),
        "error_code": error_code,
        "request_id": request_id or job.request_id,
        "remote_post_id": remote_post_id,
        "details": safe_metadata,
    }
    unknown = job.model_copy(
        update={
            "status": PublishingStatus.RECONCILIATION_REQUIRED,
            "next_attempt_at": None,
            "request_id": request_id or job.request_id,
            "error_code": error_code,
            "error": error,
            "reconciliation": reconciliation,
            "updated_at": observed_at,
        }
    )
    save_job(session, unknown)

    publication: ExternalPublication | None = None
    if remote_post_id:
        existing = get_publication_by_key(session, unknown.idempotency_key)
        publication_values = {
            "job_id": unknown.job_id,
            "campaign_id": unknown.campaign_id,
            "snapshot_id": unknown.snapshot_id,
            "platform": unknown.platform,
            "target_account_id": unknown.target_account_id,
            "provider": provider,
            "idempotency_key": unknown.idempotency_key,
            "remote_post_id": remote_post_id,
            "status": PublishingStatus.RECONCILIATION_REQUIRED,
            "request_id": request_id or unknown.job_id,
            "response_metadata": {
                **(existing.response_metadata if existing else {}),
                **safe_metadata,
                "outcome_unknown": True,
            },
        }
        if existing:
            publication = existing.model_copy(update=publication_values)
        else:
            publication = ExternalPublication(
                job_id=unknown.job_id,
                campaign_id=unknown.campaign_id,
                snapshot_id=unknown.snapshot_id,
                platform=unknown.platform,
                target_account_id=unknown.target_account_id,
                provider=provider,
                idempotency_key=unknown.idempotency_key,
                remote_post_id=remote_post_id,
                status=PublishingStatus.RECONCILIATION_REQUIRED,
                request_id=request_id or unknown.job_id,
                response_metadata={
                    **safe_metadata,
                    "outcome_unknown": True,
                },
            )
        save_publication(session, publication)

    save_audit(
        session,
        AuditLog(
            event_type=AuditEventType.PUBLISH_OUTCOME_UNKNOWN,
            actor=actor.strip() or "system",
            campaign_id=unknown.campaign_id,
            snapshot_id=unknown.snapshot_id,
            publication_id=publication.publication_id if publication else "",
            platform=unknown.platform,
            target_account_id=unknown.target_account_id,
            provider=provider,
            request_id=request_id or unknown.request_id,
            status=unknown.status.value,
            retry_count=unknown.attempt_count,
            error=error,
            metadata={
                "job_id": unknown.job_id,
                "error_code": error_code,
                "automatic_retry": False,
                "remote_post_id": remote_post_id,
                **safe_metadata,
            },
        ),
    )
    return unknown, publication


def unknown_result(job: PublicationJob, provider: str) -> PublishResult:
    return PublishResult(
        platform=job.platform,
        provider=provider,
        snapshot_id=job.snapshot_id,
        target_account_id=job.target_account_id,
        remote_post_id=str(job.reconciliation.get("remote_post_id") or "") or None,
        status=PublishingStatus.RECONCILIATION_REQUIRED,
        request_id=job.request_id or job.job_id,
        response_metadata={"automatic_retry": False, "outcome_unknown": True},
        error_code=job.error_code,
        error=job.error,
        retryable=False,
    )


def recover_stale_publishing_jobs(
    session: Session,
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> list[PublicationJob]:
    """Move abandoned publishing jobs to manual reconciliation without resending them."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current - timedelta(seconds=max(1, stale_after_seconds))
    active_leases = {
        lease.job_id for lease in list_worker_leases(session) if lease.lease_until > current
    }
    recovered: list[PublicationJob] = []
    for job in list_jobs(session, statuses={PublishingStatus.PUBLISHING}):
        if job.job_id in active_leases or job.updated_at.astimezone(UTC) > cutoff:
            continue
        updated, _ = mark_outcome_unknown(
            session,
            job,
            provider=job.platform.value,
            error_code="STALE_PUBLISHING_ATTEMPT",
            error=(
                "投稿処理中にWorkerが停止した可能性があります。外部側を確認するまで"
                "自動再送しません。"
            ),
            metadata={"recovered_as_stale": True},
            now=current,
        )
        recovered.append(updated)
    return recovered


def resolve_outcome(
    session: Session,
    job_id: str,
    *,
    actor: str,
    published: bool,
    provider: str,
    remote_post_id: str = "",
    remote_url: str = "",
    now: datetime | None = None,
) -> tuple[PublicationJob, ExternalPublication | None]:
    """Record a human-verified outcome; this function never calls an external API."""

    operator = actor.strip()
    if not operator:
        raise ValueError("照合した担当者を入力してください")
    job = get_job(session, job_id)
    if job is None:
        raise ValueError("Publication Jobが見つかりません")
    if job.status not in RECONCILIABLE_STATUSES:
        raise ValueError("結果不明または停止した投稿中Jobだけ照合できます")

    resolved_at = (now or datetime.now(UTC)).astimezone(UTC)
    existing = get_publication_by_key(session, job.idempotency_key)
    normalized_remote_id = remote_post_id.strip() or (
        existing.remote_post_id if existing else ""
    )
    normalized_url = _validated_remote_url(remote_url) or (
        existing.remote_url if existing else None
    )

    if published and not normalized_remote_id:
        raise ValueError("投稿済みとして確定するには外部サービスの投稿IDが必要です")

    outcome = "published" if published else "not_published"
    reconciliation = {
        **job.reconciliation,
        "outcome": outcome,
        "resolved_at": resolved_at.isoformat(),
        "resolved_by": operator,
        "remote_post_id": normalized_remote_id,
    }
    updated_job = job.model_copy(
        update={
            "status": PublishingStatus.PUBLISHED if published else PublishingStatus.QUEUED,
            "next_attempt_at": None if published else resolved_at,
            "request_id": (existing.request_id if existing else job.request_id),
            "error_code": "",
            "error": "",
            "reconciliation": reconciliation,
            "updated_at": resolved_at,
        }
    )
    save_job(session, updated_job)

    publication: ExternalPublication | None = existing
    if published:
        values = {
            "job_id": job.job_id,
            "campaign_id": job.campaign_id,
            "snapshot_id": job.snapshot_id,
            "platform": job.platform,
            "target_account_id": job.target_account_id,
            "provider": provider,
            "idempotency_key": job.idempotency_key,
            "remote_post_id": normalized_remote_id,
            "remote_url": normalized_url,
            "status": PublishingStatus.PUBLISHED,
            "request_id": (existing.request_id if existing else job.request_id or job.job_id),
            "published_at": existing.published_at if existing else resolved_at,
            "verified_at": resolved_at,
            "response_metadata": {
                **(existing.response_metadata if existing else {}),
                "manual_reconciliation": True,
                "reconciled_by": operator,
            },
        }
        if existing:
            publication = existing.model_copy(update=values)
        else:
            publication = ExternalPublication(
                job_id=job.job_id,
                campaign_id=job.campaign_id,
                snapshot_id=job.snapshot_id,
                platform=job.platform,
                target_account_id=job.target_account_id,
                provider=provider,
                idempotency_key=job.idempotency_key,
                remote_post_id=normalized_remote_id,
                remote_url=normalized_url,
                status=PublishingStatus.PUBLISHED,
                request_id=job.request_id or job.job_id,
                published_at=resolved_at,
                verified_at=resolved_at,
                response_metadata={
                    "manual_reconciliation": True,
                    "reconciled_by": operator,
                },
            )
        save_publication(session, publication)
    elif existing:
        publication = existing.model_copy(
            update={
                "status": PublishingStatus.FAILED,
                "verified_at": resolved_at,
                "response_metadata": {
                    **existing.response_metadata,
                    "manual_reconciliation": True,
                    "confirmed_not_published": True,
                    "reconciled_by": operator,
                },
            }
        )
        save_publication(session, publication)

    save_audit(
        session,
        AuditLog(
            event_type=AuditEventType.PUBLISH_RECONCILED,
            actor=operator,
            campaign_id=job.campaign_id,
            snapshot_id=job.snapshot_id,
            publication_id=publication.publication_id if publication else "",
            platform=job.platform,
            target_account_id=job.target_account_id,
            provider=provider,
            request_id=updated_job.request_id,
            status=updated_job.status.value,
            retry_count=updated_job.attempt_count,
            metadata={
                "job_id": job.job_id,
                "verified_outcome": outcome,
                "automatic_retry": False,
                "remote_post_id": normalized_remote_id,
            },
        ),
    )
    return updated_job, publication


def _validated_remote_url(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("外部投稿URLは認証情報を含まないHTTPS URLで入力してください")
    return cleaned
