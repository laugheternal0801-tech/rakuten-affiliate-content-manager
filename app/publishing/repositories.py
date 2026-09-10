from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.publishing.models import (
    ApprovalRecordRow,
    ApprovedSnapshotRow,
    AuditLogRow,
    CreativePerformanceRow,
    ExperimentRow,
    ExternalPublicationRow,
    LearningRecordRow,
    NotificationOutboxRow,
    PerformanceSnapshotRow,
    PublicationJobRow,
    PublishingControlRow,
    PublishingScheduleRow,
    PublishingWorkerHeartbeatRow,
    PublishingWorkerLeaseRow,
    SocialAccountRow,
    WinningPatternRow,
)
from app.publishing.schemas import (
    ApprovalRecord,
    ApprovalRecordStatus,
    ApprovedContentSnapshot,
    AuditLog,
    ContentPerformanceSnapshot,
    CreativePerformanceRecord,
    ExperimentRecord,
    ExternalPublication,
    LearningRecord,
    NotificationEvent,
    NotificationStatus,
    PublicationJob,
    PublishingControl,
    PublishingPlatform,
    PublishingSchedule,
    PublishingStatus,
    SocialAccountConnection,
    WinningPattern,
    WorkerHeartbeat,
    WorkerLease,
)


def _json(value: Any) -> dict[str, Any]:
    data = value.model_dump(mode="json")
    if not isinstance(data, dict):
        raise TypeError("JSON object is required.")
    return data


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def save_account(session: Session, account: SocialAccountConnection) -> None:
    existing = session.get(SocialAccountRow, account.connection_id)
    if existing is None:
        existing = SocialAccountRow(
            id=account.connection_id,
            platform=account.platform.value,
            account_id=account.account_id,
            display_name=account.display_name,
            status=account.status.value,
            credential_reference=account.credential_reference,
            token_expiry=account.token_expiry,
            account_json=_json(account),
            created_at=account.created_at,
        )
        session.add(existing)
    else:
        existing.account_id = account.account_id
        existing.display_name = account.display_name
        existing.status = account.status.value
        existing.credential_reference = account.credential_reference
        existing.token_expiry = account.token_expiry
        existing.account_json = _json(account)
    session.flush()


def get_account(session: Session, connection_id: str) -> SocialAccountConnection | None:
    row = session.get(SocialAccountRow, connection_id)
    return SocialAccountConnection.model_validate(row.account_json) if row else None


def get_account_by_external_id(
    session: Session,
    platform: PublishingPlatform,
    account_id: str,
) -> SocialAccountConnection | None:
    statement = select(SocialAccountRow).where(
        SocialAccountRow.platform == platform.value,
        SocialAccountRow.account_id == account_id,
    )
    row = session.scalar(statement)
    return SocialAccountConnection.model_validate(row.account_json) if row else None


def list_accounts(
    session: Session, platform: PublishingPlatform | None = None
) -> list[SocialAccountConnection]:
    statement = select(SocialAccountRow).order_by(
        SocialAccountRow.platform, SocialAccountRow.display_name
    )
    if platform:
        statement = statement.where(SocialAccountRow.platform == platform.value)
    return [
        SocialAccountConnection.model_validate(row.account_json)
        for row in session.scalars(statement)
    ]


def save_approval(session: Session, approval: ApprovalRecord) -> None:
    session.add(
        ApprovalRecordRow(
            id=approval.approval_id,
            content_package_id=approval.content_package_id,
            content_version=approval.content_version,
            approval_type=approval.approval_type.value,
            approved_by=approval.approved_by,
            status=approval.status.value,
            content_hash=approval.content_hash,
            record_json=_json(approval),
            approved_at=approval.approved_at,
        )
    )
    session.flush()


def get_approval(session: Session, approval_id: str) -> ApprovalRecord | None:
    row = session.get(ApprovalRecordRow, approval_id)
    return ApprovalRecord.model_validate(row.record_json) if row else None


def list_approvals(
    session: Session,
    package_id: str | None = None,
    *,
    active_only: bool = False,
) -> list[ApprovalRecord]:
    statement = select(ApprovalRecordRow).order_by(ApprovalRecordRow.approved_at.desc())
    if package_id:
        statement = statement.where(ApprovalRecordRow.content_package_id == package_id)
    if active_only:
        statement = statement.where(ApprovalRecordRow.status == ApprovalRecordStatus.ACTIVE.value)
    return [ApprovalRecord.model_validate(row.record_json) for row in session.scalars(statement)]


def invalidate_approval(session: Session, approval_id: str, reason: str) -> ApprovalRecord:
    row = session.get(ApprovalRecordRow, approval_id)
    if row is None:
        raise LookupError(f"Approval {approval_id} was not found.")
    approval = ApprovalRecord.model_validate(row.record_json).model_copy(
        update={"status": ApprovalRecordStatus.INVALIDATED, "reason": reason}
    )
    row.status = approval.status.value
    row.record_json = _json(approval)
    row.invalidated_at = datetime.now(UTC)
    session.flush()
    return approval


def save_snapshot(session: Session, snapshot: ApprovedContentSnapshot) -> None:
    session.add(
        ApprovedSnapshotRow(
            id=snapshot.snapshot_id,
            approval_id=snapshot.approval_id,
            campaign_id=snapshot.campaign_id,
            content_package_id=snapshot.content_package_id,
            version=snapshot.version,
            platform=snapshot.platform.value,
            target_account_id=snapshot.target_account_id,
            source_candidate_id=snapshot.source_candidate_id,
            source_candidate_hash=snapshot.source_candidate_hash,
            content_hash=snapshot.content_hash,
            snapshot_json=_json(snapshot),
            created_at=snapshot.created_at,
        )
    )
    session.flush()


def get_snapshot(session: Session, snapshot_id: str) -> ApprovedContentSnapshot | None:
    row = session.get(ApprovedSnapshotRow, snapshot_id)
    return ApprovedContentSnapshot.model_validate(row.snapshot_json) if row else None


def list_snapshots(
    session: Session,
    package_id: str | None = None,
    platform: PublishingPlatform | None = None,
) -> list[ApprovedContentSnapshot]:
    statement = select(ApprovedSnapshotRow).order_by(ApprovedSnapshotRow.created_at.desc())
    if package_id:
        statement = statement.where(ApprovedSnapshotRow.content_package_id == package_id)
    if platform:
        statement = statement.where(ApprovedSnapshotRow.platform == platform.value)
    return [
        ApprovedContentSnapshot.model_validate(row.snapshot_json)
        for row in session.scalars(statement)
    ]


def save_schedule(session: Session, schedule: PublishingSchedule) -> None:
    row = session.get(PublishingScheduleRow, schedule.schedule_id)
    if row is None:
        row = PublishingScheduleRow(
            id=schedule.schedule_id,
            campaign_id=schedule.campaign_id,
            snapshot_id=schedule.content_snapshot_id,
            platform=schedule.platform.value,
            target_account_id=schedule.target_account_id,
            scheduled_at=schedule.scheduled_at,
            timezone=schedule.timezone,
            status=schedule.status.value,
            schedule_json=_json(schedule),
            created_at=schedule.created_at,
        )
        session.add(row)
    else:
        row.scheduled_at = schedule.scheduled_at
        row.timezone = schedule.timezone
        row.status = schedule.status.value
        row.schedule_json = _json(schedule)
    session.flush()


def get_schedule(session: Session, schedule_id: str) -> PublishingSchedule | None:
    row = session.get(PublishingScheduleRow, schedule_id)
    return PublishingSchedule.model_validate(row.schedule_json) if row else None


def list_schedules(session: Session, campaign_id: str | None = None) -> list[PublishingSchedule]:
    statement = select(PublishingScheduleRow).order_by(PublishingScheduleRow.scheduled_at)
    if campaign_id:
        statement = statement.where(PublishingScheduleRow.campaign_id == campaign_id)
    return [
        PublishingSchedule.model_validate(row.schedule_json) for row in session.scalars(statement)
    ]


def save_job(session: Session, job: PublicationJob) -> None:
    row = session.get(PublicationJobRow, job.job_id)
    if row is None:
        row = PublicationJobRow(
            id=job.job_id,
            schedule_id=job.schedule_id,
            campaign_id=job.campaign_id,
            snapshot_id=job.snapshot_id,
            platform=job.platform.value,
            target_account_id=job.target_account_id,
            idempotency_key=job.idempotency_key,
            status=job.status.value,
            scheduled_at=job.scheduled_at,
            next_attempt_at=job.next_attempt_at,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            job_json=_json(job),
            error=job.error,
            created_at=job.created_at,
        )
        session.add(row)
    else:
        row.status = job.status.value
        row.next_attempt_at = job.next_attempt_at
        row.attempt_count = job.attempt_count
        row.max_attempts = job.max_attempts
        row.job_json = _json(job)
        row.error = job.error
        row.updated_at = job.updated_at
    session.flush()


def get_job(session: Session, job_id: str) -> PublicationJob | None:
    row = session.get(PublicationJobRow, job_id)
    return PublicationJob.model_validate(row.job_json) if row else None


def get_job_by_key(session: Session, idempotency_key: str) -> PublicationJob | None:
    row = session.scalar(
        select(PublicationJobRow).where(PublicationJobRow.idempotency_key == idempotency_key)
    )
    return PublicationJob.model_validate(row.job_json) if row else None


def list_jobs(
    session: Session,
    campaign_id: str | None = None,
    statuses: set[PublishingStatus] | None = None,
) -> list[PublicationJob]:
    statement = select(PublicationJobRow).order_by(PublicationJobRow.scheduled_at)
    if campaign_id:
        statement = statement.where(PublicationJobRow.campaign_id == campaign_id)
    if statuses:
        statement = statement.where(
            PublicationJobRow.status.in_([status.value for status in statuses])
        )
    return [PublicationJob.model_validate(row.job_json) for row in session.scalars(statement)]


def save_publication(session: Session, publication: ExternalPublication) -> None:
    row = session.get(ExternalPublicationRow, publication.publication_id)
    if row is None:
        row = ExternalPublicationRow(
            id=publication.publication_id,
            job_id=publication.job_id,
            campaign_id=publication.campaign_id,
            snapshot_id=publication.snapshot_id,
            platform=publication.platform.value,
            target_account_id=publication.target_account_id,
            provider=publication.provider,
            idempotency_key=publication.idempotency_key,
            remote_post_id=publication.remote_post_id,
            remote_url=publication.remote_url,
            status=publication.status.value,
            publication_json=_json(publication),
            published_at=publication.published_at,
            verified_at=publication.verified_at,
        )
        session.add(row)
    else:
        row.provider = publication.provider
        row.status = publication.status.value
        row.remote_post_id = publication.remote_post_id
        row.remote_url = publication.remote_url
        row.publication_json = _json(publication)
        row.published_at = publication.published_at
        row.verified_at = publication.verified_at
    session.flush()


def get_publication(session: Session, publication_id: str) -> ExternalPublication | None:
    row = session.get(ExternalPublicationRow, publication_id)
    return ExternalPublication.model_validate(row.publication_json) if row else None


def get_publication_by_key(session: Session, idempotency_key: str) -> ExternalPublication | None:
    row = session.scalar(
        select(ExternalPublicationRow).where(
            ExternalPublicationRow.idempotency_key == idempotency_key
        )
    )
    return ExternalPublication.model_validate(row.publication_json) if row else None


def list_publications(
    session: Session, campaign_id: str | None = None
) -> list[ExternalPublication]:
    statement = select(ExternalPublicationRow).order_by(ExternalPublicationRow.created_at.desc())
    if campaign_id:
        statement = statement.where(ExternalPublicationRow.campaign_id == campaign_id)
    return [
        ExternalPublication.model_validate(row.publication_json)
        for row in session.scalars(statement)
    ]


def save_performance_snapshot(session: Session, snapshot: ContentPerformanceSnapshot) -> None:
    session.add(
        PerformanceSnapshotRow(
            id=snapshot.performance_snapshot_id,
            publication_id=snapshot.publication_id,
            campaign_id=snapshot.campaign_id,
            platform=snapshot.platform.value,
            window=snapshot.window,
            age_hours=snapshot.age_hours,
            metrics_json=_json(snapshot),
            measured_at=snapshot.measured_at,
        )
    )
    session.flush()


def list_performance_snapshots(
    session: Session,
    publication_id: str | None = None,
    campaign_id: str | None = None,
) -> list[ContentPerformanceSnapshot]:
    statement = select(PerformanceSnapshotRow).order_by(PerformanceSnapshotRow.measured_at.desc())
    if publication_id:
        statement = statement.where(PerformanceSnapshotRow.publication_id == publication_id)
    if campaign_id:
        statement = statement.where(PerformanceSnapshotRow.campaign_id == campaign_id)
    return [
        ContentPerformanceSnapshot.model_validate(row.metrics_json)
        for row in session.scalars(statement)
    ]


def save_creative_performance(session: Session, record: CreativePerformanceRecord) -> None:
    session.add(
        CreativePerformanceRow(
            id=record.record_id,
            campaign_id=record.campaign_id,
            publication_id=record.publication_id,
            market=record.market,
            audience=record.audience,
            platform=record.platform.value,
            provider=record.provider,
            model=record.model,
            judge_score=record.judge_score,
            high_performer=record.high_performer,
            record_json=_json(record),
        )
    )
    session.flush()


def list_creative_performance(
    session: Session, campaign_id: str | None = None
) -> list[CreativePerformanceRecord]:
    statement = select(CreativePerformanceRow).order_by(CreativePerformanceRow.created_at.desc())
    if campaign_id:
        statement = statement.where(CreativePerformanceRow.campaign_id == campaign_id)
    return [
        CreativePerformanceRecord.model_validate(row.record_json)
        for row in session.scalars(statement)
    ]


def save_winning_pattern(session: Session, pattern: WinningPattern) -> None:
    session.add(
        WinningPatternRow(
            id=pattern.pattern_id,
            market=pattern.market,
            audience=pattern.audience,
            platform=pattern.platform.value,
            active=pattern.active,
            pattern_json=_json(pattern),
        )
    )
    session.flush()


def list_winning_patterns(session: Session, market: str | None = None) -> list[WinningPattern]:
    statement = select(WinningPatternRow).where(WinningPatternRow.active.is_(True))
    if market:
        statement = statement.where(WinningPatternRow.market == market)
    return [WinningPattern.model_validate(row.pattern_json) for row in session.scalars(statement)]


def save_learning(session: Session, learning: LearningRecord) -> None:
    session.add(
        LearningRecordRow(
            id=learning.learning_id,
            campaign_id=learning.campaign_id,
            market=learning.market,
            audience=learning.audience,
            platform=learning.platform.value if learning.platform else None,
            category=learning.category,
            active=learning.active,
            record_json=_json(learning),
        )
    )
    session.flush()


def list_learning(
    session: Session,
    market: str | None = None,
    campaign_id: str | None = None,
) -> list[LearningRecord]:
    statement = select(LearningRecordRow).where(LearningRecordRow.active.is_(True))
    if market:
        statement = statement.where(LearningRecordRow.market == market)
    if campaign_id:
        statement = statement.where(LearningRecordRow.campaign_id == campaign_id)
    statement = statement.order_by(LearningRecordRow.created_at.desc())
    return [LearningRecord.model_validate(row.record_json) for row in session.scalars(statement)]


def save_experiment(session: Session, experiment: ExperimentRecord) -> None:
    session.add(
        ExperimentRow(
            id=experiment.experiment_id,
            campaign_id=experiment.campaign_id,
            platform=experiment.platform.value,
            status=experiment.status,
            experiment_json=_json(experiment),
        )
    )
    session.flush()


def save_audit(session: Session, audit: AuditLog) -> None:
    session.add(
        AuditLogRow(
            id=audit.audit_id,
            event_type=audit.event_type.value,
            actor=audit.actor,
            campaign_id=audit.campaign_id,
            content_package_id=audit.content_package_id,
            snapshot_id=audit.snapshot_id,
            publication_id=audit.publication_id,
            platform=audit.platform.value if audit.platform else None,
            target_account_id=audit.target_account_id,
            status=audit.status,
            log_json=_json(audit),
            created_at=audit.created_at,
        )
    )
    session.flush()


def list_audits(session: Session, limit: int = 200) -> list[AuditLog]:
    statement = select(AuditLogRow).order_by(AuditLogRow.created_at.desc()).limit(limit)
    return [AuditLog.model_validate(row.log_json) for row in session.scalars(statement)]


def get_control(session: Session) -> PublishingControl:
    row = session.get(PublishingControlRow, "global")
    if row is None:
        return PublishingControl()
    return PublishingControl.model_validate(row.control_json)


def save_control(session: Session, control: PublishingControl) -> None:
    row = session.get(PublishingControlRow, control.control_id)
    if row is None:
        row = PublishingControlRow(
            id=control.control_id,
            globally_paused=control.globally_paused,
            publishing_enabled=control.publishing_enabled,
            dry_run=control.dry_run,
            autonomy=control.autonomy.value,
            control_json=_json(control),
            updated_at=control.updated_at,
        )
        session.add(row)
    else:
        row.globally_paused = control.globally_paused
        row.publishing_enabled = control.publishing_enabled
        row.dry_run = control.dry_run
        row.autonomy = control.autonomy.value
        row.control_json = _json(control)
        row.updated_at = control.updated_at
    session.flush()


def save_worker_heartbeat(session: Session, heartbeat: WorkerHeartbeat) -> None:
    row = session.get(PublishingWorkerHeartbeatRow, heartbeat.worker_id)
    if row is None:
        row = PublishingWorkerHeartbeatRow(
            worker_id=heartbeat.worker_id,
            state=heartbeat.state.value,
            last_seen_at=heartbeat.last_seen_at,
            heartbeat_json=_json(heartbeat),
        )
        session.add(row)
    else:
        row.state = heartbeat.state.value
        row.last_seen_at = heartbeat.last_seen_at
        row.heartbeat_json = _json(heartbeat)
    session.flush()


def get_worker_heartbeat(session: Session, worker_id: str) -> WorkerHeartbeat | None:
    row = session.get(PublishingWorkerHeartbeatRow, worker_id)
    return WorkerHeartbeat.model_validate(row.heartbeat_json) if row else None


def list_worker_heartbeats(session: Session) -> list[WorkerHeartbeat]:
    statement = select(PublishingWorkerHeartbeatRow).order_by(
        PublishingWorkerHeartbeatRow.last_seen_at.desc()
    )
    return [
        WorkerHeartbeat.model_validate(row.heartbeat_json) for row in session.scalars(statement)
    ]


def acquire_job_lease(
    session: Session,
    job_id: str,
    worker_id: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    lease_until = current + timedelta(seconds=lease_seconds)
    row = session.get(PublishingWorkerLeaseRow, job_id)
    if row is None:
        try:
            with session.begin_nested():
                session.add(
                    PublishingWorkerLeaseRow(
                        job_id=job_id,
                        worker_id=worker_id,
                        acquired_at=current,
                        heartbeat_at=current,
                        lease_until=lease_until,
                    )
                )
                session.flush()
            return True
        except IntegrityError:
            row = session.get(PublishingWorkerLeaseRow, job_id)
    if row is None:
        return False
    result = cast(
        CursorResult[Any],
        session.execute(
            update(PublishingWorkerLeaseRow)
            .where(
                PublishingWorkerLeaseRow.job_id == job_id,
                or_(
                    PublishingWorkerLeaseRow.worker_id == worker_id,
                    PublishingWorkerLeaseRow.lease_until <= current,
                ),
            )
            .values(
                worker_id=worker_id,
                acquired_at=current,
                heartbeat_at=current,
                lease_until=lease_until,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    session.flush()
    return bool(result.rowcount)


def renew_job_lease(
    session: Session,
    job_id: str,
    worker_id: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    result = cast(
        CursorResult[Any],
        session.execute(
            update(PublishingWorkerLeaseRow)
            .where(
                PublishingWorkerLeaseRow.job_id == job_id,
                PublishingWorkerLeaseRow.worker_id == worker_id,
            )
            .values(
                heartbeat_at=current,
                lease_until=current + timedelta(seconds=lease_seconds),
            )
            .execution_options(synchronize_session=False)
        ),
    )
    session.flush()
    return bool(result.rowcount)


def release_job_lease(session: Session, job_id: str, worker_id: str) -> bool:
    result = cast(
        CursorResult[Any],
        session.execute(
            delete(PublishingWorkerLeaseRow)
            .where(
                PublishingWorkerLeaseRow.job_id == job_id,
                PublishingWorkerLeaseRow.worker_id == worker_id,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    session.flush()
    return bool(result.rowcount)


def list_worker_leases(session: Session) -> list[WorkerLease]:
    statement = select(PublishingWorkerLeaseRow).order_by(PublishingWorkerLeaseRow.lease_until)
    return [
        WorkerLease(
            job_id=row.job_id,
            worker_id=row.worker_id,
            acquired_at=_aware(row.acquired_at),
            heartbeat_at=_aware(row.heartbeat_at),
            lease_until=_aware(row.lease_until),
        )
        for row in session.scalars(statement)
    ]


def save_notification(session: Session, event: NotificationEvent) -> NotificationEvent:
    existing = session.scalar(
        select(NotificationOutboxRow).where(NotificationOutboxRow.dedupe_key == event.dedupe_key)
    )
    if existing is not None:
        return NotificationEvent.model_validate(existing.event_json)
    row = NotificationOutboxRow(
        id=event.notification_id,
        dedupe_key=event.dedupe_key,
        event_type=event.event_type,
        severity=event.severity.value,
        status=event.status.value,
        target=event.target,
        next_attempt_at=event.next_attempt_at,
        event_json=_json(event),
        error=event.error,
        created_at=event.created_at,
    )
    session.add(row)
    session.flush()
    return event


def get_notification(session: Session, notification_id: str) -> NotificationEvent | None:
    row = session.get(NotificationOutboxRow, notification_id)
    return NotificationEvent.model_validate(row.event_json) if row else None


def list_notifications(
    session: Session,
    status: NotificationStatus | None = None,
    *,
    limit: int = 200,
) -> list[NotificationEvent]:
    statement = select(NotificationOutboxRow).order_by(NotificationOutboxRow.created_at.desc())
    if status is not None:
        statement = statement.where(NotificationOutboxRow.status == status.value)
    statement = statement.limit(limit)
    return [NotificationEvent.model_validate(row.event_json) for row in session.scalars(statement)]


def update_notification(session: Session, event: NotificationEvent) -> None:
    row = session.get(NotificationOutboxRow, event.notification_id)
    if row is None:
        raise LookupError(f"Notification {event.notification_id} was not found.")
    row.status = event.status.value
    row.next_attempt_at = event.next_attempt_at
    row.event_json = _json(event)
    row.error = event.error
    session.flush()


def acknowledge_notification(
    session: Session,
    notification_id: str,
) -> NotificationEvent:
    event = get_notification(session, notification_id)
    if event is None:
        raise LookupError(f"Notification {notification_id} was not found.")
    updated = event.model_copy(
        update={
            "status": NotificationStatus.ACKNOWLEDGED,
            "acknowledged_at": datetime.now(UTC),
        }
    )
    update_notification(session, updated)
    return updated
