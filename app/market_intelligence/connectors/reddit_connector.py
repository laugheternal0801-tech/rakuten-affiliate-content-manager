from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from app.market_intelligence.connectors.base import ConnectorQuery, HTTPConnector
from app.market_intelligence.connectors.common import (
    generic_normalize,
    optional_float,
    optional_int,
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

REDDIT_OAUTH_ENDPOINT = "https://www.reddit.com/api/v1/access_token"
REDDIT_SEARCH_URL = "https://oauth.reddit.com/search"


class RedditConnector(HTTPConnector):
    source = SourceName.REDDIT

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, client=client)
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._user_agent = user_agent.strip() or "sns-market-intelligence/0.1"
        self._access_token = ""
        self._token_expires_at = datetime.min.replace(tzinfo=UTC)

    async def health_check(self) -> ConnectorHealth:
        enabled = bool(self._client_id and self._client_secret)
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
                max_items_per_request=100,
            ),
            message="Reddit OAuth設定済み" if enabled else "Reddit OAuth設定が不足しています。",
        )

    async def _token(self) -> str:
        now = datetime.now(UTC)
        if self._access_token and now < self._token_expires_at:
            return self._access_token
        data = await self._request_json(
            "POST",
            REDDIT_OAUTH_ENDPOINT,
            headers={"User-Agent": self._user_agent},
            data={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth(self._client_id, self._client_secret),
        )
        token = str(data.get("access_token", ""))
        if not token:
            raise RuntimeError("Reddit OAuth tokenが返されませんでした。")
        self._access_token = token
        expires_in = max(60, int(data.get("expires_in", 3600)))
        self._token_expires_at = now + timedelta(seconds=expires_in - 30)
        return token

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        if not self._client_id or not self._client_secret:
            return []
        token = await self._token()
        days = max(1, (query.date_to - query.date_from).days)
        time_filter = (
            "week" if days <= 7 else "month" if days <= 31 else "year" if days <= 366 else "all"
        )
        data = await self._request_json(
            "GET",
            REDDIT_SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "User-Agent": self._user_agent},
            params={
                "q": query.query,
                "limit": min(100, query.max_items),
                "sort": "relevance",
                "t": time_filter,
                "type": "link",
                "raw_json": 1,
            },
        )
        results: list[RawSocialItem] = []
        for child in data.get("data", {}).get("children", []):
            payload = child.get("data", {}) if isinstance(child, dict) else {}
            if not isinstance(payload, dict):
                continue
            source_id = str(payload.get("id", ""))
            if not source_id:
                continue
            permalink = str(payload.get("permalink", ""))
            source_url = (
                f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
            )
            results.append(
                RawSocialItem(
                    research_run_id=query.research_run_id,
                    platform=self.source,
                    source_id=source_id,
                    source_url=source_url,
                    query=query.query,
                    raw_payload=payload,
                    data_mode=DataMode.LIVE,
                )
            )
        return results[: query.max_items]

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        item = generic_normalize(raw, market)
        payload = raw.raw_payload
        return item.model_copy(
            update={
                "author_name": str(payload.get("author") or "") or None,
                "created_at": datetime.fromtimestamp(float(payload["created_utc"]), tz=UTC)
                if payload.get("created_utc")
                else None,
                "text": str(payload.get("selftext") or "").strip(),
                "title": str(payload.get("title") or "").strip(),
                "comments": optional_int(payload.get("num_comments")),
                "score": optional_float(payload.get("score")),
                "likes": None,
                "shares": None,
                "views": None,
            }
        )
