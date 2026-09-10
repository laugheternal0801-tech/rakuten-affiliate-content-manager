from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.operating_system.budget import BudgetAction, BudgetManager
from app.operating_system.repositories import (
    get_cached_response,
    save_cached_response,
    save_model_call,
)
from app.operating_system.schemas import (
    ModelCallRecord,
    ModelRouteStep,
    ProviderProfile,
    TaskType,
)
from app.services.ai_council import LLMRequest, ProviderError, ProviderRegistry


class GatewayError(RuntimeError):
    """Raised when routing, budget, primary, and fallback attempts cannot complete."""


@dataclass(frozen=True)
class GatewayResult:
    content: str
    record: ModelCallRecord


class AIGateway:
    def __init__(
        self,
        registry: ProviderRegistry,
        profiles: list[ProviderProfile],
        *,
        cache_ttl_seconds: int = 86_400,
    ) -> None:
        self._registry = registry
        self._profiles = {profile.provider: profile for profile in profiles}
        self._cache_ttl_seconds = cache_ttl_seconds

    def execute(
        self,
        session: Session,
        *,
        run_id: str,
        decision_id: str = "",
        task: TaskType,
        step: ModelRouteStep,
        request: LLMRequest,
        budget: BudgetManager,
    ) -> GatewayResult:
        providers = [step.provider, *step.fallback_providers]
        failures: list[str] = []
        for index, provider_name in enumerate(providers):
            profile = self._profiles.get(provider_name)
            if profile is None:
                continue
            model = profile.model
            cache_key, content_hash = self._cache_key(provider_name, model, request)
            retry_count = step.retry_count if index == 0 else 0
            for attempt in range(retry_count + 1):
                budget_decision = budget.evaluate_call(
                    profile.planning_cost_usd, external=profile.external
                )
                if budget_decision.action is BudgetAction.DOWNGRADE:
                    failures.append(f"{provider_name}: 残予算を超えるためskip")
                    break
                if budget_decision.action in {
                    BudgetAction.STOP,
                    BudgetAction.REQUIRE_APPROVAL,
                }:
                    failures.append(f"{provider_name}: {budget_decision.reason}")
                    if budget.elapsed_seconds >= budget.budget.max_execution_time_seconds:
                        raise GatewayError(budget_decision.reason)
                    break

                cached = get_cached_response(session, cache_key)
                if cached is not None:
                    record = ModelCallRecord(
                        run_id=run_id,
                        decision_id=decision_id,
                        task=task.value,
                        agent=step.agent,
                        provider=provider_name,
                        model=model,
                        input_tokens=self._estimate_tokens(
                            f"{request.system_prompt}\n{request.user_prompt}"
                        ),
                        output_tokens=self._estimate_tokens(cached),
                        estimated_cost_usd=0,
                        latency_ms=0,
                        success=True,
                        cached=True,
                        fallback=index > 0,
                    )
                    save_model_call(session, record)
                    return GatewayResult(content=cached, record=record)

                started = time.perf_counter()
                try:
                    content = self._registry.get(provider_name).generate(request, model)
                    latency_ms = int((time.perf_counter() - started) * 1_000)
                    record = ModelCallRecord(
                        run_id=run_id,
                        decision_id=decision_id,
                        task=task.value,
                        agent=step.agent,
                        provider=provider_name,
                        model=model,
                        input_tokens=self._estimate_tokens(
                            f"{request.system_prompt}\n{request.user_prompt}"
                        ),
                        output_tokens=self._estimate_tokens(content),
                        estimated_cost_usd=profile.planning_cost_usd,
                        latency_ms=latency_ms,
                        success=True,
                        fallback=index > 0,
                    )
                    budget.record_model_call(
                        profile.planning_cost_usd,
                        external=profile.external,
                        used_search=request.enable_web_search,
                    )
                    save_model_call(session, record)
                    save_cached_response(
                        session,
                        key=cache_key,
                        provider=provider_name,
                        model=model,
                        response_text=content,
                        content_hash=content_hash,
                        ttl_seconds=self._cache_ttl_seconds,
                    )
                    return GatewayResult(content=content, record=record)
                except Exception as exc:
                    latency_ms = int((time.perf_counter() - started) * 1_000)
                    budget.record_model_call(
                        profile.planning_cost_usd,
                        external=profile.external,
                        used_search=request.enable_web_search,
                    )
                    record = ModelCallRecord(
                        run_id=run_id,
                        decision_id=decision_id,
                        task=task.value,
                        agent=step.agent,
                        provider=provider_name,
                        model=model,
                        input_tokens=self._estimate_tokens(
                            f"{request.system_prompt}\n{request.user_prompt}"
                        ),
                        output_tokens=0,
                        estimated_cost_usd=profile.planning_cost_usd,
                        latency_ms=latency_ms,
                        success=False,
                        fallback=index > 0,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    save_model_call(session, record)
                    failures.append(f"{provider_name} attempt {attempt + 1}: {type(exc).__name__}")
                    if not isinstance(exc, (ProviderError, RuntimeError)):
                        break
        raise GatewayError(" / ".join(failures) or "利用可能なProviderがありません。")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    @staticmethod
    def _cache_key(
        provider: str,
        model: str,
        request: LLMRequest,
    ) -> tuple[str, str]:
        content = "\n".join(
            [
                provider,
                model,
                request.system_prompt,
                request.user_prompt,
                str(request.max_output_tokens),
                str(request.enable_web_search),
            ]
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return digest, digest
