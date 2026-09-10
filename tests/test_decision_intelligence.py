from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.market_intelligence.models import MIEvidence, MIResearchRun
from app.models import Base
from app.operating_system.integrations import decision_evidence_from_research
from app.operating_system.repositories import (
    list_decision_outcomes,
    list_predictions,
    model_performance_summaries,
    save_decision_outcome,
)
from app.operating_system.router import ModelRouter
from app.operating_system.schemas import (
    DecisionOutcome,
    DecisionRequest,
    LatencyRequirement,
    ModelRouteRequest,
    OutcomeStatus,
    ProviderProfile,
    ResearchMode,
    RunBudget,
    TaskType,
)
from app.operating_system.service import OperatingSystemService


def _profile(provider: str, cost: float = 0.01) -> ProviderProfile:
    return ProviderProfile(
        provider=provider,
        model=f"{provider}-model",
        quality=0.85,
        planning_cost_usd=cost,
        typical_latency_ms=100,
        max_context_tokens=100_000,
    )


def test_quick_auto_escalates_but_global_budget_is_clamped() -> None:
    service = OperatingSystemService(
        Settings(
            ai_os_mock_mode=True,
            ai_os_max_run_cost_usd=0.07,
            ai_os_max_model_calls_per_run=2,
        )
    )
    request = DecisionRequest(
        objective="重要な新市場への参入を判断する",
        research_mode=ResearchMode.QUICK,
        decision_importance=0.9,
        uncertainty=0.9,
        budget=RunBudget(max_cost_usd=1, max_api_calls=7),
        allow_external_api=True,
    )

    route = service.preview_route(request)

    assert route.research_mode is ResearchMode.STANDARD
    assert route.requested_research_mode is ResearchMode.QUICK
    assert route.auto_escalated is True
    assert route.estimated_cost_usd == 0
    assert any("Mock Mode" in reason for reason in route.routing_reason)


def test_deep_router_assigns_debate_and_judge_roles_only_in_deep() -> None:
    router = ModelRouter([_profile("a"), _profile("b"), _profile("c")])
    request = ModelRouteRequest(
        task_type=TaskType.DECISION,
        research_mode=ResearchMode.DEEP,
        complexity=0.95,
        decision_importance=0.95,
        uncertainty=0.9,
        potential_downside=0.9,
        reversibility=0.2,
        evidence_score=0.2,
        required_quality=0.9,
        latency_requirement=LatencyRequirement.RELAXED,
        context_size=8_000,
        budget=RunBudget(max_cost_usd=1, max_api_calls=6),
        allow_external_api=True,
    )

    route = router.plan(request)

    assert [step.agent for step in route.steps] == [
        "research_agent",
        "bull_agent",
        "bear_agent",
        "skeptic_agent",
        "red_team_agent",
        "judge_agent",
    ]


def test_prediction_outcome_profit_roi_and_model_track_record() -> None:
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    service = OperatingSystemService(Settings())
    request = DecisionRequest(
        objective="小規模テストを実施する",
        expected_profit=1_000,
        auto_escalation=False,
    )

    with Session(database) as session:
        decision = service.run(session, request)
        saved = save_decision_outcome(
            session,
            DecisionOutcome(
                decision_id=decision.decision_id,
                run_id=decision.run_id,
                status=OutcomeStatus.SUCCESS,
                commission=1_200,
                content_cost=100,
                ad_cost=100,
            ),
        )
        predictions = list_predictions(session, decision.decision_id)
        performance = model_performance_summaries(session)
        outcomes = list_decision_outcomes(session)

    assert saved.total_cost == pytest.approx(200)
    assert saved.net_profit == pytest.approx(1_000)
    assert saved.actual_decision_roi == pytest.approx(5)
    assert predictions[0].actual_value == pytest.approx(1_200)
    assert predictions[0].error == pytest.approx(200)
    assert outcomes[0].decision_id == decision.decision_id
    assert performance[0].calls == 1


def test_saved_market_evidence_is_reused_without_network() -> None:
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    run_id = "MI-SAVED"
    now = datetime.now()
    with Session(database) as session:
        session.add(
            MIResearchRun(
                id=run_id,
                status="completed",
                market="テスト市場",
                request_json={},
                capability_snapshot={},
                configuration_snapshot={},
                progress_json={},
                is_mock=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            MIEvidence(
                evidence_id="EV-SAVED",
                research_run_id=run_id,
                platform="cross_source",
                source_item_ids=["SI-MISSING"],
                claim="複数ソースで需要増加を確認",
                sample_size=120,
                query="需要",
                date_from=date.today().isoformat(),
                date_to=date.today().isoformat(),
                metrics_json={},
                support_score=0.8,
                counter_evidence_ids=[],
                created_at=now,
            )
        )
        session.flush()

        evidence = decision_evidence_from_research(session, run_id)

    assert len(evidence) == 1
    assert evidence[0].evidence_id == "EV-SAVED"
    assert evidence[0].independence_of_sources == 0.9
    assert evidence[0].claim == "複数ソースで需要増加を確認"
