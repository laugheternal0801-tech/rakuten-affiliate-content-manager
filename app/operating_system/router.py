from __future__ import annotations

from collections.abc import Sequence

from app.operating_system.schemas import (
    LatencyRequirement,
    ModelRoutePlan,
    ModelRouteRequest,
    ModelRouteStep,
    ProviderProfile,
    ResearchMode,
)


class ModelRouter:
    """Rule-based Phase 1 router with stable interfaces for adaptive routing later."""

    def __init__(self, profiles: Sequence[ProviderProfile]) -> None:
        self._profiles = tuple(profile for profile in profiles if profile.healthy)
        if not self._profiles:
            raise ValueError("利用可能なProvider Profileがありません。")

    @property
    def profiles(self) -> tuple[ProviderProfile, ...]:
        return self._profiles

    def plan(self, request: ModelRouteRequest) -> ModelRoutePlan:
        candidates = [
            profile
            for profile in self._profiles
            if (request.allow_external_api or not profile.external)
            and profile.max_context_tokens >= request.context_size
        ]
        degraded = False
        reasons: list[str] = []
        if not candidates:
            candidates = [profile for profile in self._profiles if not profile.external]
            degraded = True
            reasons.append("条件を満たす外部Providerがないためローカルへ縮退しました。")
        if not candidates:
            raise ValueError("許可条件とContext要件を満たすProviderがありません。")

        local = [profile for profile in candidates if not profile.external]
        external = [profile for profile in candidates if profile.external]
        if not request.allow_external_api:
            selected = local[:1]
            reasons.append("外部APIが未承認のため、ゼロコストProviderだけを選択しました。")
        elif request.research_mode is ResearchMode.QUICK:
            selected = [min(external or local, key=self._quick_rank)]
            reasons.append("QUICKは利用可能なProviderから推定コスト優先で1件だけ選択します。")
        elif request.research_mode is ResearchMode.STANDARD:
            ranked = sorted(external or local, key=self._standard_rank)
            selected = ranked[:1]
            needs_verification = (
                request.uncertainty + request.decision_importance >= 1.25
                or request.required_quality >= 0.8
            )
            if needs_verification and request.budget.max_api_calls >= 2:
                selected.extend(ranked[1:2])
                reasons.append("不確実性または重要度が高いため、独立検証を1件追加しました。")
            else:
                reasons.append("STANDARDですが追加検証の必要条件を満たさず1件に抑えました。")
        else:
            ranked = sorted(external or local, key=self._deep_rank)
            desired_calls = self._deep_call_count(request)
            if ranked:
                selected = [ranked[index % len(ranked)] for index in range(desired_calls)]
            reasons.append(
                "DEEPは重要度・不確実性・損失影響に応じ、"
                f"{desired_calls}役の独立分析・反証・審判を計画しました。"
            )

        selected = self._fit_budget(selected, candidates, request)
        if not selected:
            selected = local[:1]
            degraded = True
            reasons.append("推定予算に収まらないためローカルProviderへ縮退しました。")
        if not selected:
            raise ValueError("Run予算内で実行可能なProviderがありません。")

        token_limit = {
            ResearchMode.QUICK: 800,
            ResearchMode.STANDARD: 1_600,
            ResearchMode.DEEP: 2_800,
        }[request.research_mode]
        timeout = {
            LatencyRequirement.LOW: 45.0,
            LatencyRequirement.NORMAL: 90.0,
            LatencyRequirement.RELAXED: 180.0,
        }[request.latency_requirement]
        roles = self._roles(request.research_mode, len(selected))
        fallback_order = sorted(candidates, key=self._quick_rank)
        steps = [
            ModelRouteStep(
                agent=roles[index],
                provider=profile.provider,
                model=profile.model,
                max_output_tokens=token_limit,
                temperature=0.2 if request.task_type.value != "content_generation" else 0.7,
                timeout_seconds=timeout,
                retry_count=0 if request.research_mode is ResearchMode.QUICK else 1,
                enable_web_search=(
                    request.task_type.value == "research"
                    and profile.supports_web_search
                    and request.budget.max_search_calls > 0
                ),
                fallback_providers=[
                    item.provider for item in fallback_order if item.provider != profile.provider
                ],
                estimated_cost_usd=profile.planning_cost_usd,
                optional=index > 0,
            )
            for index, profile in enumerate(selected)
        ]
        return ModelRoutePlan(
            research_mode=request.research_mode,
            task_type=request.task_type,
            steps=steps,
            estimated_cost_usd=round(sum(step.estimated_cost_usd for step in steps), 8),
            estimated_api_calls=sum(profile.external for profile in selected),
            estimated_search_calls=sum(step.enable_web_search for step in steps),
            routing_reason=reasons,
            degraded=degraded,
        )

    @staticmethod
    def _quick_rank(profile: ProviderProfile) -> tuple[float, float, int]:
        return (profile.planning_cost_usd, -profile.quality, profile.typical_latency_ms)

    @staticmethod
    def _standard_rank(profile: ProviderProfile) -> tuple[float, float, int]:
        value_penalty = profile.planning_cost_usd / max(profile.quality, 0.05)
        return (value_penalty, -profile.quality, profile.typical_latency_ms)

    @staticmethod
    def _deep_rank(profile: ProviderProfile) -> tuple[float, float, int]:
        return (-profile.quality, profile.planning_cost_usd, profile.typical_latency_ms)

    def _fit_budget(
        self,
        selected: list[ProviderProfile],
        candidates: list[ProviderProfile],
        request: ModelRouteRequest,
    ) -> list[ProviderProfile]:
        call_limit = request.budget.max_api_calls
        permitted: list[ProviderProfile] = []
        external_calls = 0
        for profile in selected:
            if profile.external and external_calls >= call_limit:
                continue
            permitted.append(profile)
            external_calls += int(profile.external)
        selected = permitted
        if not selected:
            selected = [profile for profile in candidates if not profile.external][:1]
        while selected and sum(item.planning_cost_usd for item in selected) > (
            request.budget.max_cost_usd + 1e-9
        ):
            if len(selected) > 1:
                selected.pop()
                continue
            affordable = [
                profile
                for profile in candidates
                if profile.planning_cost_usd <= request.budget.max_cost_usd
            ]
            return sorted(affordable, key=self._quick_rank)[:1]
        return selected

    @staticmethod
    def _roles(mode: ResearchMode, count: int) -> list[str]:
        if mode is ResearchMode.QUICK:
            return ["discovery_agent"]
        if mode is ResearchMode.STANDARD:
            return ["strategy_agent", "verification_agent"][:count]
        role_sets = {
            1: ["research_agent"],
            2: ["bull_agent", "bear_agent"],
            3: ["bull_agent", "bear_agent", "judge_agent"],
            4: ["bull_agent", "bear_agent", "skeptic_agent", "judge_agent"],
            5: [
                "research_agent",
                "bull_agent",
                "bear_agent",
                "skeptic_agent",
                "judge_agent",
            ],
            6: [
                "research_agent",
                "bull_agent",
                "bear_agent",
                "skeptic_agent",
                "red_team_agent",
                "judge_agent",
            ],
        }
        return role_sets[min(6, max(1, count))]

    @staticmethod
    def _deep_call_count(request: ModelRouteRequest) -> int:
        desired = 3
        desired += int(request.decision_importance >= 0.75)
        desired += int(request.uncertainty >= 0.70 or request.evidence_score < 0.45)
        desired += int(request.potential_downside >= 0.75 and request.reversibility < 0.5)
        return min(6, max(1, desired, min(3, request.budget.max_api_calls)))
