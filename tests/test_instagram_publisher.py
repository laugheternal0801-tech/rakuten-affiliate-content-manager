from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.publishing.publishers.base import PublisherError
from app.publishing.publishers.instagram_publisher import InstagramPublisher
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


def _payload(
    path: Path, *, dry_run: bool, source_url: str = "https://cdn.example.com/a.jpg"
) -> PublishPayload:
    return PublishPayload(
        job_id="JOB-instagram",
        snapshot_id="SNP-instagram",
        campaign_id="CAM-instagram",
        platform=PublishingPlatform.INSTAGRAM,
        target_account_id="ACC-instagram",
        credential_reference="memory://credentials/instagram",
        idempotency_key="a" * 64,
        caption="承認済みInstagramキャプション",
        assets=[
            SnapshotAsset(
                asset_id="AST-instagram",
                file_path=str(path),
                mime_type="image/jpeg",
                sha256="b" * 64,
                metadata={"source_url": source_url},
            )
        ],
        platform_metadata={
            "media_type": "IMAGE",
            "image_url": source_url,
            "target_external_account_id": "17841400000000000",
        },
        disclosure=GeneratedMediaDisclosure(ai_generated=True),
        dry_run=dry_run,
    )


def test_instagram_dry_run_keeps_unconfigured_version_safe(tmp_path: Path) -> None:
    provider = InstagramPublisher(
        dry_run=True,
        publishing_enabled=False,
        external_api_enabled=False,
    )

    result = asyncio.run(provider.publish(_payload(tmp_path / "image.jpg", dry_run=True)))

    assert result.status is PublishingStatus.DRY_RUN_COMPLETED
    assert result.remote_post_id is None
    capability = asyncio.run(provider.get_capabilities())
    assert capability.availability is CapabilityAvailability.DRY_RUN
    assert capability.operations == ["single_image"]
    assert capability.supported_mime_types == ["image/jpeg"]


def test_instagram_live_single_image_creates_and_publishes_container(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/instagram", "instagram-user-token")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer instagram-user-token"
        assert b"instagram-user-token" not in request.content
        if request.url.path.endswith("/media"):
            form = parse_qs(request.content.decode("utf-8"))
            assert form == {
                "image_url": ["https://cdn.example.com/a.jpg"],
                "caption": ["承認済みInstagramキャプション"],
            }
            return httpx.Response(200, json={"id": "container-1"})
        if request.url.path.endswith("/media_publish"):
            assert parse_qs(request.content.decode("utf-8")) == {"creation_id": ["container-1"]}
            return httpx.Response(
                200,
                headers={"x-fb-trace-id": "trace-instagram-1"},
                json={"id": "media-1"},
            )
        assert request.url.path.endswith("/media-1")
        return httpx.Response(
            200,
            json={"id": "media-1", "permalink": "https://www.instagram.com/p/ABC123/"},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = InstagramPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                api_version="v25.0",
                client=client,
            )
            result = await provider.publish(_payload(tmp_path / "image.jpg", dry_run=False))
            assert result.status is PublishingStatus.PUBLISHED
            assert result.remote_post_id == "media-1"
            assert result.remote_url == "https://www.instagram.com/p/ABC123/"
            assert result.request_id == "trace-instagram-1"
            assert result.response_metadata["container_id"] == "container-1"

    asyncio.run(scenario())
    assert len(requests) == 3


def test_instagram_validates_professional_account_identity() -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/instagram", "instagram-user-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path in {
            "/v25.0/17841400000000000",
            "/v25.0/99999999999999999",
        }
        return httpx.Response(
            200,
            json={"id": "17841400000000000", "username": "brand_jp"},
        )

    async def scenario() -> tuple[bool, bool]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = InstagramPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                api_version="v25.0",
                client=client,
            )
            base = {
                "platform": PublishingPlatform.INSTAGRAM,
                "display_name": "Instagram brand",
                "status": CapabilityAvailability.AVAILABLE,
                "scopes": [
                    "instagram_business_basic",
                    "instagram_business_content_publish",
                ],
                "credential_reference": "memory://credentials/instagram",
            }
            valid = SocialAccountConnection.model_validate(
                {**base, "account_id": "17841400000000000"}
            )
            wrong = SocialAccountConnection.model_validate(
                {**base, "account_id": "99999999999999999"}
            )
            return await provider.validate_credentials(valid), await provider.validate_credentials(
                wrong
            )

    assert asyncio.run(scenario()) == (True, False)


def test_instagram_rejects_non_public_media_url_and_requires_version(tmp_path: Path) -> None:
    payload = _payload(tmp_path / "image.jpg", dry_run=False, source_url="https://127.0.0.1/a.jpg")
    configured = InstagramPublisher(
        dry_run=False,
        publishing_enabled=True,
        external_api_enabled=True,
        api_version="v25.0",
    )
    problems = asyncio.run(configured.validate_content(payload))
    assert any("公開HTTPS" in problem for problem in problems)

    missing_version = InstagramPublisher(
        dry_run=False,
        publishing_enabled=True,
        external_api_enabled=True,
    )
    capability = asyncio.run(missing_version.get_capabilities())
    assert capability.availability is CapabilityAvailability.NOT_CONFIGURED
    with pytest.raises(PublisherError) as captured:
        asyncio.run(missing_version.publish(payload))
    assert captured.value.code == "INSTAGRAM_VERSION_NOT_CONFIGURED"


def test_instagram_publish_phase_timeout_requires_reconciliation(tmp_path: Path) -> None:
    store = MemorySecretStore()
    store.set("memory://credentials/instagram", "instagram-user-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "container-ambiguous"})
        raise httpx.ReadTimeout("response lost", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = InstagramPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                api_version="v25.0",
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(tmp_path / "image.jpg", dry_run=False))
            assert captured.value.outcome_unknown is True
            assert captured.value.retryable is False
            assert captured.value.metadata["container_id"] == "container-ambiguous"

    asyncio.run(scenario())
