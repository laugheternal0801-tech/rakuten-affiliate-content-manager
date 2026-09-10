from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime
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


class InstagramPublisher(DryRunOfficialPublisher):
    key = "instagram_official"
    platform = PublishingPlatform.INSTAGRAM
    live_adapter_connected = True

    def __init__(
        self,
        *,
        dry_run: bool,
        publishing_enabled: bool,
        external_api_enabled: bool,
        capability_overrides: dict[str, object] | None = None,
        secret_store: SecretStore | None = None,
        api_base_url: str = "https://graph.instagram.com",
        api_version: str = "",
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
            or parsed.hostname != "graph.instagram.com"
            or parsed.path.rstrip("/")
        ):
            raise ValueError("Instagram API base URLは公式HTTPS hostのみ利用できます。")
        normalized_version = api_version.strip().lstrip("/")
        if normalized_version and not re.fullmatch(r"v\d+\.\d+", normalized_version):
            raise ValueError("Instagram Graph API versionはvNN.N形式で指定してください。")
        self._api_base_url = api_base_url.rstrip("/")
        self._api_version = normalized_version
        self._secret_store = secret_store or build_secret_store()
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.NOT_CONFIGURED,
            operations=["single_image"],
            content_types=["image"],
            max_text_length=2_200,
            max_media_count=1,
            supported_mime_types=["image/jpeg"],
            required_scopes=[
                "instagram_business_basic",
                "instagram_business_content_publish",
            ],
            constraints={
                "professional_account_required": True,
                "public_https_media_url_required": True,
                "container_then_publish": True,
                "live_video_connected": False,
                "live_reel_connected": False,
                "live_carousel_connected": False,
                "live_story_connected": False,
            },
        )

    async def get_capabilities(
        self, account: SocialAccountConnection | None = None
    ) -> PublisherCapability:
        capability = await super().get_capabilities(account)
        if (
            not self._dry_run
            and self._publishing_enabled
            and self._external_api_enabled
            and not self._api_version
        ):
            return capability.model_copy(
                update={
                    "availability": CapabilityAvailability.NOT_CONFIGURED,
                    "enabled": False,
                    "message": "Instagram Graph API versionが未設定です。",
                }
            )
        return capability

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        source_url = self._approved_image_url(payload)
        body: dict[str, object] = {
            "image_url": source_url or "approved_public_image_url_required",
        }
        caption = payload.caption or payload.text
        if caption:
            body["caption"] = caption
        return body

    async def validate_credentials(self, account: SocialAccountConnection) -> bool:
        if self._dry_run:
            return True
        if not self._publishing_enabled or not self._external_api_enabled or not self._api_version:
            return False
        required = set(self.base_capability().required_scopes)
        if (
            not account.account_id.isdigit()
            or not account.credential_reference
            or not required <= set(account.scopes)
        ):
            return False
        try:
            token = resolve_access_token(self._secret_store, account.credential_reference)
            response = await self._client.get(
                self._endpoint(account.account_id),
                params={"fields": "id,username"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (OAuthError, httpx.HTTPError, PublisherError):
            return False
        if not response.is_success:
            return False
        try:
            data = response.json()
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False
        return account.account_id == str(data.get("id") or "")

    async def validate_content(self, payload: PublishPayload) -> list[str]:
        problems = await super().validate_content(payload)
        if len(payload.assets) != 1:
            problems.append("Instagram単一画像投稿にはJPEG画像を1件だけ指定してください。")
            return problems
        source_url = self._approved_image_url(payload)
        if not self._is_public_https_url(source_url):
            problems.append("Instagram画像にはMetaから取得可能な公開HTTPS JPEG URLが必要です。")
        media_type = str(payload.platform_metadata.get("media_type", "IMAGE")).upper()
        if media_type != "IMAGE":
            problems.append("Instagram Live Publisherは現在単一画像（IMAGE）だけに対応しています。")
        external_account_id = str(payload.platform_metadata.get("target_external_account_id") or "")
        if not external_account_id.isdigit():
            problems.append("Instagram professional account IDは数字で指定してください。")
        return problems

    async def publish(self, payload: PublishPayload) -> PublishResult:
        if payload.dry_run or self._dry_run:
            return await super().publish(payload)
        if not self._publishing_enabled or not self._external_api_enabled:
            raise PublisherError(
                "Instagram外部投稿ゲートがOFFです。",
                code="PUBLISHING_GATE_DISABLED",
                retryable=False,
            )
        if not self._api_version:
            raise PublisherError(
                "Instagram Graph API versionが未設定です。",
                code="INSTAGRAM_VERSION_NOT_CONFIGURED",
                retryable=False,
            )
        if payload.platform is not self.platform:
            raise PublisherError(
                "Instagram以外のPayloadは処理できません。",
                code="PLATFORM_MISMATCH",
                retryable=False,
            )
        problems = await self.validate_content(payload)
        if problems:
            raise PublisherError(
                " / ".join(problems),
                code="CONTENT_VALIDATION_FAILED",
                retryable=False,
            )
        try:
            token = resolve_access_token(self._secret_store, payload.credential_reference)
        except OAuthError as exc:
            raise PublisherError(str(exc), code=exc.code, retryable=exc.retryable) from exc
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        external_account_id = str(payload.platform_metadata["target_external_account_id"])
        phase = "container"
        container_id = ""
        try:
            container_response = await self._client.post(
                self._endpoint(external_account_id, "media"),
                data=self.build_request_payload(payload),
                headers=headers,
            )
            self._raise_for_response(container_response, phase="CONTAINER")
            container_id = self._response_id(container_response, phase="CONTAINER")
            phase = "publish"
            publish_response = await self._client.post(
                self._endpoint(external_account_id, "media_publish"),
                data={"creation_id": container_id},
                headers=headers,
            )
            self._raise_for_response(
                publish_response,
                phase="PUBLISH",
                outcome_unknown_on_server_error=True,
            )
            media_id = self._response_id(
                publish_response,
                phase="MEDIA",
                outcome_unknown=True,
            )
        except httpx.TimeoutException as exc:
            outcome_unknown = phase == "publish"
            raise PublisherError(
                (
                    "Instagram公開APIがタイムアウトしました。外部側を確認するまで"
                    "自動再送しません。"
                    if outcome_unknown
                    else "Instagramコンテナ作成がタイムアウトしました。"
                ),
                code="INSTAGRAM_TIMEOUT",
                retryable=not outcome_unknown,
                outcome_unknown=outcome_unknown,
                metadata={"phase": phase, "container_id": container_id},
            ) from exc
        except httpx.RequestError as exc:
            outcome_unknown = phase == "publish"
            raise PublisherError(
                (
                    "Instagram公開APIとの通信が中断されました。外部側を確認するまで"
                    "自動再送しません。"
                    if outcome_unknown
                    else "Instagramコンテナ作成APIへ接続できません。"
                ),
                code="INSTAGRAM_CONNECTION",
                retryable=not outcome_unknown,
                outcome_unknown=outcome_unknown,
                metadata={"phase": phase, "container_id": container_id},
            ) from exc

        remote_url: str | None = None
        try:
            permalink_response = await self._client.get(
                self._endpoint(media_id),
                params={"fields": "id,permalink"},
                headers=headers,
            )
            if permalink_response.is_success:
                permalink_payload = permalink_response.json()
                if isinstance(permalink_payload, dict):
                    candidate = str(permalink_payload.get("permalink") or "")
                    if self._is_instagram_permalink(candidate):
                        remote_url = candidate
        except (httpx.HTTPError, ValueError):
            pass

        request_id = (
            publish_response.headers.get("x-fb-trace-id")
            or publish_response.headers.get("x-request-id")
            or f"ig-{payload.job_id}"
        )
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=payload.snapshot_id,
            target_account_id=payload.target_account_id,
            remote_post_id=media_id,
            remote_url=remote_url,
            published_at=datetime.now(UTC),
            status=PublishingStatus.PUBLISHED,
            request_id=request_id,
            response_metadata={
                "container_id": container_id,
                "container_http_status": container_response.status_code,
                "publish_http_status": publish_response.status_code,
                "content_mode": "single_image",
            },
        )

    def _endpoint(self, object_id: str, edge: str = "") -> str:
        if not object_id or "/" in object_id or "?" in object_id or "#" in object_id:
            raise PublisherError(
                "Instagram object IDが正しくありません。",
                code="INSTAGRAM_OBJECT_ID_INVALID",
                retryable=False,
            )
        base = f"{self._api_base_url}/{self._api_version}/{object_id}"
        return f"{base}/{edge}" if edge else base

    @staticmethod
    def _approved_image_url(payload: PublishPayload) -> str:
        approved = str(payload.platform_metadata.get("image_url") or "")
        if approved:
            return approved
        return str(payload.assets[0].metadata.get("source_url") or "") if payload.assets else ""

    @staticmethod
    def _is_public_https_url(value: str) -> bool:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.casefold().rstrip(".")
        if (
            hostname == "localhost"
            or hostname.endswith(".localhost")
            or hostname.endswith(".local")
        ):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return True
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )

    @staticmethod
    def _is_instagram_permalink(value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and parsed.hostname in {
            "instagram.com",
            "www.instagram.com",
        }

    @classmethod
    def _raise_for_response(
        cls,
        response: httpx.Response,
        *,
        phase: str,
        outcome_unknown_on_server_error: bool = False,
    ) -> None:
        if response.is_success:
            return
        raise PublisherError(
            f"Instagram {phase.lower()} APIでHTTP {response.status_code}エラーが発生しました。",
            code=cls._error_code(response, phase=phase),
            retryable=response.status_code in {429, 500, 502, 503, 504},
            outcome_unknown=(outcome_unknown_on_server_error and response.status_code >= 500),
            request_id=(
                response.headers.get("x-fb-trace-id")
                or response.headers.get("x-request-id", "")
            ),
            metadata={"phase": phase.lower(), "http_status": response.status_code},
        )

    @staticmethod
    def _response_id(
        response: httpx.Response,
        *,
        phase: str,
        outcome_unknown: bool = False,
    ) -> str:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublisherError(
                "Instagram API応答をJSONとして読めません。",
                code=f"INSTAGRAM_{phase}_RESPONSE_INVALID",
                retryable=False,
                outcome_unknown=outcome_unknown,
                request_id=(
                    response.headers.get("x-fb-trace-id")
                    or response.headers.get("x-request-id", "")
                ),
                metadata={"phase": phase.lower(), "http_status": response.status_code},
            ) from exc
        remote_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        if not remote_id:
            raise PublisherError(
                "Instagram API応答にIDがありません。",
                code=f"INSTAGRAM_{phase}_ID_MISSING",
                retryable=False,
                outcome_unknown=outcome_unknown,
                request_id=(
                    response.headers.get("x-fb-trace-id")
                    or response.headers.get("x-request-id", "")
                ),
                metadata={"phase": phase.lower(), "http_status": response.status_code},
            )
        return remote_id

    @staticmethod
    def _error_code(response: httpx.Response, *, phase: str) -> str:
        if response.status_code in {401, 403}:
            return "INSTAGRAM_PERMISSION_DENIED"
        if response.status_code == 429:
            return "INSTAGRAM_RATE_LIMITED"
        if response.status_code >= 500:
            return "INSTAGRAM_UNAVAILABLE"
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and error.get("code"):
                return f"INSTAGRAM_{phase}_{str(error['code'])[:40]}"
        except ValueError:
            pass
        return f"INSTAGRAM_{phase}_REJECTED"
