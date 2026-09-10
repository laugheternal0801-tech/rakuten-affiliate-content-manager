from __future__ import annotations

from collections import Counter
from typing import Literal

from app.market_intelligence.schemas import ConfidenceBreakdown, EvidenceRecord, SocialItem

DEFAULT_WEIGHTS = {
    "evidence_volume": 0.20,
    "source_diversity": 0.15,
    "platform_diversity": 0.15,
    "temporal_consistency": 0.10,
    "model_agreement": 0.15,
    "data_quality": 0.20,
    "counter_evidence": 0.05,
}


class ConfidenceCalculator:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        candidate = weights or DEFAULT_WEIGHTS
        total = sum(max(0.0, value) for value in candidate.values()) or 1.0
        self._weights = {key: max(0.0, candidate.get(key, 0.0)) / total for key in DEFAULT_WEIGHTS}

    def calculate(
        self,
        evidence: list[EvidenceRecord],
        source_items: list[SocialItem],
        *,
        model_agreement: float = 0.5,
        counter_evidence_count: int = 0,
    ) -> ConfidenceBreakdown:
        evidence_volume = min(1.0, sum(item.sample_size for item in evidence) / 50)
        platforms = {item.platform for item in source_items}
        source_diversity = min(1.0, len(platforms) / 4)
        platform_diversity = min(1.0, len(platforms) / 7)
        dates = Counter(
            (item.created_at or item.retrieved_at).date().isoformat() for item in source_items
        )
        temporal_consistency = min(1.0, len(dates) / 5)
        data_quality = (
            sum(item.quality_score for item in source_items) / len(source_items)
            if source_items
            else 0.0
        )
        counter_evidence = 1 - min(1.0, counter_evidence_count / max(1, len(evidence)))
        values = {
            "evidence_volume": evidence_volume,
            "source_diversity": source_diversity,
            "platform_diversity": platform_diversity,
            "temporal_consistency": temporal_consistency,
            "model_agreement": max(0.0, min(1.0, model_agreement)),
            "data_quality": data_quality,
            "counter_evidence": counter_evidence,
        }
        score = sum(values[key] * self._weights[key] for key in DEFAULT_WEIGHTS)
        score = round(max(0.0, min(1.0, score)), 4)
        label: Literal["High", "Medium", "Low"] = (
            "High" if score >= 0.75 else "Medium" if score >= 0.45 else "Low"
        )
        return ConfidenceBreakdown(
            **{key: round(value, 4) for key, value in values.items()},
            score=score,
            label=label,
        )
