from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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

X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


class XConnector(HTTPConnector):
    source = SourceName.X

    def __init__(
        self,
        bearer_token: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, client=client)
        self._bearer_token = bearer_token.strip()

    async def health_check(self) -> ConnectorHealth:
        enabled = bool(self._bearer_token)
        status = AvailabilityStatus.AVAILABLE if enabled else AvailabilityStatus.NOT_CONFIGURED
        capabilities = ConnectorCapabilities(
            source=self.source,
            status=status,
            mode=DataMode.LIVE,
            enabled=enabled,
            historical_search=False,
            recent_search=True,
            metrics=True,
            max_items_per_request=100,
            details={"recent_window_days": 7},
        )
        return ConnectorHealth(
            source=self.source,
            status=status,
            capabilities=capabilities,
            message="X Bearer Token設定済み" if enabled else "X_BEARER_TOKENが未設定です。",
        )

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        if not self._bearer_token:
            return []
        if query.date_to < date.today() - timedelta(days=7):
            return []
        x_query = query.query
        if query.language and "lang:" not in x_query:
            x_query = f"({x_query}) lang:{query.language} -is:retweet"
        params: dict[str, Any] = {
            "query": x_query[:512],
            "max_results": min(100, max(10, query.max_items)),
            "tweet.fields": "created_at,lang,public_metrics,author_id,entities",
            "expansions": "author_id",
            "user.fields": "id,name,username,verified",
        }
        start_date = max(query.date_from, date.today() - timedelta(days=7))
        params["start_time"] = (
            datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        params["end_time"] = (
            datetime.combine(query.date_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )

        collected: list[RawSocialItem] = []
        while len(collected) < query.max_items:
            data = await self._request_json(
                "GET",
                X_RECENT_SEARCH_URL,
                headers={"Authorization": f"Bearer {self._bearer_token}"},
                params=params,
            )
            users = {
                str(user.get("id")): user
                for user in data.get("includes", {}).get("users", [])
                if isinstance(user, dict)
            }
            for post in data.get("data", []):
                if not isinstance(post, dict):
                    continue
                payload = dict(post)
                author = users.get(str(post.get("author_id")), {})
                payload["author"] = author
                post_id = str(post.get("id", ""))
                if not post_id:
                    continue
                collected.append(
                    RawSocialItem(
                        research_run_id=query.research_run_id,
                        platform=self.source,
                        source_id=post_id,
                        source_url=f"https://x.com/i/web/status/{post_id}",
                        query=query.query,
                        raw_payload=payload,
                        data_mode=DataMode.LIVE,
                    )
                )
                if len(collected) >= query.max_items:
                    break
            next_token = data.get("meta", {}).get("next_token")
            if not next_token or len(collected) >= query.max_items:
                break
            params["next_token"] = next_token
        return collected

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        item = generic_normalize(raw, market)
        payload = raw.raw_payload
        metrics = payload.get("public_metrics", {})
        author = payload.get("author", {})
        entities = payload.get("entities", {})
        hashtags = [
            str(entry.get("tag"))
            for entry in entities.get("hashtags", [])
            if isinstance(entry, dict) and entry.get("tag")
        ]
        share_values = [
            optional_int(metrics.get(name)) for name in ("retweet_count", "quote_count")
        ]
        shares = (
            sum(value for value in share_values if value is not None)
            if any(value is not None for value in share_values)
            else None
        )
        return item.model_copy(
            update={
                "author_id": str(payload.get("author_id") or "") or None,
                "author_name": str(author.get("username") or author.get("name") or "") or None,
                "created_at": parse_datetime(payload.get("created_at")),
                "language": str(payload.get("lang") or "") or None,
                "likes": optional_int(metrics.get("like_count")),
                "comments": optional_int(metrics.get("reply_count")),
                "shares": shares,
                "views": optional_int(metrics.get("impression_count")),
                "hashtags": hashtags,
            }
        )
