from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.publishing.publishers.base import PublisherError
from app.publishing.publishers.x_publisher import XPublisher
from app.publishing.schemas import (
    CapabilityAvailability,
    GeneratedMediaDisclosure,
    PublishingPlatform,
    PublishingStatus,
    PublishPayload,
    SocialAccountConnection,
)
from app.publishing.secret_store import MemorySecretStore


def _payload(*, dry_run: bool) -> PublishPayload:
    return PublishPayload(
        job_id="JOB-x",
        snapshot_id="SNP-x",
        campaign_id="CAM-x",
        platform=PublishingPlatform.X,
        target_account_id="ACC-x",
        credential_reference="memory://credentials/x",
        idempotency_key="a" * 64,
        text="承認済みの本文をそのまま投稿します。",
        disclosure=GeneratedMediaDisclosure(ai_generated=True),
        dry_run=dry_run,
    )


def test_x_dry_run_uses_text_only_official_shape_without_network() -> None:
    provider = XPublisher(
        dry_run=True,
        publishing_enabled=False,
        external_api_enabled=False,
    )

    result = asyncio.run(provider.publish(_payload(dry_run=True)))

    assert result.status is PublishingStatus.DRY_RUN_COMPLETED
    assert result.remote_post_id is None
    capability = asyncio.run(provider.get_capabilities())
    assert capability.availability is CapabilityAvailability.DRY_RUN
    assert capability.operations == ["text_post"]
    assert capability.constraints["media_upload_connected"] is False


def test_x_live_text_post_uses_user_token_and_made_with_ai() -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/x", "user-access-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/tweets"
        assert request.headers["Authorization"] == "Bearer user-access-token"
        body = json.loads(request.content)
        assert body == {
            "text": "承認済みの本文をそのまま投稿します。",
            "made_with_ai": True,
        }
        assert "user-access-token" not in request.content.decode("utf-8")
        return httpx.Response(
            201,
            headers={"x-request-id": "request-x-1"},
            json={"data": {"id": "1234567890", "text": body["text"]}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = XPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            result = await provider.publish(_payload(dry_run=False))
            assert result.status is PublishingStatus.PUBLISHED
            assert result.remote_post_id == "1234567890"
            assert result.remote_url == "https://x.com/i/web/status/1234567890"
            assert result.request_id == "request-x-1"

    asyncio.run(scenario())


def test_x_validates_authenticated_user_identity() -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/x", "user-access-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/users/me"
        return httpx.Response(200, json={"data": {"id": "42", "username": "brand_jp"}})

    async def scenario() -> tuple[bool, bool]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = XPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            base = {
                "platform": PublishingPlatform.X,
                "display_name": "X brand",
                "status": CapabilityAvailability.AVAILABLE,
                "scopes": ["tweet.read", "tweet.write", "users.read"],
                "credential_reference": "memory://credentials/x",
            }
            valid = SocialAccountConnection(account_id="@brand_jp", **base)
            wrong = SocialAccountConnection(account_id="different", **base)
            return await provider.validate_credentials(valid), await provider.validate_credentials(
                wrong
            )

    assert asyncio.run(scenario()) == (True, False)


def test_x_rejects_media_and_retries_rate_limits() -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/x", "user-access-token")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(429, json={}))
        ) as client:
            provider = XPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(dry_run=False))
            assert captured.value.code == "X_RATE_LIMITED"
            assert captured.value.retryable is True

    asyncio.run(scenario())


def test_x_server_error_is_an_unknown_outcome_not_an_automatic_retry() -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/x", "user-access-token")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(503, json={}))
        ) as client:
            provider = XPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(dry_run=False))
            assert captured.value.code == "X_UNAVAILABLE"
            assert captured.value.retryable is True
            assert captured.value.outcome_unknown is True

    asyncio.run(scenario())
