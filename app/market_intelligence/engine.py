from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import Settings
from app.market_intelligence.agents import CriticAgent, IndependentAgentRunner
from app.market_intelligence.confidence import ConfidenceCalculator
from app.market_intelligence.connectors.registry import SourceRegistry, build_source_registry
from app.market_intelligence.director import RuleBasedResearchDirector, choose_director
from app.market_intelligence.evidence import EvidenceStore
from app.market_intelligence.llm import build_market_llm_registry
from app.market_intelligence.normalization import NormalizationPipeline
from app.market_intelligence.quantitative import compute_quantitative_summary
from app.market_intelligence.reporting import SynthesisAgent
from app.market_intelligence.repositories import (
    create_run,
    save_agent_outputs,
    save_claims,
    save_evidence,
    save_normalized_items,
    save_report,
    save_source_results,
    update_run,
)
from app.market_intelligence.sampling import BalancedSampler
from app.market_intelligence.schemas import (
    AvailabilityStatus,
    ConnectorHealth,
    ResearchDepth,
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    ResearchStatus,
    SourceName,
    StructuredError,
)
from app.market_intelligence.source_orchestrator import SourceOrchestrator
from app.publishing.performance import research_learning_signals
from app.services.ai_council import ProviderRegistry

ProgressHandler = Callable[[ResearchStatus, str, float], None]

DEPTH_SAMPLE_SIZE = {
    ResearchDepth.QUICK: 60,
    ResearchDepth.STANDARD: 200,
    ResearchDepth.DEEP: 600,
}


@dataclass(frozen=True)
class ResearchExecutionResult:
    run_id: str
    status: ResearchStatus
    report: ResearchReport
    plan: ResearchPlan
    warnings: tuple[str, ...] = ()


def _health_snapshot(health: dict[SourceName, ConnectorHealth]) -> dict[str, Any]:
    return {source.value: value.model_dump(mode="json") for source, value in health.items()}


class MarketIntelligenceEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        source_registry: SourceRegistry | None = None,
        provider_registry: ProviderRegistry | None = None,
        provider_models: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._source_registry_override = source_registry
        if provider_registry is None and provider_models is None:
            self._providers, self._provider_models = build_market_llm_registry(settings)
        else:
            self._providers = provider_registry
            self._provider_models = provider_models or {}

    def _emit(
        self,
        callback: ProgressHandler | None,
        status: ResearchStatus,
        message: str,
        fraction: float,
    ) -> None:
        if callback:
            callback(status, message, fraction)

    def run(
        self,
        session: Session,
        request: ResearchRequest,
        on_progress: ProgressHandler | None = None,
    ) -> ResearchExecutionResult:
        run_id = f"MI-{uuid4().hex}"
        registry = self._source_registry_override or build_source_registry(
            self._settings, mock_mode=request.mock_mode
        )
        health = asyncio.run(registry.health_check_all())
        historical_performance_signals = research_learning_signals(
            session,
            request.market,
        )
        capability_snapshot = _health_snapshot(health)
        configuration_snapshot = {
            "providers": self._provider_models,
            "max_concurrency": self._settings.market_intelligence_max_concurrency,
            "max_retries": self._settings.market_intelligence_max_retries,
            "platform_weights": self._settings.market_intelligence_platform_weights,
            "confidence_weights": self._settings.market_intelligence_confidence_weights,
            "mock_mode": request.mock_mode,
            "historical_performance_signals": historical_performance_signals,
        }
        create_run(
            session,
            run_id=run_id,
            request=request,
            capability_snapshot=capability_snapshot,
            configuration_snapshot=configuration_snapshot,
        )
        warnings: list[str] = []

        self._emit(on_progress, ResearchStatus.PLANNING, "調査計画を作成中", 0.08)
        update_run(
            session,
            run_id,
            ResearchStatus.PLANNING,
            progress={"stage": "planning", "message": "調査計画を作成中"},
        )
        provider_lookup = self._providers.get if self._providers is not None else None
        director = (
            RuleBasedResearchDirector()
            if request.mock_mode
            else choose_director(self._provider_models, provider_lookup)
        )
        try:
            plan = director.create_plan(request, historical_performance_signals)
        except Exception as exc:
            warnings.append(
                f"LLM Research Directorを利用できなかったため規則ベースへ縮退: {type(exc).__name__}"
            )
            plan = RuleBasedResearchDirector().create_plan(
                request,
                historical_performance_signals,
            )
        update_run(
            session,
            run_id,
            ResearchStatus.COLLECTING,
            plan=plan,
            progress={"stage": "collecting", "message": "Source別データを並列収集中"},
        )

        self._emit(on_progress, ResearchStatus.COLLECTING, "データを並列収集中", 0.20)
        orchestrator = SourceOrchestrator(
            registry,
            max_concurrency=self._settings.market_intelligence_max_concurrency,
        )
        source_results, _ = asyncio.run(orchestrator.collect(run_id, request, plan))
        save_source_results(session, run_id, plan, source_results)
        raw_count = sum(len(result.items) for result in source_results.values())

        self._emit(
            on_progress,
            ResearchStatus.NORMALIZING,
            f"Raw Data {raw_count}件を正規化中",
            0.43,
        )
        update_run(
            session,
            run_id,
            ResearchStatus.NORMALIZING,
            progress={
                "stage": "normalizing",
                "message": "共通Schemaへ正規化中",
                "raw_items": raw_count,
            },
        )
        connector_normalized = asyncio.run(orchestrator.normalize(request, source_results))
        normalized = NormalizationPipeline().process(
            connector_normalized,
            market=request.market,
            keywords=plan.keywords,
        )
        save_normalized_items(session, normalized)
        sampled = BalancedSampler().sample(
            normalized,
            DEPTH_SAMPLE_SIZE[request.research_depth],
            seed=run_id,
        )

        self._emit(on_progress, ResearchStatus.ANALYZING, "専門Agentを独立実行中", 0.58)
        update_run(
            session,
            run_id,
            ResearchStatus.ANALYZING,
            progress={
                "stage": "analyzing",
                "message": "専門Agentを独立実行中",
                "normalized_items": len(normalized),
                "analysis_sample": len(sampled),
            },
        )
        evidence_store = EvidenceStore()
        confidence = ConfidenceCalculator(self._settings.market_intelligence_confidence_weights)
        runner = IndependentAgentRunner(
            evidence_store,
            confidence,
            provider_registry=None if request.mock_mode else self._providers,
            provider_models={} if request.mock_mode else self._provider_models,
            max_workers=self._settings.market_intelligence_max_concurrency,
        )
        specialist_outputs = runner.run(
            run_id,
            request,
            sampled,
            self._settings.market_intelligence_platform_weights,
        )

        self._emit(on_progress, ResearchStatus.VERIFYING, "CriticがClaimを検証中", 0.76)
        update_run(
            session,
            run_id,
            ResearchStatus.VERIFYING,
            progress={"stage": "verifying", "message": "ClaimとEvidenceを検証中"},
        )
        critic_output = CriticAgent().verify(run_id, specialist_outputs, evidence_store)
        evidence_records = evidence_store.records
        save_evidence(session, evidence_records)
        save_agent_outputs(session, [*specialist_outputs, critic_output])
        save_claims(session, critic_output.claims)

        self._emit(on_progress, ResearchStatus.SYNTHESIZING, "レポートを合成中", 0.89)
        update_run(
            session,
            run_id,
            ResearchStatus.SYNTHESIZING,
            progress={"stage": "synthesizing", "message": "検証済みClaimをレポート化中"},
        )
        quantitative = compute_quantitative_summary(
            normalized,
            request.competitors,
            self._settings.market_intelligence_platform_weights,
        )
        coverage = {source: result.status for source, result in source_results.items()}
        report = SynthesisAgent().synthesize(
            run_id=run_id,
            request=request,
            plan=plan,
            critic_output=critic_output,
            quantitative=quantitative,
            coverage=coverage,
            evidence=evidence_records,
            historical_performance_signals=historical_performance_signals,
        )
        save_report(session, report)

        selected_incomplete = any(
            source_results[source].status is not AvailabilityStatus.AVAILABLE
            for source in request.preferred_sources
        )
        final_status = (
            ResearchStatus.PARTIAL
            if (selected_incomplete or not normalized) and not request.mock_mode
            else ResearchStatus.COMPLETED
        )
        update_run(
            session,
            run_id,
            final_status,
            progress={
                "stage": final_status.value,
                "message": "レポート生成完了",
                "raw_items": raw_count,
                "normalized_items": len(normalized),
                "claims": len(critic_output.claims),
            },
        )
        self._emit(on_progress, final_status, "レポート生成完了", 1.0)
        return ResearchExecutionResult(
            run_id=run_id,
            status=final_status,
            report=report,
            plan=plan,
            warnings=tuple(warnings),
        )


def mark_run_failed(session: Session, run_id: str, exc: Exception) -> None:
    update_run(
        session,
        run_id,
        ResearchStatus.FAILED,
        progress={"stage": "failed", "message": "実行を完了できませんでした"},
        error=StructuredError(
            code="RESEARCH_EXECUTION_FAILED",
            message=f"{type(exc).__name__}: {exc}",
        ),
    )
