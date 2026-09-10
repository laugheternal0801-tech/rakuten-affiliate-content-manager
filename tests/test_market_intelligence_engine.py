from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.market_intelligence.engine import MarketIntelligenceEngine
from app.market_intelligence.models import (
    MIClaim,
    MIEvidence,
    MINormalizedSocialItem,
    MIRawSocialItem,
    MIReport,
    MIResearchRun,
)
from app.market_intelligence.repositories import get_evidence, get_items, get_report
from app.market_intelligence.schemas import ResearchDepth, ResearchRequest, ResearchStatus
from app.models import Base


def test_mock_research_runs_end_to_end_and_persists_traceability() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None)
    request = ResearchRequest(
        market="家庭用コーヒー器具",
        seed_keywords=["初心者", "比較"],
        research_depth=ResearchDepth.QUICK,
        max_items_per_source=10,
        mock_mode=True,
    )

    with Session(engine) as session:
        result = MarketIntelligenceEngine(settings).run(session, request)
        session.commit()

        assert result.status is ResearchStatus.COMPLETED
        assert result.report.is_mock is True
        assert "MOCK DATA" in result.report.markdown
        assert session.scalar(select(func.count()).select_from(MIResearchRun)) == 1
        assert session.scalar(select(func.count()).select_from(MIRawSocialItem)) > 0
        assert session.scalar(select(func.count()).select_from(MINormalizedSocialItem)) > 0
        assert session.scalar(select(func.count()).select_from(MIEvidence)) > 0
        assert session.scalar(select(func.count()).select_from(MIClaim)) > 0
        assert session.scalar(select(func.count()).select_from(MIReport)) == 1

        report = get_report(session, result.run_id)
        evidence = get_evidence(session, result.run_id)
        items = get_items(session, result.run_id)
        assert report is not None
        assert report.evidence_ids == [record.evidence_id for record in evidence]
        item_ids = {item.id for item in items}
        assert all(set(record.source_item_ids) <= item_ids for record in evidence)
