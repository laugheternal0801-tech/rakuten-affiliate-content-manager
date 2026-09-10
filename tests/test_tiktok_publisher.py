from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.publishing.publishers.base import PublisherError
from app.publishing.publishers.tiktok_publisher import (
    MEBIBYTE,
    MULTIPART_CHUNK_BYTES,
    TikTokPublisher,
)
from app.publishing.schemas import (
    GeneratedMediaDisclosure,
    PublishingPlatform,
    PublishingStatus,
    PublishPayload,
    SnapshotAsset,
)
from app.publishing.secret_store import MemorySecretStore


def _payload(path: Path, *, dry_run: bool) -> PublishPayload:
    return PublishPayload(
        job_id="JOB-tiktok",
        snapshot_id="SNP-tiktok",
        campaign_id="CAM-tiktok",
        platform=PublishingPlatform.TIKTOK,
        target_account_id="ACC-tiktok",
        credential_reference="memory://credentials/tiktok",
        idempotency_key="a" * 64,
        caption="承認済みTikTok動画 #AI",
        assets=[
            SnapshotAsset(
                asset_id="AST-tiktok",
                file_path=str(path),
                mime_type="video/mp4",
                sha256="b" * 64,
            )
        ],
        platform_metadata={
            "privacy_level": "SELF_ONLY",
            "disable_comment": False,
            "disable_duet": True,
            "disable_stitch": False,
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "tiktok_consent_confirmed": True,
            "account_metadata": {
                "privacy_level_options": ["SELF_ONLY"],
                "client_audited": False,
            },
        },
        disclosure=GeneratedMediaDisclosure(ai_generated=True),
        dry_run=dry_run,
    )


def test_tiktok_dry_run_builds_official_video_shape(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video-content")
    provider = TikTokPublisher(
        dry_run=True,
        publishing_enabled=False,
        external_api_enabled=False,
    )

    result = asyncio.run(provider.publish(_payload(video, dry_run=True)))
    request = provider.build_request_payload(_payload(video, dry_run=True))

    assert result.status is PublishingStatus.DRY_RUN_COMPLETED
    assert request["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": len(b"video-content"),
        "chunk_size": len(b"video-content"),
        "total_chunk_count": 1,
    }
    assert request["post_info"]["is_aigc"] is True  # type: ignore[index]


def test_tiktok_live_upload_and_status_poll(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"approved-video-bytes")
    store = MemorySecretStore()
    store.set("memory://credentials/tiktok", "tiktok-user-token")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v2/post/publish/video/init/":
            assert request.headers["Authorization"] == "Bearer tiktok-user-token"
            body = json.loads(request.content)
            assert body["post_info"]["privacy_level"] == "SELF_ONLY"
            assert body["post_info"]["is_aigc"] is True
            assert body["source_info"]["video_size"] == len(b"approved-video-bytes")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "v_pub_file~123",
                        "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=123",
                    },
                    "error": {"code": "ok", "message": "", "log_id": "log-init"},
                },
            )
        if request.url.host == "open-upload.tiktokapis.com":
            assert request.headers["Content-Range"] == (
                f"bytes 0-{len(b'approved-video-bytes') - 1}/{len(b'approved-video-bytes')}"
            )
            assert request.content == b"approved-video-bytes"
            return httpx.Response(201)
        assert request.url.path == "/v2/post/publish/status/fetch/"
        assert json.loads(request.content) == {"publish_id": "v_pub_file~123"}
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "PUBLISH_COMPLETE",
                    "publicaly_available_post_id": ["7460000000000000000"],
                },
                "error": {"code": "ok", "message": "", "log_id": "log-status"},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TikTokPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            submitted = await provider.publish(_payload(video, dry_run=False))
            assert submitted.status is PublishingStatus.PROCESSING
            assert submitted.remote_post_id == "v_pub_file~123"
            assert "credential_reference" not in submitted.model_dump()
            completed = await provider.get_publish_status(submitted)
            assert completed.status is PublishingStatus.PUBLISHED
            assert completed.published_at is not None
            assert completed.response_metadata["publicly_available_post_ids"] == [
                "7460000000000000000"
            ]

    asyncio.run(scenario())
    assert len(requests) == 3


def test_tiktok_rejects_nonofficial_upload_url(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    store = MemorySecretStore()
    store.set("memory://credentials/tiktok", "tiktok-user-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "publish_id": "v_pub_file~unsafe",
                    "upload_url": "https://attacker.example/upload",
                },
                "error": {"code": "ok", "message": "", "log_id": "log-init"},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TikTokPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(video, dry_run=False))
            assert captured.value.code == "TIKTOK_UPLOAD_URL_INVALID"

    asyncio.run(scenario())


def test_tiktok_chunk_plan_keeps_final_chunk_within_official_limit() -> None:
    chunk_size, chunk_count = TikTokPublisher._chunk_plan(65 * MEBIBYTE)

    assert chunk_size == MULTIPART_CHUNK_BYTES
    assert chunk_count == 2
    assert 5 * MEBIBYTE <= 65 * MEBIBYTE - chunk_size <= 128 * MEBIBYTE


def test_tiktok_upload_failure_preserves_publish_id_for_reconciliation(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"approved-video-bytes")
    store = MemorySecretStore()
    store.set("memory://credentials/tiktok", "tiktok-user-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/post/publish/video/init/":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "v_pub_file~ambiguous",
                        "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1",
                    },
                    "error": {"code": "ok", "message": "", "log_id": "log-ambiguous"},
                },
            )
        raise httpx.ReadTimeout("upload response lost", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = TikTokPublisher(
                dry_run=False,
                publishing_enabled=True,
                external_api_enabled=True,
                secret_store=store,
                client=client,
            )
            with pytest.raises(PublisherError) as captured:
                await provider.publish(_payload(video, dry_run=False))
            assert captured.value.outcome_unknown is True
            assert captured.value.remote_post_id == "v_pub_file~ambiguous"
            assert captured.value.request_id == "log-ambiguous"

    asyncio.run(scenario())
