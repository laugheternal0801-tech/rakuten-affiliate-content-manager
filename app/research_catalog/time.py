from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(UTC)


def to_jst(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(JST)


def format_jst(value: datetime | None) -> str:
    localized = to_jst(value)
    return "未確認" if localized is None else localized.strftime("%Y/%m/%d %H:%M JST")
