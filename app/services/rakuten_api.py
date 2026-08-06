from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.schemas import SearchCriteria

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

RETRYABLE_STATUS_CODES = {429, 500, 503}
ALLOWED_SORTS = {
    "standard",
    "+affiliateRate",
    "-affiliateRate",
    "+reviewCount",
    "-reviewCount",
    "+reviewAverage",
    "-reviewAverage",
    "+itemPrice",
    "-itemPrice",
    "+updateTimestamp",
    "-updateTimestamp",
}


class RakutenAPIError(RuntimeError):
    pass


class RakutenAPIAuthenticationError(RakutenAPIError):
    pass


class RakutenAPIClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.Client(timeout=settings.rakuten_api_timeout_seconds)
        self._sleep = sleep
        self._max_retries = max_retries
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = Lock()
        self._validate_endpoint(str(settings.rakuten_api_endpoint))

    @staticmethod
    def _validate_endpoint(endpoint: str) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or parsed.hostname != "openapi.rakuten.co.jp":
            raise ValueError("楽天APIエンドポイントは公式HTTPSホストのみ指定できます。")

    def search(self, criteria: SearchCriteria) -> dict[str, Any]:
        if not self.settings.rakuten_configured:
            raise RakutenAPIError("楽天API認証情報が未設定です。サンプルモードを利用してください。")
        if criteria.sort not in ALLOWED_SORTS:
            raise RakutenAPIError("並び順の指定が不正です。")

        params: dict[str, Any] = {
            "applicationId": self.settings.rakuten_application_id,
            "accessKey": self.settings.rakuten_access_key,
            "format": "json",
            "formatVersion": 2,
            "hits": criteria.hits,
            "sort": criteria.sort,
            "availability": 1 if criteria.available_only else 0,
            "imageFlag": 1 if criteria.image_only else 0,
        }
        optional = {
            "keyword": criteria.keyword.strip(),
            "genreId": criteria.genre_id,
            "minPrice": criteria.min_price,
            "maxPrice": criteria.max_price,
            "affiliateId": self.settings.rakuten_affiliate_id,
        }
        params.update({key: value for key, value in optional.items() if value not in {None, ""}})
        cache_key = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.settings.rakuten_api_cache_ttl_seconds:
                return cached[1]

        affiliate_id_rejected = False
        try:
            payload = self._request(params)
        except RakutenAPIAuthenticationError:
            if "affiliateId" not in params:
                raise
            fallback_params = {key: value for key, value in params.items() if key != "affiliateId"}
            payload = self._request(fallback_params)
            affiliate_id_rejected = True
        products = [
            self.normalize_item(item) for item in payload.get("Items", payload.get("items", []))
        ]
        products = self._apply_local_filters(products, criteria)
        result = {
            "products": products,
            "count": int(payload.get("count", len(products))),
            "page": int(payload.get("page", 1)),
            "affiliate_id_rejected": affiliate_id_rejected,
        }
        with self._lock:
            self._cache[cache_key] = (now, result)
            if len(self._cache) > 100:
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest, None)
        return result

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        safe_log = {
            key: value
            for key, value in params.items()
            if key not in {"applicationId", "accessKey", "affiliateId"}
        }
        for attempt in range(self._max_retries + 1):
            logger.info("楽天商品検索APIを呼び出します: %s", safe_log)
            try:
                response = self._client.get(str(self.settings.rakuten_api_endpoint), params=params)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise RakutenAPIError(
                        "楽天APIがタイムアウトしました。時間をおいて再試行してください。"
                    ) from exc
                self._sleep(2**attempt)
                continue
            except httpx.HTTPError as exc:
                raise RakutenAPIError(
                    "楽天APIへ接続できませんでした。通信環境を確認してください。"
                ) from exc

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                self._sleep(min(delay, 30))
                continue
            if response.status_code == 429:
                raise RakutenAPIError(
                    "楽天APIの利用回数上限に達しました。しばらく待って再試行してください。"
                )
            if response.status_code in {500, 503}:
                raise RakutenAPIError(
                    "楽天APIが一時的に利用できません。時間をおいて再試行してください。"
                )
            if response.status_code == 403:
                raise RakutenAPIAuthenticationError(
                    "楽天APIの認証に失敗しました（403）。App IDとAccess Keyの組み合わせ、"
                    "アプリの利用許可設定を確認してください。"
                )
            if response.status_code >= 400:
                description = ""
                try:
                    description = str(response.json().get("error_description", ""))
                except (ValueError, AttributeError):
                    pass
                logger.warning("楽天APIエラー status=%s", response.status_code)
                detail = f" {description}" if description else ""
                raise RakutenAPIError(
                    f"楽天APIが検索条件を受け付けませんでした（{response.status_code}）。{detail}"
                )
            try:
                return dict(response.json())
            except ValueError as exc:
                raise RakutenAPIError("楽天APIの応答を読み取れませんでした。") from exc
        raise RakutenAPIError("楽天APIの呼び出しに失敗しました。")

    @staticmethod
    def normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
        item = raw.get("Item", raw.get("item", raw))
        images = item.get("mediumImageUrls") or []
        image_urls = [
            str(image.get("imageUrl", "")) if isinstance(image, dict) else str(image)
            for image in images
        ]
        image_urls = [url for url in image_urls if url.startswith("https://")]

        def parse_datetime(value: Any) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.strptime(str(value), "%Y-%m-%d %H:%M").astimezone()
            except ValueError:
                return None

        return {
            "item_code": str(item.get("itemCode", "")),
            "item_name": str(item.get("itemName", "")),
            "catchcopy": str(item.get("catchcopy", "")),
            "item_price": int(item.get("itemPrice") or 0),
            "item_caption": str(item.get("itemCaption", "")),
            "item_url": str(item.get("itemUrl", "")),
            "affiliate_url": str(item.get("affiliateUrl", "")),
            "availability": int(item.get("availability") or 0),
            "postage_flag": int(item.get("postageFlag") or 1),
            "affiliate_rate": float(item.get("affiliateRate") or 0),
            "review_count": int(item.get("reviewCount") or 0),
            "review_average": float(item.get("reviewAverage") or 0),
            "point_rate": float(item.get("pointRate") or 1),
            "sale_start": parse_datetime(item.get("startTime")),
            "sale_end": parse_datetime(item.get("endTime")),
            "image_url": image_urls[0] if image_urls else "",
            "image_urls": image_urls,
            "shop_name": str(item.get("shopName", "")),
            "shop_code": str(item.get("shopCode", "")),
            "shop_url": str(item.get("shopUrl", "")),
            "shop_affiliate_url": str(item.get("shopAffiliateUrl", "")),
            "genre_id": str(item.get("genreId", "")),
            "fetched_at": datetime.now().astimezone(),
            "is_sample": False,
        }

    @staticmethod
    def _apply_local_filters(
        products: list[dict[str, Any]], criteria: SearchCriteria
    ) -> list[dict[str, Any]]:
        excluded = [term.lower() for term in criteria.excluded_keywords if term.strip()]
        return [
            product
            for product in products
            if int(product["review_count"]) >= criteria.min_review_count
            and float(product["review_average"]) >= criteria.min_review_average
            and float(product["affiliate_rate"]) >= criteria.min_affiliate_rate
            and (not criteria.free_shipping_only or int(product["postage_flag"]) == 0)
            and not any(term in str(product["item_name"]).lower() for term in excluded)
        ]
