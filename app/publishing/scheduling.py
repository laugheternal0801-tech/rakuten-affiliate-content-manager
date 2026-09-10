from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.approval import PublishingApprovalService
from app.publishing.hashing import idempotency_hash
from app.publishing.repositories import (
    get_account,
    get_job_by_key,
    get_schedule,
    get_snapshot,
    list_jobs,
    save_audit,
    save_job,
    save_schedule,
)
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    PublicationJob,
    PublishingPlatform,
    PublishingSchedule,
    PublishingStatus,
    RecommendedSchedule,
)

RECOMMENDED_LOCAL_TIMES = {
    PublishingPlatform.X: time(8, 15),
    PublishingPlatform.INSTAGRAM: time(12, 30),
    PublishingPlatform.TIKTOK: time(19, 15),
    PublishingPlatform.YOUTUBE: time(20, 0),
    PublishingPlatform.PINTEREST: time(9, 0),
    PublishingPlatform.REDDIT: time(18, 30),
    PublishingPlatform.GENERIC: time(10, 0),
}


class PublishingScheduler:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._approval = PublishingApprovalService()

    def create_approved_schedule(
        self,
        session: Session,
        snapshot_id: str,
        scheduled_at: datetime,
        timezone: str,
        approved_by: str,
        *,
        earliest_publish_at: datetime | None = None,
        latest_publish_at: datetime | None = None,
    ) -> tuple[PublishingSchedule, PublicationJob]:
        if not approved_by.strip():
            raise ValueError("Schedule承認者が必要です。")
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
            raise ValueError("scheduled_atはTimezone-awareで指定してください。")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone}") from exc
        snapshot = get_snapshot(session, snapshot_id)
        if snapshot is None:
            raise LookupError("Approved Snapshotがありません。")
        valid, problems = self._approval.validate_snapshot_lock(session, snapshot_id)
        if not valid:
            raise ValueError("再承認が必要です: " + " / ".join(problems))
        account = get_account(session, snapshot.target_account_id)
        if account is None or account.platform is not snapshot.platform:
            raise ValueError("Approved SnapshotのTarget Accountが無効です。")
        scheduled_utc = scheduled_at.astimezone(UTC)
        if scheduled_utc < datetime.now(UTC) - timedelta(minutes=1):
            raise ValueError("過去時刻へScheduleできません。")
        key = idempotency_hash(
            snapshot.campaign_id,
            snapshot.snapshot_id,
            snapshot.platform,
            snapshot.target_account_id,
            scheduled_utc.isoformat(),
        )
        existing_job = get_job_by_key(session, key)
        if existing_job:
            existing_schedule = get_schedule(session, existing_job.schedule_id)
            if existing_schedule is None:
                raise RuntimeError("Idempotent JobのScheduleがありません。")
            return existing_schedule, existing_job

        schedule = PublishingSchedule(
            campaign_id=snapshot.campaign_id,
            content_snapshot_id=snapshot.snapshot_id,
            platform=snapshot.platform,
            target_account_id=snapshot.target_account_id,
            scheduled_at=scheduled_at,
            timezone=timezone,
            earliest_publish_at=earliest_publish_at,
            latest_publish_at=latest_publish_at,
            approved_by=approved_by.strip(),
        )
        job = PublicationJob(
            schedule_id=schedule.schedule_id,
            campaign_id=snapshot.campaign_id,
            snapshot_id=snapshot.snapshot_id,
            platform=snapshot.platform,
            target_account_id=snapshot.target_account_id,
            idempotency_key=key,
            scheduled_at=scheduled_utc,
            max_attempts=self._settings.publishing_max_retries + 1,
        )
        save_schedule(session, schedule)
        save_job(session, job)
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.SCHEDULE_CREATED,
                actor=approved_by.strip(),
                campaign_id=snapshot.campaign_id,
                content_package_id=snapshot.content_package_id,
                snapshot_id=snapshot.snapshot_id,
                platform=snapshot.platform,
                target_account_id=snapshot.target_account_id,
                status=PublishingStatus.SCHEDULED.value,
                metadata={
                    "schedule_id": schedule.schedule_id,
                    "scheduled_at": scheduled_at.isoformat(),
                    "timezone": timezone,
                },
            ),
        )
        return schedule, job

    def cancel(
        self, session: Session, schedule_id: str, actor: str
    ) -> tuple[PublishingSchedule, PublicationJob]:
        schedule = self._require_schedule(session, schedule_id)
        job = self._require_job_for_schedule(session, schedule_id)
        if job.status in {
            PublishingStatus.PUBLISHING,
            PublishingStatus.RECONCILIATION_REQUIRED,
            PublishingStatus.SUBMITTED,
            PublishingStatus.PROCESSING,
            PublishingStatus.PUBLISHED,
            PublishingStatus.DRY_RUN_COMPLETED,
        }:
            raise ValueError("投稿開始後のScheduleはCancelできません。")
        now = datetime.now(UTC)
        schedule = schedule.model_copy(update={"status": PublishingStatus.CANCELLED})
        job = job.model_copy(update={"status": PublishingStatus.CANCELLED, "updated_at": now})
        save_schedule(session, schedule)
        save_job(session, job)
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.SCHEDULE_CANCELLED,
                actor=actor.strip() or "unknown",
                campaign_id=schedule.campaign_id,
                snapshot_id=schedule.content_snapshot_id,
                platform=schedule.platform,
                target_account_id=schedule.target_account_id,
                status=PublishingStatus.CANCELLED.value,
                metadata={"schedule_id": schedule.schedule_id},
            ),
        )
        return schedule, job

    def pause(self, session: Session, schedule_id: str) -> PublicationJob:
        job = self._require_job_for_schedule(session, schedule_id)
        if job.status not in {PublishingStatus.QUEUED, PublishingStatus.WAITING}:
            raise ValueError("Queued JobだけPauseできます。")
        updated = job.model_copy(
            update={"status": PublishingStatus.WAITING, "updated_at": datetime.now(UTC)}
        )
        save_job(session, updated)
        return updated

    def resume(self, session: Session, schedule_id: str) -> PublicationJob:
        job = self._require_job_for_schedule(session, schedule_id)
        if job.status is not PublishingStatus.WAITING:
            raise ValueError("Paused JobだけResumeできます。")
        updated = job.model_copy(
            update={"status": PublishingStatus.QUEUED, "updated_at": datetime.now(UTC)}
        )
        save_job(session, updated)
        return updated

    def reschedule(
        self,
        session: Session,
        schedule_id: str,
        new_scheduled_at: datetime,
        timezone: str,
        approved_by: str,
    ) -> tuple[PublishingSchedule, PublicationJob]:
        old = self._require_schedule(session, schedule_id)
        self.cancel(session, schedule_id, approved_by)
        schedule, job = self.create_approved_schedule(
            session,
            old.content_snapshot_id,
            new_scheduled_at,
            timezone,
            approved_by,
            earliest_publish_at=old.earliest_publish_at,
            latest_publish_at=old.latest_publish_at,
        )
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.SCHEDULE_RESCHEDULED,
                actor=approved_by.strip(),
                campaign_id=schedule.campaign_id,
                snapshot_id=schedule.content_snapshot_id,
                platform=schedule.platform,
                target_account_id=schedule.target_account_id,
                status=PublishingStatus.SCHEDULED.value,
                metadata={
                    "old_schedule_id": schedule_id,
                    "new_schedule_id": schedule.schedule_id,
                },
            ),
        )
        return schedule, job

    def due_jobs(self, session: Session, now: datetime | None = None) -> list[PublicationJob]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return [
            job
            for job in list_jobs(session, statuses={PublishingStatus.QUEUED})
            if job.scheduled_at.astimezone(UTC) <= current
            and (job.next_attempt_at is None or job.next_attempt_at.astimezone(UTC) <= current)
        ]

    def recommend(
        self,
        platforms: list[PublishingPlatform],
        *,
        timezone: str | None = None,
        start: datetime | None = None,
    ) -> list[RecommendedSchedule]:
        timezone_name = timezone or self._settings.publishing_default_timezone
        zone = ZoneInfo(timezone_name)
        base = (start or datetime.now(zone)).astimezone(zone)
        suggestions: list[RecommendedSchedule] = []
        for offset, platform in enumerate(dict.fromkeys(platforms), start=1):
            local_time = RECOMMENDED_LOCAL_TIMES[platform]
            candidate = datetime.combine(
                (base + timedelta(days=offset)).date(), local_time, tzinfo=zone
            )
            suggestions.append(
                RecommendedSchedule(
                    platform=platform,
                    recommended_at=candidate,
                    timezone=timezone_name,
                    reason="初期Rule-based提案。実Performance蓄積後に更新します。",
                    confidence=0.35,
                )
            )
        return suggestions

    @staticmethod
    def _require_schedule(session: Session, schedule_id: str) -> PublishingSchedule:
        schedule = get_schedule(session, schedule_id)
        if schedule is None:
            raise LookupError(f"Schedule {schedule_id} was not found.")
        return schedule

    @staticmethod
    def _require_job_for_schedule(session: Session, schedule_id: str) -> PublicationJob:
        jobs = [job for job in list_jobs(session) if job.schedule_id == schedule_id]
        if not jobs:
            raise LookupError(f"Schedule {schedule_id} has no Job.")
        return jobs[0]
