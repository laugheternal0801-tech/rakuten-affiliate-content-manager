from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.market_intelligence.connectors.base import SocialSourceConnector
from app.market_intelligence.connectors.instagram_connector import InstagramConnector
from app.market_intelligence.connectors.manual_connector import ManualImportConnector
from app.market_intelligence.connectors.mock_connector import MockConnector
from app.market_intelligence.connectors.pinterest_connector import PinterestConnector
from app.market_intelligence.connectors.reddit_connector import RedditConnector
from app.market_intelligence.connectors.tiktok_connector import TikTokConnector
from app.market_intelligence.connectors.web_connector import WebFeedConnector
from app.market_intelligence.connectors.x_connector import XConnector
from app.market_intelligence.connectors.youtube_connector import YouTubeConnector
from app.market_intelligence.schemas import (
    ALL_SOURCES,
    AvailabilityStatus,
    ConnectorCapabilities,
    ConnectorHealth,
    DataMode,
    SourceName,
)


class SourceRegistry:
    def __init__(self, connectors: list[SocialSourceConnector]) -> None:
        self._connectors = {connector.source: connector for connector in connectors}
        missing = [source.value for source in ALL_SOURCES if source not in self._connectors]
        if missing:
            raise ValueError(f"Source Registryに不足があります: {', '.join(missing)}")

    @property
    def sources(self) -> tuple[SourceName, ...]:
        return ALL_SOURCES

    def get(self, source: SourceName) -> SocialSourceConnector:
        return self._connectors[source]

    async def health_check_all(self) -> dict[SourceName, ConnectorHealth]:
        async def checked(source: SourceName) -> ConnectorHealth:
            try:
                return await self._connectors[source].health_check()
            except Exception as exc:  # connector isolation boundary
                capabilities = ConnectorCapabilities(
                    source=source,
                    status=AvailabilityStatus.ERROR,
                    mode=DataMode.LIVE,
                    enabled=False,
                    details={"error_type": type(exc).__name__},
                )
                return ConnectorHealth(
                    source=source,
                    status=AvailabilityStatus.ERROR,
                    capabilities=capabilities,
                    message="Capability確認に失敗しました。",
                )

        results = await asyncio.gather(*(checked(source) for source in ALL_SOURCES))
        return {result.source: result for result in results}


def build_source_registry(settings: Settings, *, mock_mode: bool = False) -> SourceRegistry:
    if mock_mode:
        return SourceRegistry([MockConnector(source) for source in ALL_SOURCES])

    import_dir = (
        Path(settings.market_intelligence_manual_import_dir).expanduser().resolve()
        if settings.market_intelligence_manual_import_dir.strip()
        else None
    )
    timeout = settings.market_intelligence_timeout_seconds
    retries = settings.market_intelligence_max_retries

    x: SocialSourceConnector = (
        XConnector(
            settings.x_bearer_token,
            timeout_seconds=timeout,
            max_retries=retries,
        )
        if settings.x_bearer_token
        else ManualImportConnector(SourceName.X, import_dir)
    )
    reddit: SocialSourceConnector = (
        RedditConnector(
            settings.reddit_client_id,
            settings.reddit_client_secret,
            settings.reddit_user_agent,
            timeout_seconds=timeout,
            max_retries=retries,
        )
        if settings.reddit_client_id and settings.reddit_client_secret
        else ManualImportConnector(SourceName.REDDIT, import_dir)
    )
    youtube: SocialSourceConnector = (
        YouTubeConnector(
            settings.youtube_api_key,
            timeout_seconds=timeout,
            max_retries=retries,
        )
        if settings.youtube_api_key
        else ManualImportConnector(SourceName.YOUTUBE, import_dir)
    )
    web: SocialSourceConnector = (
        WebFeedConnector(
            settings.market_intelligence_web_feeds,
            timeout_seconds=timeout,
            max_retries=retries,
        )
        if settings.market_intelligence_web_feeds
        else ManualImportConnector(SourceName.WEB, import_dir)
    )
    return SourceRegistry(
        [
            x,
            reddit,
            youtube,
            TikTokConnector(import_dir),
            InstagramConnector(import_dir),
            PinterestConnector(import_dir),
            web,
        ]
    )
