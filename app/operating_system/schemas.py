from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class ResearchMode(StrEnum):
    QUICK = "QUICK"
    STANDARD = "STANDARD"
    DEEP = "DEEP"


class DecisionVerdict(StrEnum):
    GO = "GO"
    NO_GO = "NO_GO"
    INVESTIGATE_MORE = "INVESTIGATE_MORE"


class AnalysisLevel(StrEnum):
    FACT = "FACT"
    CAUSE = "CAUSE"
    HUMAN_INSIGHT = "HUMAN_INSIGHT"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    STRATEGY = "STRATEGY"
    DECISION = "DECISION"
    EXECUTION_PLAN = "EXECUTION_PLAN"


class TaskType(StrEnum):
    DISCOVERY = "discovery"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    DECISION = "decision"
    CONTENT_GENERATION = "content_generation"
    CRITIQUE = "critique"
    SYNTHESIS = "synthesis"


class LatencyRequirement(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    RELAXED = "relaxed"


class RunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFICATION_REQUESTED = "modification_requested"


class EvidenceSupport(StrEnum):
    SUPPORT = "support"
    CONTRADICT = "contradict"
    NEUTRAL = "neutral"


class OutcomeStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE = "FAILURE"
    UNKNOWN = "UNKNOWN"


class OutcomeErrorCause(StrEnum):
    BAD_DATA = "bad_data"
    OLD_DATA = "old_data"
    WRONG_ASSUMPTION = "wrong_assumption"
    UNEXPECTED_EVENT = "unexpected_event"
    MODEL_BIAS = "model_bias"
    EXECUTION_FAILURE = "execution_failure"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    MARKET_CHANGE = "market_change"


class RunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_cost_usd: float = Field(default=0.05, ge=0, le=10_000)
    max_api_calls: int = Field(default=1, ge=0, le=100)
    max_search_calls: int = Field(default=0, ge=0, le=100)
    max_execution_time_seconds: int = Field(default=90, ge=5, le=86_400)

    @classmethod
    def for_mode(cls, mode: ResearchMode) -> RunBudget:
        return {
            ResearchMode.QUICK: cls(
                max_cost_usd=0.05,
                max_api_calls=1,
                max_search_calls=0,
                max_execution_time_seconds=90,
            ),
            ResearchMode.STANDARD: cls(
                max_cost_usd=0.25,
                max_api_calls=3,
                max_search_calls=1,
                max_execution_time_seconds=240,
            ),
            ResearchMode.DEEP: cls(
                max_cost_usd=1.00,
                max_api_calls=7,
                max_search_calls=2,
                max_execution_time_seconds=600,
            ),
        }[mode]


class OpportunityDimensions(BaseModel):
    """Explainable 0-100 inputs. High competition/difficulty/risk reduce the score."""

    model_config = ConfigDict(extra="forbid")

    demand: float = Field(default=50, ge=0, le=100)
    growth: float = Field(default=50, ge=0, le=100)
    competition: float = Field(default=50, ge=0, le=100)
    monetization_potential: float = Field(default=50, ge=0, le=100)
    profit_potential: float = Field(default=50, ge=0, le=100)
    content_opportunity: float = Field(default=50, ge=0, le=100)
    sns_opportunity: float = Field(default=50, ge=0, le=100)
    differentiation_potential: float = Field(default=50, ge=0, le=100)
    entry_difficulty: float = Field(default=50, ge=0, le=100)
    risk: float = Field(default=50, ge=0, le=100)
    confidence: float = Field(default=50, ge=0, le=100)


class DecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(default_factory=lambda: f"OSE-{uuid4().hex[:12].upper()}")
    statement: str = Field(default="", max_length=5_000)
    claim: str = Field(default="", max_length=5_000)
    source: str = Field(min_length=1, max_length=500)
    source_type: str = Field(default="unknown", max_length=100)
    source_url: str = Field(default="", max_length=2_000)
    source_quality: float = Field(default=0.6, ge=0, le=1)
    reliability: float = Field(default=0.6, ge=0, le=1)
    recency: float = Field(default=0.6, ge=0, le=1)
    recency_score: float = Field(default=0.6, ge=0, le=1)
    relevance: float = Field(default=0.6, ge=0, le=1)
    agreement_between_sources: float = Field(default=0.5, ge=0, le=1)
    independence_of_sources: float = Field(default=0.5, ge=0, le=1)
    data_quality: float = Field(default=0.6, ge=0, le=1)
    sample_size: int = Field(default=1, ge=1, le=10_000_000)
    official_source: bool = False
    user_generated_content: bool = False
    support_or_contradict: EvidenceSupport = EvidenceSupport.SUPPORT
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def synchronize_claim_fields(self) -> DecisionEvidence:
        text = self.claim or self.statement
        if not text.strip():
            raise ValueError("Evidenceにはclaimまたはstatementが必要です。")
        self.claim = text
        self.statement = text
        return self


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    objective: str = Field(min_length=1, max_length=2_000)
    market: str = Field(default="", max_length=500)
    background: str = Field(default="", max_length=20_000)
    research_mode: ResearchMode = ResearchMode.QUICK
    decision_importance: float = Field(default=0.5, ge=0, le=1)
    uncertainty: float = Field(default=0.5, ge=0, le=1)
    potential_downside: float = Field(default=0.5, ge=0, le=1)
    reversibility: float = Field(default=0.5, ge=0, le=1)
    urgency: float = Field(default=0.5, ge=0, le=1)
    expected_value: float | None = Field(default=None, ge=0)
    expected_profit: float | None = None
    required_quality: float = Field(default=0.6, ge=0, le=1)
    latency_requirement: LatencyRequirement = LatencyRequirement.NORMAL
    context_size: int = Field(default=8_000, ge=1, le=2_000_000)
    budget: RunBudget = Field(default_factory=RunBudget)
    opportunity: OpportunityDimensions = Field(default_factory=OpportunityDimensions)
    evidence: list[DecisionEvidence] = Field(default_factory=list, max_length=500)
    allow_external_api: bool = False
    auto_escalation: bool = True
    research_run_id: str = Field(default="", max_length=64)


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    quality: float = Field(ge=0, le=1)
    planning_cost_usd: float = Field(ge=0)
    typical_latency_ms: int = Field(ge=0)
    max_context_tokens: int = Field(ge=1)
    supports_web_search: bool = False
    external: bool = True
    healthy: bool = True


class ModelRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    research_mode: ResearchMode
    complexity: float = Field(ge=0, le=1)
    decision_importance: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    potential_downside: float = Field(default=0.5, ge=0, le=1)
    reversibility: float = Field(default=0.5, ge=0, le=1)
    evidence_score: float = Field(default=0.0, ge=0, le=1)
    required_quality: float = Field(ge=0, le=1)
    latency_requirement: LatencyRequirement
    context_size: int = Field(ge=1)
    budget: RunBudget
    allow_external_api: bool = False


class ModelRouteStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    provider: str
    model: str
    max_output_tokens: int = Field(ge=128, le=32_000)
    temperature: float = Field(ge=0, le=2)
    timeout_seconds: float = Field(gt=0, le=600)
    retry_count: int = Field(ge=0, le=3)
    enable_web_search: bool = False
    fallback_providers: list[str] = Field(default_factory=list)
    estimated_cost_usd: float = Field(ge=0)
    optional: bool = False


class ModelRoutePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_mode: ResearchMode
    task_type: TaskType
    steps: list[ModelRouteStep]
    estimated_cost_usd: float = Field(ge=0)
    estimated_api_calls: int = Field(ge=0)
    estimated_search_calls: int = Field(ge=0)
    routing_reason: list[str] = Field(default_factory=list)
    degraded: bool = False
    requested_research_mode: ResearchMode | None = None
    auto_escalated: bool = False


class OpportunityScoreResult(BaseModel):
    score: float = Field(ge=0, le=100)
    evidence_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    contributions: dict[str, float]
    explanations: list[str]


class ModelCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=lambda: f"OSC-{uuid4().hex}")
    run_id: str
    decision_id: str = ""
    task: str
    agent: str
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    actual_cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    success: bool
    cached: bool = False
    fallback: bool = False
    error_type: str = ""
    error_message: str = ""
    timestamp: datetime = Field(default_factory=utcnow)


class ExecutionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_action: str
    owner: str = "user"
    estimated_cost: float | None = Field(default=None, ge=0)
    expected_value: float | None = None
    deadline: datetime | None = None
    dependencies: list[str] = Field(default_factory=list)


class PredictionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction_id: str = Field(default_factory=lambda: f"OSP-{uuid4().hex}")
    decision_id: str
    metric: str
    predicted_value: float | None = None
    predicted_range: tuple[float, float] | None = None
    confidence: float = Field(ge=0, le=1)
    actual_value: float | None = None
    error: float | None = None
    evaluated_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_predicted_range(self) -> PredictionRecord:
        if self.predicted_range and self.predicted_range[0] > self.predicted_range[1]:
            raise ValueError("predicted_rangeの下限は上限以下にしてください。")
        return self


class DecisionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: str = Field(default_factory=lambda: f"OSO-{uuid4().hex}")
    decision_id: str
    run_id: str
    status: OutcomeStatus = OutcomeStatus.UNKNOWN
    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    conversions: int = Field(default=0, ge=0)
    revenue: float = Field(default=0, ge=0)
    commission: float = Field(default=0, ge=0)
    gross_profit: float = 0
    ai_cost: float = Field(default=0, ge=0)
    content_cost: float = Field(default=0, ge=0)
    ad_cost: float = Field(default=0, ge=0)
    platform_cost: float = Field(default=0, ge=0)
    total_cost: float = Field(default=0, ge=0)
    net_profit: float = 0
    decision_roi: float | None = None
    actual_decision_roi: float | None = None
    error_causes: list[OutcomeErrorCause] = Field(default_factory=list)
    actual_result: dict[str, Any] = Field(default_factory=dict)
    lessons: list[str] = Field(default_factory=list)
    recorded_at: datetime = Field(default_factory=utcnow)


class ModelPerformanceSummary(BaseModel):
    provider: str
    model: str
    task_type: str
    calls: int = Field(ge=0)
    decisions: int = Field(ge=0)
    average_cost: float = Field(ge=0)
    average_latency_ms: float = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    prediction_accuracy: float | None = Field(default=None, ge=0, le=1)
    user_approval_rate: float | None = Field(default=None, ge=0, le=1)
    associated_profit: float = 0


class OpportunityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(default_factory=lambda: f"OSN-{uuid4().hex[:12].upper()}")
    research_run_id: str
    label: str
    rationale: str
    hidden_niche: str = ""
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    source_claims: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    dimensions: OpportunityDimensions


class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(default_factory=lambda: f"OSD-{uuid4().hex}")
    decision: DecisionVerdict
    opportunity_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    evidence_score: float = Field(ge=0, le=1)
    research_mode: ResearchMode
    requested_research_mode: ResearchMode | None = None
    summary: str
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    predictions: list[PredictionRecord] = Field(default_factory=list)
    reasons: list[str]
    risks: list[str]
    counter_arguments: list[str]
    recommended_actions: list[str]
    evidence: list[DecisionEvidence]
    sources: list[str]
    models_used: list[str]
    agents_used: list[str] = Field(default_factory=list)
    analysis_levels: dict[AnalysisLevel, str]
    execution_plan: list[ExecutionAction] = Field(default_factory=list)
    disagreement_analysis: list[str] = Field(default_factory=list)
    estimated_value: float | None = None
    estimated_profit: float | None = None
    estimated_ai_cost: float = Field(ge=0)
    ai_cost: float = Field(ge=0)
    api_calls: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    stopped_early: bool = False
    budget_exhausted: bool = False
    requires_human_approval: bool = True
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    expected_outcome: dict[str, Any] | None = None
    actual_outcome: dict[str, Any] | None = None
    decision_roi: float | None = None
    run_id: str
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def investigate_when_confidence_is_very_low(self) -> DecisionOutput:
        if self.confidence < 0.25 and self.decision is not DecisionVerdict.INVESTIGATE_MORE:
            raise ValueError("confidenceが0.25未満の場合は追加調査判断が必要です。")
        return self

    def export_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
