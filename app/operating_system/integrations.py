from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.market_intelligence.repositories import get_evidence, get_items, get_report
from app.market_intelligence.schemas import SourceName
from app.operating_system.schemas import (
    DecisionEvidence,
    EvidenceSupport,
    OpportunityDimensions,
)


def decision_evidence_from_research(
    session: Session,
    research_run_id: str,
) -> list[DecisionEvidence]:
    """Convert persisted Market Intelligence evidence without another API call."""

    evidence_records = get_evidence(session, research_run_id)
    items = get_items(session, research_run_id)
    item_by_id = {item.id: item for item in items}
    converted: list[DecisionEvidence] = []
    now = datetime.now(UTC)
    for record in evidence_records:
        linked = [
            item_by_id[item_id] for item_id in record.source_item_ids if item_id in item_by_id
        ]
        quality = (
            sum(item.quality_score for item in linked) / len(linked)
            if linked
            else record.support_score
        )
        relevance = (
            sum(item.relevance_score for item in linked) / len(linked)
            if linked
            else record.support_score
        )
        source_url = next((item.source_url for item in linked if item.source_url), "")
        age_days = max(0, (now.date() - record.date_to).days)
        recency = max(0.0, 1 - age_days / 365)
        platform = (
            record.platform.value if isinstance(record.platform, SourceName) else record.platform
        )
        converted.append(
            DecisionEvidence(
                evidence_id=record.evidence_id,
                claim=record.claim,
                source=f"SNS市場調査/{platform}",
                source_type=platform,
                source_url=source_url,
                source_quality=max(0.0, min(1.0, quality)),
                reliability=record.support_score,
                recency=recency,
                recency_score=recency,
                relevance=max(0.0, min(1.0, relevance)),
                agreement_between_sources=record.support_score,
                independence_of_sources=0.9 if platform == "cross_source" else 0.6,
                data_quality=max(0.0, min(1.0, quality)),
                sample_size=record.sample_size,
                user_generated_content=platform != "web",
                support_or_contradict=(
                    EvidenceSupport.CONTRADICT
                    if record.support_score < 0.35
                    else EvidenceSupport.SUPPORT
                ),
                published_at=datetime.combine(record.date_to, datetime.min.time(), UTC),
                retrieved_at=record.created_at,
                observed_at=datetime.combine(record.date_to, datetime.min.time(), UTC),
            )
        )
    return converted


def opportunity_dimensions_from_research(
    session: Session,
    research_run_id: str,
) -> OpportunityDimensions:
    """Build explainable heuristic inputs from one persisted report."""

    report = get_report(session, research_run_id)
    if report is None:
        raise LookupError(f"Research report {research_run_id} が見つかりません。")
    sample_signal = min(100.0, 20 + 18 * math.log10(report.sample_size + 1))
    confidence = float(report.confidence_summary.get("score", 0.5)) * 100
    if confidence <= 1:
        confidence *= 100
    opportunities = len(report.opportunities)
    trends = len(report.emerging_trends)
    competitors = len(report.competitor_landscape)
    needs = len(report.consumer_needs) + len(report.pain_points)
    return OpportunityDimensions(
        demand=sample_signal,
        growth=min(100, 40 + trends * 8),
        competition=min(100, 35 + competitors * 8),
        monetization_potential=min(100, 40 + opportunities * 8),
        profit_potential=min(100, 35 + opportunities * 7),
        content_opportunity=min(100, 40 + len(report.content_trends) * 8),
        sns_opportunity=min(100, 45 + len(report.source_coverage) * 5),
        differentiation_potential=min(100, 35 + needs * 5),
        entry_difficulty=min(100, 35 + competitors * 5),
        risk=min(100, 30 + len(report.risks) * 7 + len(report.data_limitations) * 4),
        confidence=max(0, min(100, confidence)),
    )
