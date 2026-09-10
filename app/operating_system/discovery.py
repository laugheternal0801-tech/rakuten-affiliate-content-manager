from __future__ import annotations

from sqlalchemy.orm import Session

from app.market_intelligence.repositories import get_report, list_runs
from app.operating_system.integrations import opportunity_dimensions_from_research
from app.operating_system.schemas import OpportunityCandidate


def discover_saved_opportunities(
    session: Session,
    *,
    limit_runs: int = 20,
) -> list[OpportunityCandidate]:
    """Find candidates only in saved reports; this function performs no API calls."""

    candidates: list[OpportunityCandidate] = []
    for run in list_runs(session, limit=limit_runs):
        report = get_report(session, run.id)
        if report is None:
            continue
        dimensions = opportunity_dimensions_from_research(session, run.id)
        claims = [
            *(report.opportunities or []),
            *(report.consumer_needs or []),
            *(report.pain_points or []),
            *(report.content_trends or []),
        ]
        seen: set[str] = set()
        for claim in claims:
            normalized = claim.claim.strip().casefold()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            confidence = claim.confidence.score
            evidence_count = len(claim.evidence_ids)
            score = min(
                100.0,
                0.65 * dimensions.model_dump()["demand"]
                + 25 * confidence
                + min(10, evidence_count * 2),
            )
            platforms = ", ".join(platform.value for platform in claim.platforms)
            hidden_niche = (
                f"{run.market} × {platforms}の未充足需要"
                if platforms
                else f"{run.market}の未充足需要"
            )
            candidates.append(
                OpportunityCandidate(
                    candidate_id=f"OSN-{claim.claim_id}",
                    research_run_id=run.id,
                    label=claim.claim[:160],
                    rationale=(
                        f"保存済みClaim（{claim.verification.value}）。"
                        f"Evidence {evidence_count}件、Confidence {confidence:.0%}。"
                    ),
                    hidden_niche=hidden_niche,
                    score=round(score, 1),
                    confidence=confidence,
                    source_claims=[claim.claim],
                    evidence_ids=claim.evidence_ids,
                    dimensions=dimensions,
                )
            )
    return sorted(candidates, key=lambda item: (item.score, item.confidence), reverse=True)
