from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.market_intelligence.connectors.base import ConnectorQuery, HTTPConnector
from app.market_intelligence.connectors.common import (
    generic_normalize,
    optional_int,
    parse_datetime,
)
from app.market_intelligence.schemas import (
    AvailabilityStatus,
    ConnectorCapabilities,
    ConnectorHealth,
    DataMode,
    RawSocialItem,
    SocialItem,
    SourceName,
)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


class YouTubeConnector(HTTPConnector):
    source = SourceName.YOUTUBE

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, client=client)
        self._api_key = api_key.strip()

    async def health_check(self) -> ConnectorHealth:
        enabled = bool(self._api_key)
        status = AvailabilityStatus.AVAILABLE if enabled else AvailabilityStatus.NOT_CONFIGURED
        return ConnectorHealth(
            source=self.source,
            status=status,
            capabilities=ConnectorCapabilities(
                source=self.source,
                status=status,
                mode=DataMode.LIVE,
                enabled=enabled,
                historical_search=True,
                recent_search=True,
                metrics=True,
                comments=False,
                max_items_per_request=50,
            ),
            message="YouTube Data API設定済み" if enabled else "YOUTUBE_API_KEYが未設定です。",
        )

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        if not self._api_key:
            return []
        search_data = await self._request_json(
            "GET",
            YOUTUBE_SEARCH_URL,
            params={
                "key": self._api_key,
                "part": "snippet",
                "type": "video",
                "q": query.query,
                "maxResults": min(50, query.max_items),
                "relevanceLanguage": query.language,
                "regionCode": str(query.parameters.get("region_code", "JP")),
                "publishedAfter": datetime.combine(query.date_from, datetime.min.time(), tzinfo=UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "publishedBefore": datetime.combine(
                    query.date_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                )
                .isoformat()
                .replace("+00:00", "Z"),
            },
        )
        search_items = [item for item in search_data.get("items", []) if isinstance(item, dict)]
        video_ids = [
            str(item.get("id", {}).get("videoId", ""))
            for item in search_items
            if item.get("id", {}).get("videoId")
        ]
        detail_by_id: dict[str, dict[str, Any]] = {}
        if video_ids:
            details = await self._request_json(
                "GET",
                YOUTUBE_VIDEOS_URL,
                params={
                    "key": self._api_key,
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(video_ids),
                },
            )
            detail_by_id = {
                str(item.get("id")): item
                for item in details.get("items", [])
                if isinstance(item, dict) and item.get("id")
            }
        results: list[RawSocialItem] = []
        for search_item in search_items:
            video_id = str(search_item.get("id", {}).get("videoId", ""))
            if not video_id:
                continue
            payload = {"search": search_item, "detail": detail_by_id.get(video_id, {})}
            results.append(
                RawSocialItem(
                    research_run_id=query.research_run_id,
                    platform=self.source,
                    source_id=video_id,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    query=query.query,
                    raw_payload=payload,
                    data_mode=DataMode.LIVE,
                )
            )
        return results[: query.max_items]

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        payload = raw.raw_payload
        detail = payload.get("detail", {})
        search = payload.get("search", {})
        snippet = detail.get("snippet") or search.get("snippet", {})
        statistics = detail.get("statistics", {})
        flattened = {
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "author_id": snippet.get("channelId"),
            "author_name": snippet.get("channelTitle"),
            "published_at": snippet.get("publishedAt"),
            "language": snippet.get("defaultLanguage") or snippet.get("defaultAudioLanguage"),
            "hashtags": snippet.get("tags", []),
            "like_count": statistics.get("likeCount"),
            "comment_count": statistics.get("commentCount"),
            "view_count": statistics.get("viewCount"),
        }
        base = generic_normalize(raw.model_copy(update={"raw_payload": flattened}), market)
        return base.model_copy(
            update={
                "raw_payload": raw.raw_payload,
                "created_at": parse_datetime(flattened["published_at"]),
                "likes": optional_int(flattened["like_count"]),
                "comments": optional_int(flattened["comment_count"]),
                "views": optional_int(flattened["view_count"]),
                "shares": None,
            }
        )
