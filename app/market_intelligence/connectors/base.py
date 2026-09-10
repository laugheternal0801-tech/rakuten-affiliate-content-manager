from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from app.market_intelligence.schemas import (
    AvailabilityStatus,
    ConnectorCapabilities,
    ConnectorHealth,
    DataMode,
    RawSocialItem,
    SocialItem,
    SourceName,
)


@dataclass(frozen=True)
class ConnectorQuery:
    research_run_id: str
    market: str
    query: str
    language: str
    date_from: date
    date_to: date
    max_items: int
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cache_key(self) -> str:
        payload = "|".join(
            (
                self.market,
                self.query,
                self.language,
                self.date_from.isoformat(),
                self.date_to.isoformat(),
                str(self.max_items),
                repr(sorted(self.parameters.items())),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ConnectorError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class SocialSourceConnector(ABC):
    source: SourceName

    @abstractmethod
    async def health_check(self) -> ConnectorHealth:
        """Return configured capabilities without exposing secrets."""

    @abstractmethod
    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        """Collect raw source records without replacing them with AI summaries."""

    async def fetch_details(self, items: list[RawSocialItem]) -> list[RawSocialItem]:
        return items

    @abstractmethod
    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        """Map one source record to the common SocialItem schema."""


Sleep = Callable[[float], Awaitable[None]]


class HTTPConnector(SocialSourceConnector):
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._max_retries = max_retries
        self._sleep = sleep

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        auth: httpx.Auth | None = None,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=dict(headers or {}),
                    params=dict(params or {}),
                    data=dict(data or {}),
                    auth=auth,
                )
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise ConnectorError(
                        "TIMEOUT", "APIが時間内に応答しませんでした。", retryable=True
                    ) from exc
                await self._sleep(min(2**attempt, 4))
                continue
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise ConnectorError(
                        "NETWORK_ERROR", "APIへ接続できませんでした。", retryable=True
                    ) from exc
                await self._sleep(min(2**attempt, 4))
                continue

            if response.status_code == 429:
                if attempt >= self._max_retries:
                    raise ConnectorError(
                        "RATE_LIMITED",
                        "APIのRate Limitに達しました。",
                        retryable=True,
                        status_code=429,
                    )
                retry_after = response.headers.get("Retry-After", "")
                delay = (
                    float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2**attempt
                )
                await self._sleep(min(delay, 10))
                continue
            if response.status_code in {500, 502, 503, 504} and attempt < self._max_retries:
                await self._sleep(min(2**attempt, 4))
                continue
            if response.status_code in {401, 403}:
                raise ConnectorError(
                    "UNAUTHORIZED",
                    "API認証または利用権限を確認してください。",
                    status_code=response.status_code,
                )
            if not response.is_success:
                raise ConnectorError(
                    "API_ERROR",
                    f"APIでHTTP {response.status_code}エラーが発生しました。",
                    retryable=response.status_code >= 500,
                    status_code=response.status_code,
                )
            try:
                result = response.json()
            except ValueError as exc:
                raise ConnectorError("INVALID_RESPONSE", "API応答がJSONではありません。") from exc
            if not isinstance(result, dict):
                raise ConnectorError("INVALID_RESPONSE", "API応答形式が正しくありません。")
            return result
        raise ConnectorError("UNKNOWN", "API呼び出しを完了できませんでした。")

    async def _request_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.get(url, headers=dict(headers or {}))
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt >= self._max_retries:
                    raise ConnectorError(
                        "NETWORK_ERROR", "データソースへ接続できませんでした。", retryable=True
                    ) from exc
                await self._sleep(min(2**attempt, 4))
                continue
            if response.status_code == 429 and attempt < self._max_retries:
                await self._sleep(min(2**attempt, 4))
                continue
            if not response.is_success:
                raise ConnectorError(
                    "API_ERROR",
                    f"データソースでHTTP {response.status_code}エラーが発生しました。",
                    retryable=response.status_code >= 500,
                    status_code=response.status_code,
                )
            return response.text
        raise ConnectorError("UNKNOWN", "データソースを取得できませんでした。")


def unavailable_health(
    source: SourceName,
    *,
    message: str,
    manual_import: bool = False,
) -> ConnectorHealth:
    capabilities = ConnectorCapabilities(
        source=source,
        status=AvailabilityStatus.NOT_CONFIGURED,
        mode=DataMode.MANUAL_IMPORT if manual_import else DataMode.LIVE,
        enabled=False,
        manual_import=manual_import,
    )
    return ConnectorHealth(
        source=source,
        status=AvailabilityStatus.NOT_CONFIGURED,
        capabilities=capabilities,
        message=message,
    )
