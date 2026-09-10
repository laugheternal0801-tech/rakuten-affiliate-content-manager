from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utcnow


class SocialAccountRow(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("platform", "account_id", name="uq_social_account_platform"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(300), index=True)
    display_name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), index=True)
    credential_reference: Mapped[str] = mapped_column(String(300), default="")
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApprovalRecordRow(Base):
    __tablename__ = "approval_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_package_id: Mapped[str] = mapped_column(
        ForeignKey("creative_content_packages.id"), index=True
    )
    content_version: Mapped[int] = mapped_column(Integer)
    approval_type: Mapped[str] = mapped_column(String(32), index=True)
    approved_by: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovedSnapshotRow(Base):
    __tablename__ = "approved_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "content_package_id",
            "version",
            "source_candidate_id",
            "target_account_id",
            name="uq_approved_snapshot_version_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_records.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    content_package_id: Mapped[str] = mapped_column(
        ForeignKey("creative_content_packages.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    target_account_id: Mapped[str] = mapped_column(String(300), index=True)
    source_candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    source_candidate_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublishingScheduleRow(Base):
    __tablename__ = "publishing_schedules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("approved_snapshots.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    target_account_id: Mapped[str] = mapped_column(String(300), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), index=True)
    schedule_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PublicationJobRow(Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_publication_job_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("publishing_schedules.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("approved_snapshots.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    target_account_id: Mapped[str] = mapped_column(String(300), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    job_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExternalPublicationRow(Base):
    __tablename__ = "external_publications"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_external_publication_key"),
        UniqueConstraint(
            "platform",
            "target_account_id",
            "remote_post_id",
            name="uq_external_remote_post",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("publication_jobs.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("approved_snapshots.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    target_account_id: Mapped[str] = mapped_column(String(300), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    remote_post_id: Mapped[str] = mapped_column(String(300), index=True)
    remote_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    publication_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PerformanceSnapshotRow(Base):
    __tablename__ = "performance_snapshots"
    __table_args__ = (
        UniqueConstraint("publication_id", "window", name="uq_performance_publication_window"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    publication_id: Mapped[str] = mapped_column(ForeignKey("external_publications.id"), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    window: Mapped[str] = mapped_column(String(20), index=True)
    age_hours: Mapped[float] = mapped_column(Float)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CreativePerformanceRow(Base):
    __tablename__ = "creative_performance"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    publication_id: Mapped[str] = mapped_column(ForeignKey("external_publications.id"), index=True)
    market: Mapped[str] = mapped_column(String(300), index=True)
    audience: Mapped[str] = mapped_column(String(300), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200), index=True)
    judge_score: Mapped[float | None] = mapped_column(Float)
    high_performer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WinningPatternRow(Base):
    __tablename__ = "winning_patterns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market: Mapped[str] = mapped_column(String(300), index=True)
    audience: Mapped[str] = mapped_column(String(300), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    pattern_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningRecordRow(Base):
    __tablename__ = "learning_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(300), index=True)
    audience: Mapped[str] = mapped_column(String(300), index=True)
    platform: Mapped[str | None] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    record_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    experiment_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(300), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    content_package_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), index=True)
    publication_id: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str | None] = mapped_column(String(32), index=True)
    target_account_id: Mapped[str] = mapped_column(String(300), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    log_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PublishingControlRow(Base):
    __tablename__ = "publishing_controls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    globally_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    publishing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    autonomy: Mapped[str] = mapped_column(String(32))
    control_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PublishingWorkerLeaseRow(Base):
    __tablename__ = "publishing_worker_leases"

    job_id: Mapped[str] = mapped_column(ForeignKey("publication_jobs.id"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(200), index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PublishingWorkerHeartbeatRow(Base):
    __tablename__ = "publishing_worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class NotificationOutboxRow(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_notification_dedupe"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    target: Mapped[str] = mapped_column(String(100), index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    event_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
