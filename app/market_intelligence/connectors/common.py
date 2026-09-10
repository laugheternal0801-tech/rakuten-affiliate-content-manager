from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from app.market_intelligence.schemas import RawSocialItem, SocialItem

HASHTAG_PATTERN = re.compile(r"(?<!\w)#([\w\-ぁ-んァ-ヶ一-龠々ー]+)", re.UNICODE)


def first_known(payload: dict[str, Any], *keys: str) -> Any:
    """Return the first present value while preserving valid zero metrics."""
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.replace("\x00", " ").split())


def generic_normalize(raw: RawSocialItem, market: str) -> SocialItem:
    payload = raw.raw_payload
    title = normalized_text(payload.get("title"))
    text = normalized_text(payload.get("text") or payload.get("body") or payload.get("description"))
    hashtags = [str(value).lstrip("#") for value in payload.get("hashtags", []) if str(value)]
    hashtags.extend(HASHTAG_PATTERN.findall(f"{title} {text}"))
    return SocialItem(
        raw_id=raw.raw_id,
        research_run_id=raw.research_run_id,
        platform=raw.platform,
        source_id=raw.source_id,
        source_url=raw.source_url,
        author_id=str(payload.get("author_id") or "") or None,
        author_name=str(payload.get("author_name") or payload.get("author") or "") or None,
        created_at=parse_datetime(payload.get("created_at") or payload.get("published_at")),
        retrieved_at=raw.retrieved_at,
        text=text,
        title=title,
        language=str(payload.get("language") or payload.get("lang") or "") or None,
        likes=optional_int(first_known(payload, "likes", "like_count")),
        comments=optional_int(first_known(payload, "comments", "comment_count")),
        shares=optional_int(first_known(payload, "shares", "repost_count")),
        views=optional_int(first_known(payload, "views", "view_count")),
        score=optional_float(payload.get("score")),
        hashtags=list(dict.fromkeys(hashtags)),
        keywords=[],
        query=raw.query,
        market=market,
        raw_payload=payload,
        data_mode=raw.data_mode,
        is_mock=raw.is_mock,
    )
