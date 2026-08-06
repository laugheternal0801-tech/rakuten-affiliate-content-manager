from __future__ import annotations

import logging

import httpx
import pytest

from app.config import Settings
from app.schemas import SearchCriteria
from app.services.rakuten_api import RakutenAPIClient, RakutenAPIError


def make_settings() -> Settings:
    return Settings(
        rakuten_application_id="test-application-secret",
        rakuten_access_key="test-access-secret",
        rakuten_affiliate_id="test-affiliate-secret",
        rakuten_api_cache_ttl_seconds=900,
    )


def test_normal_response_is_transformed() -> None:
    payload = {
        "count": 1,
        "Items": [
            {
                "itemName": "テストコーヒー",
                "itemCode": "shop:1",
                "itemPrice": 1200,
                "affiliateUrl": "https://hb.afl.rakuten.co.jp/example",
                "itemUrl": "https://item.rakuten.co.jp/shop/1/",
                "availability": 1,
                "postageFlag": 0,
                "affiliateRate": 5,
                "reviewCount": 100,
                "reviewAverage": 4.5,
                "pointRate": 2,
                "mediumImageUrls": [{"imageUrl": "https://example.invalid/image.jpg"}],
                "shopName": "テスト店",
                "shopCode": "shop",
                "genreId": "1",
            }
        ],
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = RakutenAPIClient(make_settings(), client=httpx.Client(transport=transport))
    result = client.search(SearchCriteria(keyword="コーヒー"))
    assert result["products"][0]["item_code"] == "shop:1"
    assert result["products"][0]["image_url"].startswith("https://")


def test_api_error_has_japanese_message() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error_description": "bad keyword"})
    )
    client = RakutenAPIClient(make_settings(), client=httpx.Client(transport=transport))
    with pytest.raises(RakutenAPIError, match="検索条件"):
        client.search(SearchCriteria(keyword="コーヒー"))


def test_429_uses_backoff_then_succeeds() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "too_many_requests"})
        return httpx.Response(200, json={"Items": [], "count": 0})

    client = RakutenAPIClient(
        make_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    client.search(SearchCriteria(keyword="コーヒー"))
    assert calls == 2
    assert sleeps == [1]


def test_secrets_are_not_written_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"Items": []}))
    client = RakutenAPIClient(make_settings(), client=httpx.Client(transport=transport))
    with caplog.at_level(logging.INFO):
        client.search(SearchCriteria(keyword="コーヒー"))
    log = caplog.text
    assert "test-application-secret" not in log
    assert "test-access-secret" not in log
    assert "test-affiliate-secret" not in log
