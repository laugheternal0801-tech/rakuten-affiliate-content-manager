from __future__ import annotations

from datetime import UTC, datetime

from app.market_intelligence.normalization import NormalizationPipeline
from app.market_intelligence.schemas import DataMode, SocialItem, SourceName


def _item(identifier: str, text: str) -> SocialItem:
    return SocialItem(
        id=identifier,
        raw_id=f"raw-{identifier}",
        research_run_id="run",
        platform=SourceName.REDDIT,
        source_id=identifier,
        retrieved_at=datetime.now(UTC),
        text=text,
        query="coffee",
        market="coffee",
        raw_payload={},
        data_mode=DataMode.LIVE,
    )


def test_pipeline_keeps_rows_and_scores_duplicates_and_spam() -> None:
    rows = [
        _item("a", "coffee grinder comparison for beginners"),
        _item("b", "coffee grinder comparison for beginners"),
        _item("c", "coffee 今すぐ 無料登録 https://example.com"),
    ]

    normalized = NormalizationPipeline().process(
        rows, market="coffee grinder", keywords=["comparison"]
    )

    assert len(normalized) == 3
    assert normalized[0].duplicate_group == normalized[1].duplicate_group
    assert normalized[0].duplicate_group is not None
    assert normalized[2].spam_score > 0
    assert normalized[0].relevance_score > 0
