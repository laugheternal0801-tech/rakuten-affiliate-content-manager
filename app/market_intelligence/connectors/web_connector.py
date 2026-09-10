from __future__ import annotations

import hashlib
import ipaddress
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree

from app.market_intelligence.connectors.base import ConnectorError, ConnectorQuery, HTTPConnector
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


def is_safe_feed_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _first_text(element: ElementTree.Element, *names: str) -> str:
    expected = set(names)
    for child in element.iter():
        if _local_name(child.tag) in expected and child.text:
            return child.text.strip()
    return ""


def parse_feed(xml_text: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    rows: list[dict[str, str]] = []
    for entry in entries:
        link = _first_text(entry, "link")
        if not link:
            for child in entry.iter():
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        rows.append(
            {
                "title": _first_text(entry, "title"),
                "description": _first_text(entry, "description", "summary", "content"),
                "source_url": link,
                "published_at": _first_text(entry, "pubdate", "published", "updated"),
                "author_name": _first_text(entry, "author", "creator"),
                "source_type": "feed",
            }
        )
    return rows


class WebFeedConnector(HTTPConnector):
    source = SourceName.WEB

    def __init__(
        self,
        feed_urls: tuple[str, ...],
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries, client=client)
        self._feed_urls = tuple(url for url in feed_urls if is_safe_feed_url(url))

    async def health_check(self) -> ConnectorHealth:
        enabled = bool(self._feed_urls)
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
                metrics=False,
                details={"feed_count": len(self._feed_urls), "collection": "RSS/Atom only"},
            ),
            message=(
                f"承認済みRSS/Atomフィード {len(self._feed_urls)}件"
                if enabled
                else "MARKET_INTELLIGENCE_WEB_FEED_URLSが未設定です。"
            ),
        )

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        tokens = [token.casefold() for token in query.query.split() if len(token) >= 2]
        results: list[RawSocialItem] = []
        for feed_url in self._feed_urls:
            try:
                xml_text = await self._request_text(
                    feed_url, headers={"User-Agent": "SNSMarketIntelligence/0.1"}
                )
                rows = parse_feed(xml_text)
            except (ConnectorError, ElementTree.ParseError):
                continue
            for row in rows:
                searchable = f"{row['title']} {row['description']}".casefold()
                if tokens and not any(token in searchable for token in tokens):
                    continue
                url = row["source_url"]
                source_id = hashlib.sha256(f"{feed_url}|{url}|{row['title']}".encode()).hexdigest()[
                    :24
                ]
                payload = {
                    **row,
                    "publisher": urlsplit(feed_url).hostname or "",
                    "feed_url": feed_url,
                }
                results.append(
                    RawSocialItem(
                        research_run_id=query.research_run_id,
                        platform=self.source,
                        source_id=source_id,
                        source_url=url,
                        query=query.query,
                        retrieved_at=datetime.now(UTC),
                        raw_payload=payload,
                        data_mode=DataMode.LIVE,
                    )
                )
                if len(results) >= query.max_items:
                    return results
        return results

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        return generic_normalize(raw, market)
