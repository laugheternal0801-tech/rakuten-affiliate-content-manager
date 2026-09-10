from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base
from app.operating_system.budget import BudgetAction, BudgetManager
from app.operating_system.models import OSDecisionRow, OSModelCallRow, OSRunRow
from app.operating_system.repositories import list_model_calls
from app.operating_system.router import ModelRouter
from app.operating_system.schemas import (
    DecisionEvidence,
    DecisionRequest,
    DecisionVerdict,
    LatencyRequirement,
    ModelRouteRequest,
    OpportunityDimensions,
    ProviderProfile,
    ResearchMode,
    RunBudget,
    TaskType,
)
from app.operating_system.scoring import decide, score_opportunity
from app.operating_system.service import OperatingSystemService
from app.services.ai_council import LLMProvider, LLMRequest, ProviderError, ProviderRegistry


def _profile(
    provider: str,
    *,
    cost: float,
    quality: float,
    external: bool = True,
) -> ProviderProfile:
    return ProviderProfile(
        provider=provider,
        model=f"{provider}-model",
        quality=quality,
        planning_cost_usd=cost,
        typical_latency_ms=100,
        max_context_tokens=100_000,
        external=external,
    )


def _route_request(mode: ResearchMode, **updates: object) -> ModelRouteRequest:
    values: dict[str, object] = {
        "task_type": TaskType.DECISION,
        "research_mode": mode,
        "complexity": 0.6,
        "decision_importance": 0.5,
        "uncertainty": 0.5,
        "required_quality": 0.6,
        "latency_requirement": LatencyRequirement.NORMAL,
        "context_size": 8_000,
        "budget": RunBudget(max_cost_usd=1, max_api_calls=4),
        "allow_external_api": True,
    }
    values.update(updates)
    return ModelRouteRequest.model_validate(values)


def test_router_uses_one_cheapest_provider_for_quick() -> None:
    router = ModelRouter(
        [
            _profile("openai", cost=0.03, quality=0.9),
            _profile("anthropic", cost=0.04, quality=0.92),
            _profile("gemini", cost=0.01, quality=0.8),
            _profile("demo", cost=0, quality=0.3, external=False),
        ]
    )

    plan = router.plan(_route_request(ResearchMode.QUICK))

    assert [step.provider for step in plan.steps] == ["gemini"]
    assert plan.estimated_api_calls == 1
    assert plan.estimated_cost_usd == 0.01


def test_router_only_adds_standard_verifier_when_warranted() -> None:
    router = ModelRouter(
        [
            _profile("one", cost=0.02, quality=0.85),
            _profile("two", cost=0.03, quality=0.9),
        ]
    )

    ordinary = router.plan(_route_request(ResearchMode.STANDARD))
    important = router.plan(
        _route_request(
            ResearchMode.STANDARD,
            decision_importance=0.8,
            uncertainty=0.7,
        )
    )

    assert len(ordinary.steps) == 1
    assert [step.agent for step in important.steps] == [
        "strategy_agent",
        "verification_agent",
    ]


def test_budget_manager_downgrades_and_early_stops() -> None:
    manager = BudgetManager(RunBudget(max_cost_usd=0.02, max_api_calls=1))

    assert manager.evaluate_call(0.03).action is BudgetAction.DOWNGRADE
    assert manager.evaluate_call(0, external=False).action is BudgetAction.ALLOW
    manager.record_model_call(0.02, external=True)
    assert manager.evaluate_call(0.01).action is BudgetAction.STOP
    assert BudgetManager.should_stop_early(
        confidence=0.85,
        disagreement=0.05,
        completed_analyses=2,
        minimum_analyses=2,
        decision_importance=0.6,
    )


def test_opportunity_score_is_explainable_and_evidence_aware() -> None:
    dimensions = OpportunityDimensions(
        demand=90,
        growth=80,
        competition=20,
        monetization_potential=85,
        content_opportunity=80,
        sns_opportunity=80,
        entry_difficulty=20,
        risk=20,
        confidence=90,
    )
    strong_evidence = [
        DecisionEvidence(
            source="official",
            statement="一次統計",
            source_quality=1,
            recency=1,
            agreement_between_sources=1,
            sample_size=1_000,
            official_source=True,
        )
    ]

    without_evidence = score_opportunity(dimensions, [])
    with_evidence = score_opportunity(dimensions, strong_evidence)

    assert with_evidence.score > without_evidence.score
    assert with_evidence.evidence_score == 1
    assert with_evidence.contributions["competition"] > 0
    assert decide(with_evidence.score, with_evidence.confidence) is DecisionVerdict.GO


class SignalProvider(LLMProvider):
    def __init__(self, key: str, verdict: DecisionVerdict = DecisionVerdict.GO) -> None:
        self.key = key
        self.verdict = verdict
        self.calls = 0

    def generate(self, request: LLMRequest, model: str) -> str:
        self.calls += 1
        return (
            "EvidenceとScoreは整合しています。\n"
            f"DECISION_SIGNAL: {self.verdict.value}\n"
            "CONFIDENCE: 0.95"
        )


class FailingProvider(LLMProvider):
    key = "failing"

    def generate(self, request: LLMRequest, model: str) -> str:
        raise ProviderError("temporary provider failure")


def test_service_falls_back_and_logs_failed_attempt() -> None:
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    backup = SignalProvider("backup", DecisionVerdict.INVESTIGATE_MORE)
    service = OperatingSystemService(
        Settings(ai_os_external_api_enabled=True, ai_os_mock_mode=False),
        registry=ProviderRegistry([FailingProvider(), backup]),
        models={"failing": "failing-model", "backup": "backup-model"},
        profiles=[
            _profile("failing", cost=0.01, quality=0.8),
            _profile("backup", cost=0.02, quality=0.75),
        ],
    )
    request = DecisionRequest(
        objective="Provider障害時も判断を継続する",
        research_mode=ResearchMode.QUICK,
        budget=RunBudget(max_cost_usd=0.1, max_api_calls=2),
        allow_external_api=True,
    )

    with Session(database) as session:
        output = service.run(session, request)
        calls = list_model_calls(session, output.run_id)

    assert [call.success for call in calls] == [False, True]
    assert calls[1].fallback is True
    assert output.ai_cost == 0.03
    assert output.api_calls == 2


def test_service_persists_trace_stops_early_and_reuses_cache() -> None:
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    first = SignalProvider("first")
    second = SignalProvider("second")
    registry = ProviderRegistry([first, second])
    profiles = [
        _profile("first", cost=0.02, quality=0.9),
        _profile("second", cost=0.03, quality=0.88),
    ]
    service = OperatingSystemService(
        Settings(ai_os_external_api_enabled=True, ai_os_mock_mode=False),
        registry=registry,
        models={"first": "first-model", "second": "second-model"},
        profiles=profiles,
    )
    evidence = [
        DecisionEvidence(
            source="official",
            statement="需要と購入意向を確認",
            source_quality=1,
            recency=1,
            agreement_between_sources=1,
            sample_size=1_000,
            official_source=True,
        )
    ]
    request = DecisionRequest(
        objective="小規模な市場テストを開始する",
        research_mode=ResearchMode.STANDARD,
        decision_importance=0.8,
        uncertainty=0.7,
        budget=RunBudget(max_cost_usd=0.2, max_api_calls=2),
        opportunity=OpportunityDimensions(
            demand=90,
            growth=80,
            competition=20,
            monetization_potential=85,
            content_opportunity=80,
            sns_opportunity=80,
            entry_difficulty=20,
            risk=20,
            confidence=95,
        ),
        evidence=evidence,
        allow_external_api=True,
    )

    with Session(database) as session:
        first_output = service.run(session, request)
        session.commit()
        first_calls = list_model_calls(session, first_output.run_id)

        assert first_output.decision is DecisionVerdict.GO
        assert first_output.stopped_early is True
        assert first_output.api_calls == 1
        assert first_output.ai_cost == 0.02
        assert len(first_calls) == 1
        assert first_calls[0].cached is False

        second_output = service.run(session, request)
        session.commit()
        second_calls = list_model_calls(session, second_output.run_id)

        assert second_output.api_calls == 0
        assert second_output.ai_cost == 0
        assert len(second_calls) == 1
        assert second_calls[0].cached is True
        assert first.calls == 1
        assert second.calls == 0
        assert session.scalar(select(func.count()).select_from(OSRunRow)) == 2
        assert session.scalar(select(func.count()).select_from(OSDecisionRow)) == 2
        assert session.scalar(select(func.count()).select_from(OSModelCallRow)) == 2
