from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.market_intelligence.connectors.common import generic_normalize
from app.market_intelligence.schemas import (
    ALL_SOURCES,
    DataMode,
    RawSocialItem,
    ResearchRequest,
    SourceName,
)


def test_registry_contract_contains_all_requested_sources() -> None:
    assert [source.value for source in ALL_SOURCES] == [
        "x",
        "reddit",
        "youtube",
        "tiktok",
        "instagram",
        "pinterest",
        "web",
    ]


def test_research_request_rejects_inverted_period() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(
            market="coffee",
            date_from=date(2026, 8, 2),
            date_to=date(2026, 8, 1),
        )


def test_mock_row_must_be_explicitly_marked() -> None:
    with pytest.raises(ValidationError):
        RawSocialItem(
            research_run_id="run",
            platform=SourceName.X,
            source_id="1",
            query="coffee",
            raw_payload={},
            data_mode=DataMode.MOCK,
        )


def test_zero_metric_is_preserved_and_unknown_metric_remains_none() -> None:
    raw = RawSocialItem(
        research_run_id="run",
        platform=SourceName.X,
        source_id="1",
        query="coffee",
        retrieved_at=datetime.now(UTC),
        raw_payload={"text": "coffee", "likes": 0, "comments": None},
        data_mode=DataMode.LIVE,
    )

    item = generic_normalize(raw, "coffee")

    assert item.likes == 0
    assert item.comments is None
