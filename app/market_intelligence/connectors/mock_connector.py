from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.market_intelligence.connectors.base import ConnectorQuery, SocialSourceConnector
from app.market_intelligence.connectors.common import generic_normalize
from app.market_intelligence.schemas import (
    AvailabilityStatus,
    ConnectorCapabilities,
    ConnectorHealth,
    DataMode,
    RawSocialItem,
    SocialItem,
    SourceName,
)


class MockConnector(SocialSourceConnector):
    """Deterministic development data that is always marked MOCK DATA."""

    def __init__(self, source: SourceName) -> None:
        self.source = source

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(
            source=self.source,
            status=AvailabilityStatus.AVAILABLE,
            capabilities=ConnectorCapabilities(
                source=self.source,
                status=AvailabilityStatus.AVAILABLE,
                mode=DataMode.MOCK,
                enabled=True,
                historical_search=True,
                recent_search=True,
                metrics=True,
                comments=True,
                details={"warning": "MOCK DATA - not collected from the platform"},
            ),
            message="MOCK DATA（実SNSから取得していません）",
        )

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        themes = (
            ("比較時の迷い", "価格差と機能差が分かりにくいという検証用サンプルです。"),
            ("購入障壁", "継続費用と手入れ負担を気にする検証用サンプルです。"),
            ("期待機能", "初心者向け説明とシンプルな選択肢を求める検証用サンプルです。"),
        )
        results: list[RawSocialItem] = []
        for index, (title, text) in enumerate(themes[: query.max_items], start=1):
            source_id = hashlib.sha256(
                f"{self.source.value}|{query.query}|{index}".encode()
            ).hexdigest()[:20]
            results.append(
                RawSocialItem(
                    research_run_id=query.research_run_id,
                    platform=self.source,
                    source_id=f"mock-{source_id}",
                    source_url="",
                    query=query.query,
                    retrieved_at=datetime.now(UTC),
                    raw_payload={
                        "title": f"MOCK DATA: {title}",
                        "text": f"{query.market} / {query.query}: {text}",
                        "created_at": (datetime.now(UTC) - timedelta(days=index * 2)).isoformat(),
                        "language": query.language,
                        "likes": 10 * index,
                        "comments": 3 * index,
                        "shares": index,
                        "views": 100 * index,
                        "author_name": "mock-user",
                    },
                    data_mode=DataMode.MOCK,
                    is_mock=True,
                )
            )
        return results

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        return generic_normalize(raw, market)
