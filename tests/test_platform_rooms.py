from types import SimpleNamespace

import pytest

from app.platform_rooms import (
    PLATFORM_ROOMS,
    get_platform_room,
    summarize_platform_contents,
)
from app.services.content_generation import CHANNELS


def test_platform_rooms_cover_every_content_channel() -> None:
    assert set(PLATFORM_ROOMS) == set(CHANNELS)
    assert all(len(room.menu_items) == 4 for room in PLATFORM_ROOMS.values())


def test_platform_summary_only_counts_requested_channel() -> None:
    contents = [
        SimpleNamespace(channel="note", status="review"),
        SimpleNamespace(channel="note", status="scheduled"),
        SimpleNamespace(channel="note", status="published"),
        SimpleNamespace(channel="Pinterest", status="published"),
    ]

    assert summarize_platform_contents(contents, "note") == {
        "total": 3,
        "review": 1,
        "scheduled": 1,
        "published": 1,
    }


def test_unknown_platform_room_is_rejected() -> None:
    with pytest.raises(ValueError, match="未対応のプラットフォーム"):
        get_platform_room("unknown")
