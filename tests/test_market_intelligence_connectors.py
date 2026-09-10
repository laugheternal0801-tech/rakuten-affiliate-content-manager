from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx

from app.market_intelligence.connectors.base import (
    ConnectorError,
    ConnectorQuery,
    SocialSourceConnector,
)
from app.market_intelligence.connectors.mock_connector import MockConnector
from app.market_intelligence.connectors.reddit_connector import RedditConnector
from app.market_intelligence.connectors.registry import SourceRegistry
from app.market_intelligence.connectors.x_connector import XConnector
from app.market_intelligence.connectors.youtube_connector import YouTubeConnector
from app.market_intelligence.director import RuleBasedResearchDirector
from app.market_intelligence.schemas import (
    ALL_SOURCES,
    AvailabilityStatus,
    ConnectorCapabilities,
    ConnectorHealth,
    DataMode,
    RawSocialItem,
    ResearchRequest,
    SocialItem,
    SourceName,
)
from app.market_intelligence.source_orchestrator import SourceOrchestrator


def _query(max_items: int = 10) -> ConnectorQuery:
    return ConnectorQuery(
        research_run_id="run",
        market="coffee",
        query="coffee grinder",
        language="en",
        date_from=date.today() - timedelta(days=2),
        date_to=date.today(),
        max_items=max_items,
    )


def test_x_connector_maps_official_recent_search_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/tweets/search/recent"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "123",
                        "text": "coffee grinder",
                        "author_id": "u1",
                        "created_at": "2026-08-24T00:00:00Z",
                        "lang": "en",
                        "public_metrics": {
                            "like_count": 0,
                            "reply_count": 2,
                            "retweet_count": 1,
                            "quote_count": 0,
                        },
                    }
                ],
                "includes": {"users": [{"id": "u1", "username": "maker"}]},
                "meta": {},
            },
        )

    async def scenario() -> tuple[list[RawSocialItem], SocialItem]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = XConnector("secret", client=client, max_retries=0)
            rows = await connector.search(_query())
            return rows, await connector.normalize(rows[0], "coffee")

    raw_rows, normalized = asyncio.run(scenario())
    assert len(raw_rows) == 1
    assert normalized.likes == 0
    assert normalized.comments == 2
    assert normalized.shares == 1
    assert normalized.author_name == "maker"


def test_youtube_connector_fetches_search_then_statistics() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": {"videoId": "v1"},
                            "snippet": {"title": "Coffee", "description": "Review"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "v1",
                        "snippet": {
                            "title": "Coffee",
                            "description": "Review",
                            "publishedAt": "2026-08-20T00:00:00Z",
                        },
                        "statistics": {"viewCount": "100", "likeCount": "0"},
                    }
                ]
            },
        )

    async def scenario() -> SocialItem:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = YouTubeConnector("key", client=client, max_retries=0)
            rows = await connector.search(_query())
            return await connector.normalize(rows[0], "coffee")

    item = asyncio.run(scenario())
    assert calls == ["/youtube/v3/search", "/youtube/v3/videos"]
    assert item.views == 100
    assert item.likes == 0
    assert item.shares is None


def test_reddit_connector_gets_app_token_then_searches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/access_token":
            assert request.headers["User-Agent"] == "market-intelligence-test/1.0"
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(
                200,
                json={"access_token": "reddit-token", "expires_in": 3600},
            )
        assert request.url.path == "/search"
        assert request.headers["Authorization"] == "Bearer reddit-token"
        return httpx.Response(
            200,
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "post-1",
                                "title": "Coffee grinder review",
                                "selftext": "Useful comparison",
                                "author": "reviewer",
                                "permalink": "/r/Coffee/comments/post-1/review/",
                                "created_utc": 1_777_075_200,
                                "num_comments": 3,
                                "score": 12,
                            }
                        }
                    ]
                }
            },
        )

    async def scenario() -> SocialItem:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = RedditConnector(
                "client-id",
                "client-secret",
                "market-intelligence-test/1.0",
                client=client,
                max_retries=0,
            )
            rows = await connector.search(_query())
            return await connector.normalize(rows[0], "coffee")

    item = asyncio.run(scenario())
    assert calls == ["/api/v1/access_token", "/search"]
    assert item.title == "Coffee grinder review"
    assert item.comments == 3
    assert item.score == 12


class FailingConnector(SocialSourceConnector):
    source = SourceName.X

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(
            source=self.source,
            status=AvailabilityStatus.AVAILABLE,
            capabilities=ConnectorCapabilities(
                source=self.source,
                status=AvailabilityStatus.AVAILABLE,
                mode=DataMode.LIVE,
                enabled=True,
            ),
        )

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        raise ConnectorError("RATE_LIMITED", "limited", retryable=True)

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        raise AssertionError("No rows should be normalized")


def test_one_source_failure_does_not_abort_other_sources() -> None:
    connectors: list[SocialSourceConnector] = [
        FailingConnector(),
        *[MockConnector(source) for source in ALL_SOURCES if source is not SourceName.X],
    ]
    registry = SourceRegistry(connectors)
    request = ResearchRequest(
        market="coffee",
        preferred_sources=[SourceName.X, SourceName.REDDIT],
        mock_mode=True,
    )
    plan = RuleBasedResearchDirector().create_plan(request)

    results, _ = asyncio.run(SourceOrchestrator(registry).collect("run", request, plan))

    assert results[SourceName.X].status is AvailabilityStatus.DEGRADED
    assert results[SourceName.X].error is not None
    assert results[SourceName.REDDIT].status is AvailabilityStatus.AVAILABLE
    assert results[SourceName.REDDIT].items
