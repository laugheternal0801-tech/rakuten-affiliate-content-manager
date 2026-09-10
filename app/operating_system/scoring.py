from __future__ import annotations

import math

from app.operating_system.schemas import (
    DecisionEvidence,
    DecisionVerdict,
    EvidenceSupport,
    OpportunityDimensions,
    OpportunityScoreResult,
)

WEIGHTS = {
    "demand": 0.13,
    "growth": 0.09,
    "competition": 0.10,
    "monetization_potential": 0.12,
    "profit_potential": 0.13,
    "content_opportunity": 0.08,
    "sns_opportunity": 0.07,
    "differentiation_potential": 0.09,
    "entry_difficulty": 0.06,
    "risk": 0.06,
    "confidence": 0.07,
}


def evidence_score(records: list[DecisionEvidence]) -> float:
    if not records:
        return 0.0
    weighted: list[tuple[float, float]] = []
    for record in records:
        sample_factor = min(1.0, math.log10(record.sample_size + 1) / 2)
        strength = (
            0.20 * record.source_quality
            + 0.15 * record.reliability
            + 0.12 * record.recency_score
            + 0.10 * record.relevance
            + 0.10 * record.agreement_between_sources
            + 0.10 * record.independence_of_sources
            + 0.10 * record.data_quality
            + 0.08 * sample_factor
            + 0.05 * int(record.official_source)
        )
        legacy_strength = (
            0.35 * record.source_quality
            + 0.20 * record.recency
            + 0.20 * record.agreement_between_sources
            + 0.15 * sample_factor
            + 0.10 * int(record.official_source)
        )
        weight = 1.0 + min(4.0, math.log10(record.sample_size + 1))
        weighted.append((min(1.0, max(strength, legacy_strength)), weight))
    return round(sum(value * weight for value, weight in weighted) / sum(w for _, w in weighted), 4)


def score_opportunity(
    dimensions: OpportunityDimensions,
    evidence: list[DecisionEvidence],
) -> OpportunityScoreResult:
    raw = dimensions.model_dump()
    normalized = {
        **raw,
        "competition": 100 - raw["competition"],
        "entry_difficulty": 100 - raw["entry_difficulty"],
        "risk": 100 - raw["risk"],
    }
    contributions = {name: round(normalized[name] * weight, 2) for name, weight in WEIGHTS.items()}
    base_score = sum(contributions.values())
    ev_score = evidence_score(evidence)
    contradict_ratio = (
        sum(item.support_or_contradict is EvidenceSupport.CONTRADICT for item in evidence)
        / len(evidence)
        if evidence
        else 0
    )
    evidence_adjustment = 0.85 + 0.15 * ev_score
    score = round(base_score * evidence_adjustment, 1)
    dimension_confidence = dimensions.confidence / 100
    confidence = round(
        (0.55 * dimension_confidence + 0.45 * ev_score) * (1 - 0.35 * contradict_ratio),
        3,
    )
    largest = sorted(contributions.items(), key=lambda item: item[1], reverse=True)[:3]
    explanations = [f"{name}: 加重点 {value:.1f}" for name, value in largest] + [
        f"Evidence Score {ev_score:.2f} を説明可能性・信頼度へ反映しました。",
        "競争、参入難易度、リスクは値が高いほどOpportunity Scoreを下げます。",
        f"反証Evidence比率 {contradict_ratio:.0%} をConfidenceへ反映しました。",
    ]
    return OpportunityScoreResult(
        score=max(0.0, min(100.0, score)),
        evidence_score=ev_score,
        confidence=max(0.0, min(1.0, confidence)),
        contributions=contributions,
        explanations=explanations,
    )


def decide(score: float, confidence: float) -> DecisionVerdict:
    if confidence < 0.45:
        return DecisionVerdict.INVESTIGATE_MORE
    if score >= 68:
        return DecisionVerdict.GO
    if score < 42 and confidence >= 0.60:
        return DecisionVerdict.NO_GO
    return DecisionVerdict.INVESTIGATE_MORE
