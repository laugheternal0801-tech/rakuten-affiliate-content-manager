from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from app.operating_system.schemas import RunBudget


class BudgetAction(StrEnum):
    ALLOW = "allow"
    DOWNGRADE = "downgrade"
    STOP = "stop"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class BudgetDecision:
    action: BudgetAction
    reason: str
    remaining_cost_usd: float
    remaining_api_calls: int


class BudgetManager:
    """Run-scoped guardrail. Dollar values are planning estimates, not invoices."""

    def __init__(self, budget: RunBudget) -> None:
        self.budget = budget
        self.started_at = time.monotonic()
        self.spent_cost_usd = 0.0
        self.api_calls = 0
        self.search_calls = 0
        self.exhausted = False

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_cost_usd(self) -> float:
        return max(0.0, self.budget.max_cost_usd - self.spent_cost_usd)

    @property
    def remaining_api_calls(self) -> int:
        return max(0, self.budget.max_api_calls - self.api_calls)

    def evaluate_call(self, estimated_cost_usd: float, *, external: bool = True) -> BudgetDecision:
        if self.elapsed_seconds >= self.budget.max_execution_time_seconds:
            self.exhausted = True
            return self._decision(BudgetAction.STOP, "最大実行時間に達しました。")
        if external and self.remaining_api_calls <= 0:
            self.exhausted = True
            return self._decision(BudgetAction.STOP, "最大API呼び出し回数に達しました。")
        if estimated_cost_usd > self.remaining_cost_usd:
            if self.remaining_cost_usd > 0:
                return self._decision(
                    BudgetAction.DOWNGRADE,
                    "残予算内の低コストProviderへ切り替える必要があります。",
                )
            self.exhausted = True
            return self._decision(BudgetAction.REQUIRE_APPROVAL, "AI予算上限に達しました。")
        return self._decision(BudgetAction.ALLOW, "予算内です。")

    def record_model_call(
        self,
        estimated_cost_usd: float,
        *,
        external: bool,
        used_search: bool = False,
    ) -> None:
        if external:
            self.api_calls += 1
        self.spent_cost_usd = round(self.spent_cost_usd + estimated_cost_usd, 8)
        if used_search:
            self.search_calls += 1
        if (external and self.api_calls >= self.budget.max_api_calls) or (
            self.budget.max_cost_usd > 0 and self.spent_cost_usd >= self.budget.max_cost_usd
        ):
            self.exhausted = True

    def search_allowed(self) -> bool:
        return self.search_calls < self.budget.max_search_calls

    @staticmethod
    def should_stop_early(
        *,
        confidence: float,
        disagreement: float,
        completed_analyses: int,
        minimum_analyses: int,
        decision_importance: float,
    ) -> bool:
        if completed_analyses < minimum_analyses:
            return False
        confidence_threshold = 0.82 if decision_importance >= 0.8 else 0.72
        return confidence >= confidence_threshold and disagreement <= 0.15

    def _decision(self, action: BudgetAction, reason: str) -> BudgetDecision:
        return BudgetDecision(
            action=action,
            reason=reason,
            remaining_cost_usd=self.remaining_cost_usd,
            remaining_api_calls=self.remaining_api_calls,
        )
