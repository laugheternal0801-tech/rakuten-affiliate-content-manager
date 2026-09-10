from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class CreativeQualityLevel(StrEnum):
    DRAFT = "draft"
    STANDARD = "standard"
    PREMIUM = "premium"
    FLAGSHIP = "flagship"


class CreativePlatform(StrEnum):
    ARTICLE = "article"
    NOTE = "note"
    BLOG = "blog"
    SEO_ARTICLE = "seo_article"
    X = "x"
    INSTAGRAM_FEED = "instagram_feed"
    INSTAGRAM_CAROUSEL = "instagram_carousel"
    INSTAGRAM_REEL = "instagram_reel"
    INSTAGRAM_STORY = "instagram_story"
    TIKTOK = "tiktok"
    YOUTUBE_LONG = "youtube_long"
    YOUTUBE_SHORTS = "youtube_shorts"
    PINTEREST = "pinterest"
    IMAGE = "image"
    VIDEO = "video"


class CreativeStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    BRIEFING = "briefing"
    GENERATING = "generating"
    JUDGING = "judging"
    REVISING = "revising"
    COMPOSITING = "compositing"
    QUALITY_CHECK = "quality_check"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    PARTIAL = "partial"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_REVISION = "request_revision"
    REJECT = "reject"


class ProviderAvailability(StrEnum):
    AVAILABLE = "available"
    NOT_CONFIGURED = "not_configured"
    DEGRADED = "degraded"
    ERROR = "error"


class AssetKind(StrEnum):
    GENERATED_IMAGE = "generated_image"
    COMPOSITE_IMAGE = "composite_image"
    STORYBOARD = "storyboard"
    VIDEO_SHOT = "video_shot"
    VIDEO = "video"
    SCRIPT = "script"
    CAPTION = "caption"
    DOCUMENT = "document"


class ClaimUseStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


class ClaimBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str
    claim: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1)
    allowed: bool = True
    verification: str = "SUPPORTED"


class CampaignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(default_factory=lambda: identifier("CAM"))
    research_run_id: str
    name: str
    objective: str
    target: str
    pain_point: str
    core_insight: str
    primary_message: str
    angles: list[str]
    hooks: list[str]
    cta: str
    platform_priority: list[CreativePlatform]
    content_series: list[dict[str, str]] = Field(default_factory=list)
    performance_signals: list[str] = Field(default_factory=list)
    publishing_priority: int = Field(default=1, ge=1, le=5)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class BrandVoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(default_factory=lambda: identifier("BVP"))
    name: str = "Default voice"
    tone: list[str] = Field(default_factory=lambda: ["明快", "誠実", "具体的"])
    formality: Literal["casual", "balanced", "formal"] = "balanced"
    sentence_length: Literal["short", "mixed", "long"] = "mixed"
    vocabulary: list[str] = Field(default_factory=list)
    emoji_usage: Literal["none", "light", "moderate"] = "light"
    humor: Literal["none", "light", "active"] = "light"
    sales_pressure: Literal["low", "medium", "high"] = "low"
    authority: Literal["peer", "guide", "expert"] = "guide"
    personality: str = "信頼できる編集者"
    forbidden_phrases: list[str] = Field(
        default_factory=lambda: ["絶対", "必ず成功", "誰でも稼げる"]
    )
    preferred_phrases: list[str] = Field(default_factory=list)
    writing_examples: list[str] = Field(default_factory=list)


class BrandVisualProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(default_factory=lambda: identifier("BVIS"))
    name: str = "Default visual"
    logo_path: str = ""
    colors: list[str] = Field(default_factory=lambda: ["#17324D", "#F3B61F", "#F7F9FB"])
    typography: list[str] = Field(default_factory=lambda: ["Yu Gothic", "sans-serif"])
    spacing: str = "generous"
    image_style: str = "editorial, clean, credible"
    photography_style: str = "natural light, documentary"
    illustration_style: str = "minimal geometric"
    video_style: str = "clear visual progression"
    motion_style: str = "purposeful, restrained"
    forbidden_visuals: list[str] = Field(
        default_factory=lambda: ["third-party logos", "watermarks", "misleading UI"]
    )

    @field_validator("colors")
    @classmethod
    def validate_colors(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("Brand colorを1色以上指定してください。")
        for value in values:
            if len(value) != 7 or not value.startswith("#"):
                raise ValueError("Brand colorは#RRGGBB形式で指定してください。")
            int(value[1:], 16)
        return values


class ContentBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content_brief_id: str = Field(default_factory=lambda: identifier("BRF"))
    research_run_id: str
    strategy_id: str
    campaign_id: str
    market: str
    target_audience: str
    target_problem: str
    consumer_insight: str
    objective: str
    primary_message: str
    secondary_messages: list[str] = Field(default_factory=list)
    platforms: list[CreativePlatform] = Field(min_length=1)
    content_types: list[str] = Field(min_length=1)
    cta: str
    allowed_claims: list[ClaimBinding] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    brand_voice_profile_id: str
    brand_visual_profile_id: str
    creative_direction: str
    visual_direction: str
    tone: list[str]
    references: list[str] = Field(default_factory=list)
    performance_signals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    quality_level: CreativeQualityLevel
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def evidence_must_cover_allowed_claims(self) -> ContentBrief:
        known = set(self.evidence_ids)
        for claim in self.allowed_claims:
            if not set(claim.evidence_ids) <= known:
                raise ValueError("allowed_claimsのEvidenceはBriefのevidence_idsに必要です。")
        return self


class CreativeProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_run_id: str
    platforms: list[CreativePlatform] = Field(min_length=1)
    quality_level: CreativeQualityLevel = CreativeQualityLevel.STANDARD
    include_images: bool = True
    include_video_storyboards: bool = True
    max_revision_rounds: int | None = Field(default=None, ge=0, le=5)
    budget_limit: float | None = Field(default=None, ge=0)
    allow_external_api: bool = False
    target_audience_override: str = ""
    objective_override: str = ""
    cta_override: str = ""

    @field_validator("platforms")
    @classmethod
    def deduplicate_platforms(cls, platforms: list[CreativePlatform]) -> list[CreativePlatform]:
        return list(dict.fromkeys(platforms))


class PlatformTask(BaseModel):
    task_id: str = Field(default_factory=lambda: identifier("TSK"))
    platform: CreativePlatform
    format_name: str
    objective: str
    core_message: str
    evidence_ids: list[str]
    requirements: list[str]
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ContentCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: identifier("CAN"))
    content_brief_id: str
    platform: CreativePlatform
    provider: str
    model: str
    variant: str
    content: str
    structured_content: dict[str, Any] = Field(default_factory=dict)
    claims_used: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    parent_candidate_id: str | None = None
    revision_round: int = Field(default=0, ge=0)
    is_external: bool = False
    is_placeholder: bool = False
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    created_at: datetime = Field(default_factory=utcnow)


class CreativeScore(BaseModel):
    score_id: str = Field(default_factory=lambda: identifier("SCR"))
    candidate_id: str
    judge_name: str
    rubric: dict[str, float]
    overall: float = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)

    @field_validator("rubric")
    @classmethod
    def validate_rubric(cls, rubric: dict[str, float]) -> dict[str, float]:
        if not rubric:
            raise ValueError("Judge rubricは空にできません。")
        if any(value < 0 or value > 100 for value in rubric.values()):
            raise ValueError("各評価値は0〜100にしてください。")
        return rubric


class ClaimCheck(BaseModel):
    claim: str
    status: ClaimUseStatus
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str


class CriticReport(BaseModel):
    critic_report_id: str = Field(default_factory=lambda: identifier("CRT"))
    candidate_id: str
    critic_name: str
    strengths: list[str]
    weaknesses: list[str]
    revision_instructions: list[str]
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    brand_violations: list[str] = Field(default_factory=list)
    platform_violations: list[str] = Field(default_factory=list)
    pass_threshold: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class ModelRun(BaseModel):
    model_run_id: str = Field(default_factory=lambda: identifier("MRUN"))
    campaign_id: str
    content_id: str
    provider: str
    model: str
    task: str
    platform: CreativePlatform
    creative_type: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    latency_ms: int = 0
    judge_score: float | None = None
    real_performance: dict[str, float | int | None] = Field(default_factory=dict)


class VisualBrief(BaseModel):
    visual_brief_id: str = Field(default_factory=lambda: identifier("VBR"))
    content_brief_id: str
    platform: CreativePlatform
    concept: str
    mood: str
    lighting: str
    composition: str
    color_direction: list[str]
    texture: str
    environment: str
    subject: str
    styling: str
    camera: str
    reference_attributes: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)
    headline: str = ""
    cta: str = ""
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)


class ImageCandidate(BaseModel):
    asset_id: str = Field(default_factory=lambda: identifier("AST"))
    visual_brief_id: str
    content_brief_id: str
    provider: str
    model: str
    file_path: str
    mime_type: str
    width: int
    height: int
    prompt: str
    negative_prompt: str
    seed: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    parent_asset_id: str | None = None
    revision_number: int = 0
    is_placeholder: bool = False
    estimated_cost: float | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ObjectiveImageQA(BaseModel):
    asset_id: str
    valid_file: bool
    resolution_ok: bool
    aspect_ratio_ok: bool
    file_size_ok: bool
    safe_area_ok: bool
    text_overflow: bool
    duplicate_hash: str
    issues: list[str] = Field(default_factory=list)
    passed: bool


class VideoShot(BaseModel):
    shot_id: str = Field(default_factory=lambda: identifier("SHOT"))
    scene: int = Field(ge=1)
    order: int = Field(ge=1)
    duration: float = Field(gt=0, le=120)
    purpose: str
    subject: str
    action: str
    location: str
    camera_angle: str
    camera_motion: str
    lens: str
    lighting: str
    composition: str
    reference_images: list[str] = Field(default_factory=list)
    generation_prompt: str
    negative_prompt: str
    dialogue: str = ""
    narration: str = ""
    audio_notes: str = ""
    continuity_notes: str = ""
    transition: str = "cut"


class Storyboard(BaseModel):
    storyboard_id: str = Field(default_factory=lambda: identifier("STB"))
    content_brief_id: str
    platform: CreativePlatform
    title: str
    aspect_ratio: str
    total_duration: float
    shots: list[VideoShot] = Field(min_length=1)
    retention_notes: list[str]
    is_placeholder: bool = False

    @model_validator(mode="after")
    def validate_duration(self) -> Storyboard:
        calculated = round(sum(shot.duration for shot in self.shots), 2)
        if abs(calculated - self.total_duration) > 0.05:
            raise ValueError("Storyboardのtotal_durationとShot合計が一致しません。")
        return self


class CaptionCue(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    position: Literal["top", "center", "bottom"] = "bottom"
    emphasis: list[str] = Field(default_factory=list)
    max_lines: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def valid_timing(self) -> CaptionCue:
        if self.end <= self.start:
            raise ValueError("Caption endはstartより後にしてください。")
        return self


class VisualIdentityProfile(BaseModel):
    identity_id: str = Field(default_factory=lambda: identifier("VID"))
    reference_images: list[str] = Field(default_factory=list)
    subject_description: str
    product_description: str
    wardrobe: str
    environment: str
    colors: list[str]
    forbidden_changes: list[str]


class VideoJob(BaseModel):
    job_id: str = Field(default_factory=lambda: identifier("VJOB"))
    provider: str
    model: str
    status: CreativeStatus = CreativeStatus.QUEUED
    shot_id: str | None = None
    asset_id: str | None = None
    progress: float = Field(default=0, ge=0, le=1)
    error: str = ""
    estimated_cost: float | None = None


class AssetRecord(BaseModel):
    asset_id: str
    campaign_id: str
    content_brief_id: str
    kind: AssetKind
    provider: str
    model: str
    file_path: str
    mime_type: str
    prompt: str = ""
    seed: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    parent_asset_id: str | None = None
    revision_number: int = 0
    score: float | None = None
    is_placeholder: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class CostEntry(BaseModel):
    cost_id: str = Field(default_factory=lambda: identifier("COST"))
    campaign_id: str
    content_id: str
    provider: str
    model: str
    operation: str
    quantity: float = Field(ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    created_at: datetime = Field(default_factory=utcnow)


class ContentPerformance(BaseModel):
    performance_id: str = Field(default_factory=lambda: identifier("PERF"))
    package_id: str
    platform: CreativePlatform
    provider: str = ""
    model: str = ""
    impressions: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    ctr: float | None = Field(default=None, ge=0)
    watch_time: float | None = Field(default=None, ge=0)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    conversions: int | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)
    captured_at: datetime = Field(default_factory=utcnow)


class ContentPackage(BaseModel):
    package_id: str = Field(default_factory=lambda: identifier("PKG"))
    campaign: CampaignPlan
    content_brief: ContentBrief
    final_content: dict[CreativePlatform, ContentCandidate]
    candidates: list[ContentCandidate]
    images: list[ImageCandidate] = Field(default_factory=list)
    storyboards: list[Storyboard] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)
    model_runs: list[ModelRun] = Field(default_factory=list)
    critic_reports: list[CriticReport] = Field(default_factory=list)
    scores: list[CreativeScore] = Field(default_factory=list)
    costs: list[CostEntry] = Field(default_factory=list)
    brand_voice_profile: BrandVoiceProfile
    brand_visual_profile: BrandVisualProfile
    evidence_ids: list[str]
    claims: list[ClaimBinding]
    version: int = 1
    status: CreativeStatus = CreativeStatus.AWAITING_APPROVAL
    degraded_quality_mode: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
    human_approval_required: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
