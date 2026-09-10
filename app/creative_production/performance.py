from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.creative_production.schemas import ContentPerformance, CreativePlatform


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class ModelPerformanceSummary:
    provider: str
    model: str
    platform: CreativePlatform
    samples: int
    average_ctr: float | None
    average_completion_rate: float | None
    total_conversions: int
    total_revenue: float


class PerformanceAgent:
    def analyze(self, rows: list[ContentPerformance]) -> list[ModelPerformanceSummary]:
        grouped: dict[tuple[str, str, CreativePlatform], list[ContentPerformance]] = defaultdict(
            list
        )
        for row in rows:
            grouped[(row.provider, row.model, row.platform)].append(row)
        summaries: list[ModelPerformanceSummary] = []
        for (provider, model, platform), values in grouped.items():
            ctr_values = [
                row.ctr if row.ctr is not None else _rate(row.clicks, row.impressions)
                for row in values
            ]
            completion_values = [
                row.completion_rate for row in values if row.completion_rate is not None
            ]
            known_ctr = [value for value in ctr_values if value is not None]
            summaries.append(
                ModelPerformanceSummary(
                    provider=provider,
                    model=model,
                    platform=platform,
                    samples=len(values),
                    average_ctr=(sum(known_ctr) / len(known_ctr) if known_ctr else None),
                    average_completion_rate=(
                        sum(completion_values) / len(completion_values)
                        if completion_values
                        else None
                    ),
                    total_conversions=sum(row.conversions or 0 for row in values),
                    total_revenue=sum(row.revenue or 0 for row in values),
                )
            )
        return sorted(
            summaries,
            key=lambda item: (
                item.average_ctr or 0,
                item.average_completion_rate or 0,
                item.total_conversions,
            ),
            reverse=True,
        )


class RuleBasedModelRouter:
    def select(
        self,
        platform: CreativePlatform,
        performance: list[ModelPerformanceSummary],
        fallback_provider: str,
        fallback_model: str,
    ) -> tuple[str, str, str]:
        eligible = [item for item in performance if item.platform is platform and item.samples >= 3]
        if not eligible:
            return fallback_provider, fallback_model, "実績3件未満のため設定fallback"
        winner = eligible[0]
        return winner.provider, winner.model, "実Performanceを優先"
