from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("日時はTimezone-awareで指定してください。")
    return value


class PublishingPlatform(StrEnum):
    X = "x"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    PINTEREST = "pinterest"
    REDDIT = "reddit"
    GENERIC = "generic"


class PublishingAutonomy(StrEnum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    SEMI_AUTO = "semi_auto"


class PublishingStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"
    APPROVED = "approved"
    LOCKED = "locked"
    MODIFIED_AFTER_APPROVAL = "modified_after_approval"
    REQUIRES_REAPPROVAL = "requires_reapproval"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    WAITING = "waiting"
    PREFLIGHT = "preflight"
    PUBLISHING = "publishing"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    PUBLISHED = "published"
    DRY_RUN_COMPLETED = "dry_run_completed"
    MONITORING = "monitoring"
    PERFORMANCE_COLLECTED = "performance_collected"
    ANALYZED = "analyzed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    MISSED_SCHEDULE = "missed_schedule"


class ApprovalType(StrEnum):
    CONTENT = "content"
    SCHEDULE = "schedule"
    DELETE = "delete"


class ApprovalRecordStatus(StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"


class CapabilityAvailability(StrEnum):
    AVAILABLE = "available"
    DRY_RUN = "dry_run"
    NOT_CONFIGURED = "not_configured"
    PERMISSION_REQUIRED = "permission_required"
    PUBLIC_POST_UNAVAILABLE = "public_post_unavailable"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    DISABLED = "disabled"
    ERROR = "error"


class PreflightStatus(StrEnum):
    PASS = "pass"  # noqa: S105 - workflow result, not a credential
    WARNING = "warning"
    BLOCKED = "blocked"


class CheckResult(StrEnum):
    PASS = "pass"  # noqa: S105 - validation result, not a credential
    WARNING = "warning"
    FAIL = "fail"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    NOT_APPLICABLE = "not_applicable"


class WorkerState(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"


class AuditEventType(StrEnum):
    ACCOUNT_CONNECTED = "account_connected"
    ACCOUNT_REFRESHED = "account_refreshed"
    ACCOUNT_DISCONNECTED = "account_disconnected"
    HUMAN_APPROVED = "human_approved"
    APPROVAL_INVALIDATED = "approval_invalidated"
    SNAPSHOT_LOCKED = "snapshot_locked"
    SCHEDULE_CREATED = "schedule_created"
    SCHEDULE_CANCELLED = "schedule_cancelled"
    SCHEDULE_RESCHEDULED = "schedule_rescheduled"
    PREFLIGHT_PASS = "preflight_pass"  # noqa: S105 - audit event, not a credential
    PREFLIGHT_BLOCKED = "preflight_blocked"
    PUBLISH_REQUESTED = "publish_requested"
    PUBLISH_OUTCOME_UNKNOWN = "publish_outcome_unknown"
    PUBLISH_RECONCILED = "publish_reconciled"
    PUBLISH_SUBMITTED = "publish_submitted"
    PUBLISH_SUCCESS = "publish_success"
    PUBLISH_FAILED = "publish_failed"
    DRY_RUN_COMPLETED = "dry_run_completed"
    PERFORMANCE_COLLECTED = "performance_collected"
    LEARNING_CREATED = "learning_created"
    GLOBAL_PAUSED = "global_paused"
    GLOBAL_RESUMED = "global_resumed"
    REVISION_REQUESTED = "revision_requested"
    WORKER_HEARTBEAT = "worker_heartbeat"
    NOTIFICATION_ENQUEUED = "notification_enqueued"


class SnapshotAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    file_path: str
    mime_type: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    is_placeholder: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocialAccountConnection(BaseModel):
    """Account metadata only. OAuth token bodies must stay in a secret store."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: str = Field(default_factory=lambda: identifier("ACC"))
    platform: PublishingPlatform
    account_id: str = Field(min_length=1, max_length=300)
    display_name: str = Field(min_length=1, max_length=300)
    status: CapabilityAvailability = CapabilityAvailability.NOT_CONFIGURED
    scopes: list[str] = Field(default_factory=list)
    credential_reference: str = Field(default="", max_length=300)
    token_expiry: datetime | None = None
    last_verified_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

    _token_expiry_aware = field_validator("token_expiry")(_require_aware)
    _verified_aware = field_validator("last_verified_at")(_require_aware)


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(default_factory=lambda: identifier("APR"))
    content_package_id: str
    content_version: int = Field(ge=1)
    approved_platforms: list[PublishingPlatform] = Field(min_length=1)
    approved_by: str = Field(min_length=1, max_length=300)
    approved_at: datetime = Field(default_factory=utcnow)
    approval_type: ApprovalType = ApprovalType.CONTENT
    approved_schedule: dict[str, str] | None = None
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    asset_hashes: list[str] = Field(default_factory=list)
    status: ApprovalRecordStatus = ApprovalRecordStatus.ACTIVE
    reason: str = ""


class ApprovedContentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: identifier("SNP"))
    approval_id: str
    campaign_id: str
    content_package_id: str
    version: int = Field(ge=1)
    platform: PublishingPlatform
    target_account_id: str = Field(min_length=1)
    source_candidate_id: str
    source_candidate_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = ""
    caption: str = ""
    title: str = ""
    description: str = ""
    assets: list[SnapshotAsset] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    integrity_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=utcnow)


class GeneratedMediaDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_generated: bool = True
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    paid_partnership: bool = False
    age_restricted: bool = False
    audience: str = "general"
    extra: dict[str, Any] = Field(default_factory=dict)


class PublisherCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: PublishingPlatform
    provider: str
    availability: CapabilityAvailability
    enabled: bool = False
    operations: list[str] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)
    native_scheduling: bool = False
    sandbox_available: bool = False
    max_text_length: int | None = Field(default=None, ge=1)
    max_title_length: int | None = Field(default=None, ge=1)
    max_description_length: int | None = Field(default=None, ge=1)
    max_media_count: int | None = Field(default=None, ge=1)
    supported_mime_types: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    refreshed_at: datetime = Field(default_factory=utcnow)
    message: str = ""


class PublishingSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: str = Field(default_factory=lambda: identifier("SCH"))
    campaign_id: str
    content_snapshot_id: str
    platform: PublishingPlatform
    target_account_id: str
    scheduled_at: datetime
    timezone: str
    earliest_publish_at: datetime | None = None
    latest_publish_at: datetime | None = None
    status: PublishingStatus = PublishingStatus.SCHEDULED
    approved_by: str = Field(min_length=1)
    approved_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)

    _scheduled_aware = field_validator("scheduled_at")(_require_aware)
    _earliest_aware = field_validator("earliest_publish_at")(_require_aware)
    _latest_aware = field_validator("latest_publish_at")(_require_aware)

    @model_validator(mode="after")
    def validate_window(self) -> PublishingSchedule:
        if self.earliest_publish_at and self.scheduled_at < self.earliest_publish_at:
            raise ValueError("scheduled_atはEarliest publish以降にしてください。")
        if self.latest_publish_at and self.scheduled_at > self.latest_publish_at:
            raise ValueError("scheduled_atはLatest publish以前にしてください。")
        return self


class PublishPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    snapshot_id: str
    campaign_id: str
    platform: PublishingPlatform
    target_account_id: str
    credential_reference: str = Field(default="", max_length=300)
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = ""
    caption: str = ""
    title: str = ""
    description: str = ""
    assets: list[SnapshotAsset] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    platform_metadata: dict[str, Any] = Field(default_factory=dict)
    disclosure: GeneratedMediaDisclosure = Field(default_factory=GeneratedMediaDisclosure)
    dry_run: bool = True


class PublicationJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(default_factory=lambda: identifier("JOB"))
    schedule_id: str
    campaign_id: str
    snapshot_id: str
    platform: PublishingPlatform
    target_account_id: str
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: PublishingStatus = PublishingStatus.QUEUED
    scheduled_at: datetime
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    next_attempt_at: datetime | None = None
    request_id: str = ""
    preflight: dict[str, Any] = Field(default_factory=dict)
    reconciliation: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    _scheduled_aware = field_validator("scheduled_at")(_require_aware)
    _next_attempt_aware = field_validator("next_attempt_at")(_require_aware)


class PublishResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_result_id: str = Field(default_factory=lambda: identifier("PUBR"))
    platform: PublishingPlatform
    provider: str
    snapshot_id: str
    target_account_id: str
    remote_post_id: str | None = None
    remote_url: str | None = None
    published_at: datetime | None = None
    status: PublishingStatus
    request_id: str
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error: str = ""
    retryable: bool = False
    credential_reference: str = Field(default="", max_length=300, exclude=True, repr=False)

    _published_aware = field_validator("published_at")(_require_aware)

    @model_validator(mode="after")
    def successful_live_publish_requires_remote_id(self) -> PublishResult:
        if (
            self.status
            in {
                PublishingStatus.SUBMITTED,
                PublishingStatus.PROCESSING,
                PublishingStatus.PUBLISHED,
            }
            and not self.remote_post_id
        ):
            raise ValueError("Live publish成功結果にはremote_post_idが必要です。")
        if self.status is PublishingStatus.DRY_RUN_COMPLETED and self.remote_post_id:
            raise ValueError("Dry Run結果へremote_post_idを設定できません。")
        return self


class ExternalPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: str = Field(default_factory=lambda: identifier("PUB"))
    job_id: str
    campaign_id: str
    snapshot_id: str
    platform: PublishingPlatform
    target_account_id: str
    provider: str
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    remote_post_id: str
    remote_url: str | None = None
    status: PublishingStatus
    request_id: str
    published_at: datetime | None = None
    verified_at: datetime | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)

    _published_aware = field_validator("published_at")(_require_aware)
    _verified_aware = field_validator("verified_at")(_require_aware)


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    result: CheckResult
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preflight_id: str = Field(default_factory=lambda: identifier("PFL"))
    job_id: str
    platform: PublishingPlatform
    status: PreflightStatus
    checks: list[PreflightCheck]
    checked_at: datetime = Field(default_factory=utcnow)


class ContentPerformanceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performance_snapshot_id: str = Field(default_factory=lambda: identifier("PSN"))
    publication_id: str
    campaign_id: str
    platform: PublishingPlatform
    measured_at: datetime = Field(default_factory=utcnow)
    age_hours: float = Field(ge=0)
    window: str
    impressions: int | None = Field(default=None, ge=0)
    reach: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    ctr: float | None = Field(default=None, ge=0)
    watch_time: float | None = Field(default=None, ge=0)
    average_watch_time: float | None = Field(default=None, ge=0)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    followers_gained: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)
    raw_metrics: dict[str, Any] = Field(default_factory=dict)

    _measured_aware = field_validator("measured_at")(_require_aware)


class NormalizedPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performance_snapshot_id: str
    platform: PublishingPlatform
    engagement_rate: float | None = None
    view_to_like_rate: float | None = None
    comment_rate: float | None = None
    share_rate: float | None = None
    save_rate: float | None = None
    ctr: float | None = None
    completion_rate: float | None = None
    conversion_rate: float | None = None


class PerformanceInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str = Field(default_factory=lambda: identifier("INS"))
    publication_id: str
    campaign_id: str
    platform: PublishingPlatform
    observation: str
    interpretation: str
    confidence: float = Field(ge=0, le=1)
    supporting_snapshot_ids: list[str]
    created_at: datetime = Field(default_factory=utcnow)


class CreativePerformanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(default_factory=lambda: identifier("CPR"))
    campaign_id: str
    publication_id: str
    market: str
    audience: str
    platform: PublishingPlatform
    content_type: str
    content_variant: str
    hook_type: str
    message_angle: str
    visual_style: str
    cta_type: str
    content_length: int = Field(ge=0)
    posting_time: datetime
    provider: str
    model: str
    judge_score: float | None = Field(default=None, ge=0, le=100)
    performance_metrics: dict[str, float | int | None]
    approved: bool = True
    published: bool = True
    high_performer: bool = False

    _posting_aware = field_validator("posting_time")(_require_aware)


class WinningPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(default_factory=lambda: identifier("PAT"))
    market: str
    audience: str
    platform: PublishingPlatform
    hook_type: str
    content_type: str
    duration_range: str = ""
    visual_style: str = ""
    cta_type: str = ""
    sample_size: int = Field(ge=1)
    percentile: float = Field(ge=0, le=1)
    metrics: dict[str, float | int | None]
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class LearningRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning_id: str = Field(default_factory=lambda: identifier("LRN"))
    campaign_id: str
    market: str
    audience: str
    platform: PublishingPlatform | None = None
    category: str
    observation: str
    interpretation: str
    recommendation: str
    evidence_snapshot_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(default_factory=lambda: identifier("EXP"))
    campaign_id: str
    platform: PublishingPlatform
    hypothesis: str
    control_variant: str
    challenger_variant: str
    exploration_ratio: float = Field(default=0.2, ge=0, le=1)
    status: str = "planned"
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class AuditLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=lambda: identifier("AUD"))
    event_type: AuditEventType
    actor: str
    campaign_id: str = ""
    content_package_id: str = ""
    snapshot_id: str = ""
    publication_id: str = ""
    platform: PublishingPlatform | None = None
    target_account_id: str = ""
    provider: str = ""
    request_id: str = ""
    status: str
    retry_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class PublishingControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_id: str = "global"
    globally_paused: bool = False
    publishing_enabled: bool = False
    dry_run: bool = True
    autonomy: PublishingAutonomy = PublishingAutonomy.ASSISTED
    updated_by: str = "system"
    updated_at: datetime = Field(default_factory=utcnow)


class RecommendedSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: PublishingPlatform
    recommended_at: datetime
    timezone: str
    reason: str
    confidence: float = Field(ge=0, le=1)
    requires_human_approval: bool = True

    _recommended_aware = field_validator("recommended_at")(_require_aware)


class WorkerHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=200)
    state: WorkerState = WorkerState.STARTING
    started_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    processed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    current_job_id: str = ""
    message: str = ""

    _started_aware = field_validator("started_at")(_require_aware)
    _last_seen_aware = field_validator("last_seen_at")(_require_aware)


class WorkerLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    worker_id: str
    acquired_at: datetime
    heartbeat_at: datetime
    lease_until: datetime

    _acquired_aware = field_validator("acquired_at")(_require_aware)
    _heartbeat_aware = field_validator("heartbeat_at")(_require_aware)
    _lease_until_aware = field_validator("lease_until")(_require_aware)


class WorkerRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    claimed_job_ids: list[str] = Field(default_factory=list)
    results: dict[str, str] = Field(default_factory=dict)
    skipped_reason: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)

    _run_started_aware = field_validator("started_at")(_require_aware)
    _run_finished_aware = field_validator("finished_at")(_require_aware)


class NotificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_id: str = Field(default_factory=lambda: identifier("NTF"))
    dedupe_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_type: str = Field(min_length=1, max_length=100)
    severity: NotificationSeverity = NotificationSeverity.INFO
    title: str = Field(min_length=1, max_length=300)
    message: str = Field(min_length=1, max_length=2_000)
    status: NotificationStatus = NotificationStatus.PENDING
    campaign_id: str = ""
    job_id: str = ""
    publication_id: str = ""
    target: str = "in_app"
    attempt_count: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None
    acknowledged_at: datetime | None = None

    _notification_next_aware = field_validator("next_attempt_at")(_require_aware)
    _notification_delivered_aware = field_validator("delivered_at")(_require_aware)
    _notification_ack_aware = field_validator("acknowledged_at")(_require_aware)
