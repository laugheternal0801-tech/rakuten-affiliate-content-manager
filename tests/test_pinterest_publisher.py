from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from app.publishing.publishers.base import PublisherError
from app.publishing.publishers.pinterest_publisher import PinterestPublisher
from app.publishing.schemas import (
    CapabilityAvailability,
    GeneratedMediaDisclosure,
    PublishingPlatform,
    PublishingStatus,
    PublishPayload,
    SnapshotAsset,
    SocialAccountConnection,
)
from app.publishing.secret_store import MemorySecretStore


def _payload(path: Path, *, dry_run: bool) -> PublishPayload:
    return PublishPayload(
        job_id="JOB-pin",
        snapshot_id="SNP-pin",
        campaign_id="CAM-pin",
        platform=PublishingPlatform.PINTEREST,
        target_account_id="ACC-pin",
        credential_reference="memory://credentials/pinterest",
        idempotency_key="a" * 64,
        title="AIで作った画像Pin",
        description="Pinterest投稿テスト",
        assets=[
            SnapshotAsset(
                asset_id="AST-pin",
                file_path=str(path),
                mime_type="image/png",
                sha256="b" * 64,
            )
        ],
        links=["https://example.com/article"],
        platform_metadata={
            "board_id": "123456",
            "board_section_id": "7890",
            "alt_text": "白い背景の商品画像",
        },
        disclosure=GeneratedMediaDisclosure(ai_generated=True),
        dry_run=dry_run,
    )


def test_pinterest_dry_run_uses_official_shape_without_network(tmp_path: Path) -> None:
    image = tmp_path / "pin.png"
    image.write_bytes(b"not-read-in-dry-run")
    provider = PinterestPublisher(
        dry_run=True,
        publishing_enabled=False,
        external_api_enabled=False,
    )

    result = asyncio.run(provider.publish(_payload(image, dry_run=True)))

    assert result.status is PublishingStatus.DRY_RUN_COMPLETED
    assert result.remote_post_id is None
    capability = asyncio.run(provider.get_capabilities())
    assert capability.availability is CapabilityAvailability.DRY_RUN
    assert "boards:write" in capability.required_scopes
    assert capability.constraints["live_video_upload_connected"] is False


def test_pinterest_live_image_pin_uses_secret_reference_and_ai_disclosure(
    tmp_path: Path,
) -> None:
    image = tmp_path / "pin.png"
    image.write_bytes(b"png-image-content")
    store = MemorySecretStore()
    store.set("memory://credentials/pinterest", "access-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/pins"
        assert request.headers["Authorization"] == "Bearer access-token"
        body = __import__("json").loads(request.content)
        assert body["board_id"] == "123456"
        assert body["media_source"] == {
            "source_type": "image_base64",
            "content_type": "image/png",
            "data": base64.b64encode(b"png-image-content").decode("ascii"),
        }
        assert body["ai_disclosures"] == {"values": ["AI_MODIFIED"]}
        assert "access-token" not in request.content.decode("utf-8")
        return httpx.Response(
            201,
            headers={"x-request-id": "request-pin-1"},
            json={"id": "999999", "link": "https://www.pinterest.com/pin/999999/"},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = PinterestPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            result = await provider.publish(_payload(image, dry_run=False))
            assert result.status is PublishingStatus.PUBLISHED
            assert result.remote_post_id == "999999"
            assert result.request_id == "request-pin-1"

    asyncio.run(scenario())


def test_pinterest_validates_remote_account_identity() -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/pinterest", "access-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/user_account"
        return httpx.Response(200, json={"id": "account-123", "username": "brand"})

    async def scenario() -> tuple[bool, bool]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = PinterestPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            base = {
                "platform": PublishingPlatform.PINTEREST,
                "display_name": "Pinterest brand",
                "status": CapabilityAvailability.AVAILABLE,
                "scopes": ["boards:read", "boards:write", "pins:read", "pins:write"],
                "credential_reference": "memory://credentials/pinterest",
            }
            valid = SocialAccountConnection(account_id="account-123", **base)
            wrong = SocialAccountConnection(account_id="different-account", **base)
            return (
                await provider.validate_credentials(valid),
                await provider.validate_credentials(wrong),
            )

    assert asyncio.run(scenario()) == (True, False)


def test_pinterest_rate_limit_is_retryable(tmp_path: Path) -> None:
    image = tmp_path / "pin.png"
    image.write_bytes(b"png-image-content")
    store = MemorySecretStore()
    store.set("memory://credentials/pinterest", "access-token")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(429, json={}))
        ) as client:
            provider = PinterestPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(image, dry_run=False))
            assert captured.value.code == "PINTEREST_RATE_LIMITED"
            assert captured.value.retryable is True

    asyncio.run(scenario())


def test_pinterest_success_without_pin_id_requires_reconciliation(tmp_path: Path) -> None:
    image = tmp_path / "pin.png"
    image.write_bytes(b"png-image-content")
    store = MemorySecretStore()
    store.set("memory://credentials/pinterest", "access-token")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    201,
                    headers={"x-request-id": "pin-ambiguous"},
                    json={},
                )
            )
        ) as client:
            provider = PinterestPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(image, dry_run=False))
            assert captured.value.code == "PINTEREST_PIN_ID_MISSING"
            assert captured.value.outcome_unknown is True
            assert captured.value.request_id == "pin-ambiguous"

    asyncio.run(scenario())
