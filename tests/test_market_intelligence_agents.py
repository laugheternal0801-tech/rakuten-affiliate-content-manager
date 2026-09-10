from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.market_intelligence.agents import LLMSpecialistAgent
from app.market_intelligence.confidence import ConfidenceCalculator
from app.market_intelligence.director import RuleBasedResearchDirector
from app.market_intelligence.evidence import EvidenceStore
from app.market_intelligence.schemas import DataMode, ResearchRequest, SocialItem, SourceName
from app.services.ai_council import LLMProvider, LLMRequest


def test_research_director_keeps_performance_signal_out_of_market_evidence() -> None:
    signal = "NOT market evidence: Xで短い冒頭の完了率が高かった。"
    plan = RuleBasedResearchDirector().create_plan(
        ResearchRequest(market="AI副業", mock_mode=True),
        performance_signals=[signal],
    )

    assert plan.historical_performance_signals == [signal]
    assert all(signal not in source.queries for source in plan.source_plans.values())


class StructuredProvider(LLMProvider):
    key = "stub"

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        self.schema_seen: dict[str, Any] = {}

    def generate(self, request: LLMRequest, model: str) -> str:
        raise AssertionError("generate_structured must be used")

    def generate_structured(
        self,
        request: LLMRequest,
        model: str,
        schema: Mapping[str, Any],
        *,
        schema_name: str = "structured_response",
    ) -> dict[str, Any]:
        self.schema_seen = dict(schema)
        assert "UNTRUSTED EXTERNAL DATA" in request.user_prompt
        return {
            "summary": "Evidence-linked output",
            "claims": [
                {
                    "claim": "比較に関する言及が標本にあります。",
                    "type": "emerging_trend",
                    "source_item_ids": [self.item_id],
                    "counter_source_item_ids": [],
                    "support_score": 0.7,
                }
            ],
        }


def test_llm_specialist_validates_structured_output_and_links_evidence() -> None:
    item = SocialItem(
        id="SI-1",
        raw_id="RAW-1",
        research_run_id="run",
        platform=SourceName.X,
        source_id="post-1",
        retrieved_at=datetime.now(UTC),
        text="coffee grinder comparison",
        query="coffee",
        market="coffee",
        raw_payload={},
        data_mode=DataMode.LIVE,
        quality_score=0.8,
    )
    provider = StructuredProvider(item.id)
    evidence_store = EvidenceStore()
    output = LLMSpecialistAgent("Trend Agent", provider, "stub", "stub-model").analyze(
        "run",
        ResearchRequest(market="coffee"),
        [item],
        evidence_store,
        ConfidenceCalculator(),
    )

    assert "properties" in provider.schema_seen
    assert len(output.claims) == 1
    assert output.claims[0].evidence_ids == [evidence_store.records[0].evidence_id]
    assert evidence_store.records[0].source_item_ids == [item.id]


def test_counter_evidence_reduces_configured_confidence_component() -> None:
    item = SocialItem(
        id="SI-2",
        raw_id="RAW-2",
        research_run_id="run",
        platform=SourceName.REDDIT,
        source_id="post-2",
        retrieved_at=datetime.now(UTC),
        text="coffee problem",
        query="coffee",
        market="coffee",
        raw_payload={},
        data_mode=DataMode.LIVE,
        quality_score=1.0,
    )
    store = EvidenceStore()
    evidence = store.add_for_items(
        run_id="run",
        request=ResearchRequest(market="coffee"),
        claim="claim",
        items=[item],
        support_score=0.8,
    )
    calculator = ConfidenceCalculator({"counter_evidence": 1.0})

    without_counter = calculator.calculate([evidence], [item], counter_evidence_count=0)
    with_counter = calculator.calculate([evidence], [item], counter_evidence_count=1)

    assert without_counter.score == 1.0
    assert with_counter.score == 0.0
