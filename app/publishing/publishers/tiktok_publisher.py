from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.publishing.oauth import OAuthError, resolve_access_token
from app.publishing.publishers.base import DryRunOfficialPublisher, PublisherError
from app.publishing.schemas import (
    CapabilityAvailability,
    PublisherCapability,
    PublishingPlatform,
    PublishingStatus,
    PublishPayload,
    PublishResult,
    SocialAccountConnection,
)
from app.publishing.secret_store import SecretStore, build_secret_store

MEBIBYTE = 1024 * 1024
MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024
MAX_SINGLE_CHUNK_BYTES = 64 * MEBIBYTE
MULTIPART_CHUNK_BYTES = 32 * MEBIBYTE


class TikTokPublisher(DryRunOfficialPublisher):
    key = "tiktok_official"
    platform = PublishingPlatform.TIKTOK
    live_adapter_connected = True

    def __init__(
        self,
        *,
        dry_run: bool,
        publishing_enabled: bool,
        external_api_enabled: bool,
        capability_overrides: dict[str, object] | None = None,
        secret_store: SecretStore | None = None,
        api_base_url: str = "https://open.tiktokapis.com/v2",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            dry_run=dry_run,
            publishing_enabled=publishing_enabled,
            external_api_enabled=external_api_enabled,
            capability_overrides=capability_overrides,
        )
        parsed = urlsplit(api_base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "open.tiktokapis.com"
            or parsed.path.rstrip("/") != "/v2"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("TikTok API base URLは公式HTTPS /v2 endpointのみ利用できます。")
        self._api_base_url = api_base_url.rstrip("/")
        self._secret_store = secret_store or build_secret_store()
        self._client = client or httpx.AsyncClient(timeout=60.0, follow_redirects=False)

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.NOT_CONFIGURED,
            operations=["video_direct_post"],
            content_types=["video"],
            max_text_length=2_200,
            max_media_count=1,
            supported_mime_types=["video/mp4"],
            required_scopes=["user.info.basic", "video.publish"],
            constraints={
                "creator_info_required": True,
                "privacy_must_be_user_selected": True,
                "unaudited_public_post_unavailable": True,
                "explicit_user_consent_required": True,
                "max_video_bytes": MAX_VIDEO_BYTES,
                "live_photo_post_connected": False,
                "live_draft_upload_connected": False,
            },
        )

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        size = self._video_size(payload)
        chunk_size, total_chunks = self._chunk_plan(size) if size else (0, 1)
        metadata = payload.platform_metadata
        return {
            "post_info": {
                "title": payload.caption or payload.text,
                "privacy_level": metadata.get("privacy_level"),
                "disable_comment": bool(metadata.get("disable_comment", False)),
                "disable_duet": bool(metadata.get("disable_duet", False)),
                "disable_stitch": bool(metadata.get("disable_stitch", False)),
                "brand_content_toggle": bool(metadata.get("brand_content_toggle", False)),
                "brand_organic_toggle": bool(metadata.get("brand_organic_toggle", False)),
                "is_aigc": payload.disclosure.ai_generated,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }

    async def validate_credentials(self, account: SocialAccountConnection) -> bool:
        if self._dry_run:
            return True
        required = set(self.base_capability().required_scopes)
        if (
            not self._publishing_enabled
            or not self._external_api_enabled
            or not account.credential_reference
            or not required <= set(account.scopes)
        ):
            return False
        try:
            token = resolve_access_token(self._secret_store, account.credential_reference)
            response = await self._client.get(
                f"{self._api_base_url}/user/info/",
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            response_payload = self._response_json(response, phase="TIKTOK_IDENTITY")
        except (OAuthError, httpx.HTTPError, PublisherError):
            return False
        data = response_payload.get("data")
        user = data.get("user") if isinstance(data, dict) else None
        return isinstance(user, dict) and str(user.get("open_id") or "") == account.account_id

    async def validate_content(self, payload: PublishPayload) -> list[str]:
        problems = await super().validate_content(payload)
        if payload.platform is not self.platform:
            problems.append("TikTok以外のPayloadは処理できません。")
        if len(payload.assets) != 1 or payload.assets[0].mime_type != "video/mp4":
            problems.append("TikTok Direct PostにはMP4動画を1件だけ指定してください。")
            return problems
        asset = payload.assets[0]
        if asset.is_placeholder:
            problems.append("Placeholder動画はTikTokへ投稿できません。")
        path = Path(asset.file_path)
        if not payload.dry_run and not path.is_file():
            problems.append("承認済みTikTok動画ファイルが見つかりません。")
        if path.is_file():
            size = path.stat().st_size
            if size <= 0:
                problems.append("TikTok動画ファイルが空です。")
            elif size > MAX_VIDEO_BYTES:
                problems.append("TikTok動画は4GB以下にしてください。")
        if self._utf16_length(payload.caption or payload.text) > 2_200:
            problems.append("TikTok本文がUTF-16換算2,200文字を超えています。")
        privacy = str(payload.platform_metadata.get("privacy_level") or "")
        account_metadata = payload.platform_metadata.get("account_metadata")
        if isinstance(account_metadata, dict):
            options = account_metadata.get("privacy_level_options")
            if isinstance(options, list) and privacy not in {str(value) for value in options}:
                problems.append("選択したTikTok公開範囲が最新Creator Infoに含まれていません。")
        return problems

    async def publish(self, payload: PublishPayload) -> PublishResult:
        if payload.dry_run or self._dry_run:
            return await super().publish(payload)
        if not self._publishing_enabled or not self._external_api_enabled:
            raise PublisherError(
                "TikTok外部投稿ゲートがOFFです。",
                code="PUBLISHING_GATE_DISABLED",
                retryable=False,
            )
        problems = await self.validate_content(payload)
        if problems:
            raise PublisherError(
                " / ".join(problems),
                code="TIKTOK_CONTENT_INVALID",
                retryable=False,
            )
        try:
            token = resolve_access_token(self._secret_store, payload.credential_reference)
        except OAuthError as exc:
            raise PublisherError(str(exc), code=exc.code, retryable=exc.retryable) from exc

        request_payload = self.build_request_payload(payload)
        try:
            response = await self._client.post(
                f"{self._api_base_url}/post/publish/video/init/",
                headers=self._headers(token),
                json=request_payload,
            )
            response_payload = self._response_json(response, phase="TIKTOK_VIDEO_INIT")
        except httpx.TimeoutException as exc:
            raise PublisherError(
                "TikTok動画初期化がタイムアウトしました。重複防止のため自動再送しません。",
                code="TIKTOK_INIT_TIMEOUT",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "video_init"},
            ) from exc
        except httpx.RequestError as exc:
            raise PublisherError(
                "TikTok動画初期化Endpointへ接続できません。",
                code="TIKTOK_INIT_CONNECTION",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "video_init"},
            ) from exc
        data = response_payload.get("data")
        if not isinstance(data, dict):
            raise PublisherError(
                "TikTok動画初期化応答が正しくありません。",
                code="TIKTOK_INIT_RESPONSE_INVALID",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "video_init"},
            )
        publish_id = str(data.get("publish_id") or "")
        upload_url = str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise PublisherError(
                "TikTok動画初期化応答にpublish_idまたはupload_urlがありません。",
                code="TIKTOK_INIT_FIELDS_MISSING",
                retryable=False,
                outcome_unknown=True,
                remote_post_id=publish_id,
                metadata={"phase": "video_init"},
            )
        error = response_payload.get("error")
        log_id = str(error.get("log_id") or "") if isinstance(error, dict) else ""
        try:
            self._validate_upload_url(upload_url)
            await self._upload_video(Path(payload.assets[0].file_path), upload_url)
        except PublisherError as exc:
            raise PublisherError(
                str(exc),
                code=exc.code,
                retryable=False,
                outcome_unknown=True,
                remote_post_id=publish_id,
                request_id=log_id,
                metadata={
                    **exc.metadata,
                    "phase": "video_upload",
                    "original_retryable": exc.retryable,
                },
            ) from exc
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=payload.snapshot_id,
            target_account_id=payload.target_account_id,
            remote_post_id=publish_id,
            status=PublishingStatus.PROCESSING,
            request_id=log_id or f"tiktok-{payload.job_id}",
            response_metadata={
                "provider_status": "PROCESSING_UPLOAD",
                "upload_complete": True,
                "ai_generated": payload.disclosure.ai_generated,
            },
            credential_reference=payload.credential_reference,
        )

    async def get_publish_status(self, result: PublishResult) -> PublishResult:
        if self._dry_run or result.status in {PublishingStatus.PUBLISHED, PublishingStatus.FAILED}:
            return result
        if not result.remote_post_id or not result.credential_reference:
            raise PublisherError(
                "TikTok公開状態の確認に必要な参照情報がありません。",
                code="TIKTOK_STATUS_CONTEXT_MISSING",
                retryable=False,
            )
        try:
            token = resolve_access_token(self._secret_store, result.credential_reference)
            response = await self._client.post(
                f"{self._api_base_url}/post/publish/status/fetch/",
                headers=self._headers(token),
                json={"publish_id": result.remote_post_id},
            )
            response_payload = self._response_json(response, phase="TIKTOK_STATUS")
        except OAuthError as exc:
            raise PublisherError(str(exc), code=exc.code, retryable=exc.retryable) from exc
        except httpx.TimeoutException as exc:
            raise PublisherError(
                "TikTok公開状態の確認がタイムアウトしました。",
                code="TIKTOK_STATUS_TIMEOUT",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise PublisherError(
                "TikTok公開状態Endpointへ接続できません。",
                code="TIKTOK_STATUS_CONNECTION",
                retryable=True,
            ) from exc
        data = response_payload.get("data")
        if not isinstance(data, dict):
            raise PublisherError(
                "TikTok公開状態応答が正しくありません。",
                code="TIKTOK_STATUS_RESPONSE_INVALID",
                retryable=False,
            )
        provider_status = str(data.get("status") or "")
        public_ids = data.get("publicaly_available_post_id")
        if not isinstance(public_ids, list):
            public_ids = []
        update: dict[str, object] = {
            "response_metadata": {
                **result.response_metadata,
                "provider_status": provider_status,
                "publicly_available_post_ids": [str(value) for value in public_ids],
            }
        }
        if provider_status == "PUBLISH_COMPLETE":
            update.update({"status": PublishingStatus.PUBLISHED, "published_at": datetime.now(UTC)})
        elif provider_status == "FAILED":
            reason = str(data.get("fail_reason") or "unknown")
            update.update(
                {
                    "status": PublishingStatus.FAILED,
                    "error_code": f"TIKTOK_{reason[:80]}",
                    "error": f"TikTok processing failed: {reason}",
                    "retryable": False,
                }
            )
        else:
            update["status"] = PublishingStatus.PROCESSING
        return result.model_copy(update=update)

    async def _upload_video(self, path: Path, upload_url: str) -> None:
        size = path.stat().st_size
        chunk_size, total_chunks = self._chunk_plan(size)
        with path.open("rb") as media:
            for index in range(total_chunks):
                start = index * chunk_size
                length = size - start if index == total_chunks - 1 else chunk_size
                chunk = media.read(length)
                if len(chunk) != length:
                    raise PublisherError(
                        "TikTok動画の読み込み中にファイルサイズが変わりました。",
                        code="TIKTOK_MEDIA_CHANGED",
                        retryable=False,
                    )
                try:
                    response = await self._client.put(
                        upload_url,
                        content=chunk,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(length),
                            "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                        },
                    )
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    raise PublisherError(
                        "TikTok動画アップロードが中断されました。重複防止のため自動再送しません。",
                        code="TIKTOK_UPLOAD_CONNECTION",
                        retryable=False,
                        outcome_unknown=True,
                        metadata={"chunk_index": index, "total_chunks": total_chunks},
                    ) from exc
                expected = 201 if index == total_chunks - 1 else 206
                if response.status_code != expected:
                    raise PublisherError(
                        f"TikTok動画アップロードでHTTP {response.status_code}を受信しました。",
                        code="TIKTOK_UPLOAD_REJECTED",
                        retryable=False,
                        outcome_unknown=(
                            index == total_chunks - 1 and response.status_code >= 500
                        ),
                        metadata={
                            "chunk_index": index,
                            "total_chunks": total_chunks,
                            "http_status": response.status_code,
                        },
                    )

    @staticmethod
    def _chunk_plan(size: int) -> tuple[int, int]:
        if size <= 0:
            raise PublisherError(
                "TikTok動画ファイルが空です。",
                code="TIKTOK_MEDIA_EMPTY",
                retryable=False,
            )
        if size > MAX_VIDEO_BYTES:
            raise PublisherError(
                "TikTok動画は4GB以下にしてください。",
                code="TIKTOK_MEDIA_TOO_LARGE",
                retryable=False,
            )
        if size <= MAX_SINGLE_CHUNK_BYTES:
            return size, 1
        total_chunks = size // MULTIPART_CHUNK_BYTES
        if not 1 <= total_chunks <= 1000:
            raise PublisherError(
                "TikTok動画のChunk数が許容範囲外です。",
                code="TIKTOK_CHUNK_COUNT_INVALID",
                retryable=False,
            )
        return MULTIPART_CHUNK_BYTES, total_chunks

    @staticmethod
    def _video_size(payload: PublishPayload) -> int:
        if len(payload.assets) != 1:
            return 0
        path = Path(payload.assets[0].file_path)
        return path.stat().st_size if path.is_file() else 0

    @staticmethod
    def _utf16_length(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
        }

    @staticmethod
    def _validate_upload_url(upload_url: str) -> None:
        parsed = urlsplit(upload_url)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not hostname.endswith(".tiktokapis.com")
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise PublisherError(
                "TikTok upload_urlが公式HTTPS domainではありません。",
                code="TIKTOK_UPLOAD_URL_INVALID",
                retryable=False,
            )

    @staticmethod
    def _response_json(response: httpx.Response, *, phase: str) -> dict[str, object]:
        if not response.is_success:
            code = "REQUEST_REJECTED"
            try:
                raw_error = response.json()
                if isinstance(raw_error, dict):
                    error = raw_error.get("error")
                    if isinstance(error, dict) and error.get("code"):
                        code = str(error["code"])
            except ValueError:
                pass
            raise PublisherError(
                f"{phase}でHTTP {response.status_code}エラーが発生しました。",
                code=f"{phase}_{code[:60]}",
                retryable=response.status_code == 429,
                outcome_unknown=(
                    phase == "TIKTOK_VIDEO_INIT" and response.status_code >= 500
                ),
                metadata={"phase": phase.lower(), "http_status": response.status_code},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublisherError(
                f"{phase}応答をJSONとして読めません。",
                code=f"{phase}_RESPONSE_INVALID",
                retryable=False,
                outcome_unknown=phase == "TIKTOK_VIDEO_INIT",
                metadata={"phase": phase.lower(), "http_status": response.status_code},
            ) from exc
        if not isinstance(payload, dict):
            raise PublisherError(
                f"{phase}応答がJSON objectではありません。",
                code=f"{phase}_RESPONSE_INVALID",
                retryable=False,
                outcome_unknown=phase == "TIKTOK_VIDEO_INIT",
                metadata={"phase": phase.lower(), "http_status": response.status_code},
            )
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            if code and code != "ok":
                raise PublisherError(
                    f"{phase}がTikTokに拒否されました: {error.get('message') or code}",
                    code=f"{phase}_{code[:60]}",
                    retryable=code == "rate_limit_exceeded",
                )
        return payload
