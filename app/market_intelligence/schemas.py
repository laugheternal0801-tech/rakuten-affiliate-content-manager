from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceName(StrEnum):
    X = "x"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    PINTEREST = "pinterest"
    WEB = "web"


ALL_SOURCES = tuple(SourceName)


class ResearchDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class ResearchStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    COLLECTING = "collecting"
    NORMALIZING = "normalizing"
    ANALYZING = "analyzing"
    VERIFYING = "verifying"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    ERROR = "error"


class DataMode(StrEnum):
    LIVE = "live"
    MANUAL_IMPORT = "manual_import"
    MOCK = "mock"


class ClaimVerification(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTRADICTED = "CONTRADICTED"


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    market: str = Field(min_length=1, max_length=300)
    purpose: str = Field(default="市場機会とリスクを把握する", max_length=1000)
    country: str = Field(default="Japan", max_length=100)
    region: str = Field(default="", max_length=200)
    language: str = Field(default="ja", min_length=2, max_length=20)
    date_from: date = Field(default_factory=lambda: date.today() - timedelta(days=30))
    date_to: date = Field(default_factory=date.today)
    competitors: list[str] = Field(default_factory=list, max_length=50)
    seed_keywords: list[str] = Field(default_factory=list, max_length=100)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=100)
    target_demographic: str = Field(default="", max_length=1000)
    research_depth: ResearchDepth = ResearchDepth.STANDARD
    max_items_per_source: int = Field(default=100, ge=1, le=10_000)
    preferred_sources: list[SourceName] = Field(default_factory=lambda: list(ALL_SOURCES))
    notes: str = Field(default="", max_length=10_000)
    mock_mode: bool = False

    @field_validator("competitors", "seed_keywords", "excluded_keywords")
    @classmethod
    def deduplicate_text_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("preferred_sources")
    @classmethod
    def deduplicate_sources(cls, values: list[SourceName]) -> list[SourceName]:
        unique = list(dict.fromkeys(values))
        if not unique:
            raise ValueError("少なくとも1つの調査ソースが必要です。")
        return unique

    @model_validator(mode="after")
    def validate_period(self) -> ResearchRequest:
        if self.date_from > self.date_to:
            raise ValueError("調査開始日は終了日以前にしてください。")
        if (self.date_to - self.date_from).days > 3_650:
            raise ValueError("調査期間は10年以内にしてください。")
        return self


class SourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceName
    queries: list[str] = Field(min_length=1, max_length=30)
    rationale: str = ""
    target_items: int = Field(default=100, ge=1, le=10_000)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str
    objective: str
    market_definition: str
    research_questions: list[str] = Field(min_length=1, max_length=30)
    hypotheses: list[str] = Field(default_factory=list, max_length=30)
    keywords: list[str] = Field(min_length=1, max_length=100)
    related_keywords: list[str] = Field(default_factory=list, max_length=100)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=100)
    competitors: list[str] = Field(default_factory=list, max_length=50)
    product_candidates: list[str] = Field(default_factory=list, max_length=50)
    pain_point_candidates: list[str] = Field(default_factory=list, max_length=50)
    source_plans: dict[SourceName, SourcePlan]
    required_data_volume: int = Field(ge=1)
    analysis_strategy: list[str] = Field(min_length=1, max_length=30)
    historical_performance_signals: list[str] = Field(default_factory=list, max_length=30)
    prompt_version: str = "research-director-v1"


class ConnectorCapabilities(BaseModel):
    source: SourceName
    status: AvailabilityStatus
    mode: DataMode
    enabled: bool
    historical_search: bool = False
    recent_search: bool = False
    metrics: bool = False
    comments: bool = False
    manual_import: bool = False
    max_items_per_request: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ConnectorHealth(BaseModel):
    source: SourceName
    status: AvailabilityStatus
    checked_at: datetime = Field(default_factory=utcnow)
    capabilities: ConnectorCapabilities
    message: str = ""


class StructuredError(BaseModel):
    code: str
    message: str
    source: SourceName | None = None
    retryable: bool = False
    occurred_at: datetime = Field(default_factory=utcnow)
    details: dict[str, Any] = Field(default_factory=dict)


class RawSocialItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_id: str = Field(default_factory=lambda: f"RAW-{uuid4().hex}")
    research_run_id: str
    platform: SourceName
    source_id: str
    source_url: str = ""
    query: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    raw_payload: dict[str, Any]
    data_mode: DataMode
    is_mock: bool = False

    @model_validator(mode="after")
    def mock_mode_is_explicit(self) -> RawSocialItem:
        if self.data_mode is DataMode.MOCK and not self.is_mock:
            raise ValueError("MOCKデータにはis_mock=trueが必要です。")
        if self.is_mock and self.data_mode is not DataMode.MOCK:
            raise ValueError("is_mock=trueのデータはdata_mode=mockである必要があります。")
        return self


class SocialItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"SI-{uuid4().hex}")
    raw_id: str
    research_run_id: str
    platform: SourceName
    source_id: str
    source_url: str = ""
    author_id: str | None = None
    author_name: str | None = None
    created_at: datetime | None = None
    retrieved_at: datetime
    text: str = ""
    title: str = ""
    language: str | None = None
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    score: float | None = None
    hashtags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    query: str
    market: str
    raw_payload: dict[str, Any]
    data_mode: DataMode
    is_mock: bool = False
    spam_score: float = Field(default=0.0, ge=0, le=1)
    bot_score: float = Field(default=0.0, ge=0, le=1)
    relevance_score: float = Field(default=0.0, ge=0, le=1)
    quality_score: float = Field(default=0.0, ge=0, le=1)
    duplicate_group: str | None = None
    advertisement_likelihood: float = Field(default=0.0, ge=0, le=1)

    @property
    def engagement(self) -> int:
        return sum(value or 0 for value in (self.likes, self.comments, self.shares))


class SourceRunResult(BaseModel):
    source: SourceName
    status: AvailabilityStatus
    items: list[RawSocialItem] = Field(default_factory=list)
    queries_attempted: int = 0
    duration_ms: int = 0
    from_cache: bool = False
    error: StructuredError | None = None


class EvidenceRecord(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"EV-{uuid4().hex[:12].upper()}")
    research_run_id: str
    platform: SourceName | Literal["cross_source"]
    source_item_ids: list[str] = Field(min_length=1)
    claim: str
    sample_size: int = Field(ge=1)
    query: str
    date_from: date
    date_to: date
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    support_score: float = Field(ge=0, le=1)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class ConfidenceBreakdown(BaseModel):
    evidence_volume: float = Field(ge=0, le=1)
    source_diversity: float = Field(ge=0, le=1)
    platform_diversity: float = Field(ge=0, le=1)
    temporal_consistency: float = Field(ge=0, le=1)
    model_agreement: float = Field(ge=0, le=1)
    data_quality: float = Field(ge=0, le=1)
    counter_evidence: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)
    label: Literal["High", "Medium", "Low"]


class Claim(BaseModel):
    claim_id: str = Field(default_factory=lambda: f"CL-{uuid4().hex[:12].upper()}")
    research_run_id: str
    claim: str
    type: str
    confidence: ConfidenceBreakdown
    evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    platforms: list[SourceName] = Field(default_factory=list)
    verification: ClaimVerification = ClaimVerification.INSUFFICIENT_EVIDENCE
    agent_name: str


class AgentOutput(BaseModel):
    agent_run_id: str = Field(default_factory=lambda: f"AG-{uuid4().hex}")
    research_run_id: str
    agent_name: str
    provider: str
    model: str
    prompt_version: str
    claims: list[Claim] = Field(default_factory=list)
    summary: str = ""
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    error: StructuredError | None = None


class QuantitativeSummary(BaseModel):
    total_items: int
    platform_counts: dict[SourceName, int]
    total_engagement: int
    keyword_frequency: dict[str, int]
    competitor_mentions: dict[str, int]
    platform_distribution: dict[SourceName, float]
    daily_mentions: dict[str, int]
    source_quality_average: float


class ResearchReport(BaseModel):
    research_run_id: str
    title: str
    executive_summary: str
    scope: dict[str, Any]
    source_coverage: dict[SourceName, AvailabilityStatus]
    sample_size: int
    key_findings: list[Claim]
    emerging_trends: list[Claim]
    consumer_needs: list[Claim]
    pain_points: list[Claim]
    competitor_landscape: list[Claim]
    product_mentions: list[Claim] = Field(default_factory=list)
    content_trends: list[Claim]
    opportunities: list[Claim]
    platform_analysis: dict[SourceName, dict[str, Any]] = Field(default_factory=dict)
    confidence_summary: dict[str, float | int | str] = Field(default_factory=dict)
    risks: list[str]
    counter_evidence: list[str]
    methodology: list[str]
    data_limitations: list[str]
    quantitative: QuantitativeSummary
    evidence_ids: list[str]
    historical_performance_signals: list[str] = Field(default_factory=list)
    is_mock: bool = False
    generated_at: datetime = Field(default_factory=utcnow)
    markdown: str = ""
