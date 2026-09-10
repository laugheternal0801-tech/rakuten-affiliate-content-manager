from __future__ import annotations

import base64
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


class PinterestPublisher(DryRunOfficialPublisher):
    key = "pinterest_official"
    platform = PublishingPlatform.PINTEREST
    live_adapter_connected = True

    def __init__(
        self,
        *,
        dry_run: bool,
        publishing_enabled: bool,
        external_api_enabled: bool,
        capability_overrides: dict[str, object] | None = None,
        secret_store: SecretStore | None = None,
        api_base_url: str = "https://api.pinterest.com/v5",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            dry_run=dry_run,
            publishing_enabled=publishing_enabled,
            external_api_enabled=external_api_enabled,
            capability_overrides=capability_overrides,
        )
        parsed = urlsplit(api_base_url)
        if parsed.scheme != "https" or parsed.hostname != "api.pinterest.com":
            raise ValueError("Pinterest API base URLは公式HTTPS hostのみ利用できます。")
        self._api_base_url = api_base_url.rstrip("/")
        self._secret_store = secret_store or build_secret_store()
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.NOT_CONFIGURED,
            operations=["image_pin"],
            content_types=["image"],
            sandbox_available=True,
            max_title_length=100,
            max_description_length=800,
            max_media_count=1,
            supported_mime_types=["image/jpeg", "image/png"],
            required_scopes=["boards:read", "boards:write", "pins:read", "pins:write"],
            constraints={
                "board_required": True,
                "alt_text_max": 500,
                "destination_url_max": 2048,
                "ai_disclosures_supported": True,
                "live_video_upload_connected": False,
            },
        )

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        values = self._ai_disclosures(payload)
        return {
            "board_id": payload.platform_metadata.get("board_id"),
            "board_section_id": payload.platform_metadata.get("board_section_id"),
            "title": payload.title,
            "description": payload.description or payload.caption,
            "link": payload.links[0] if payload.links else None,
            "alt_text": payload.platform_metadata.get("alt_text"),
            "media_source": {
                "source_type": "approved_asset",
                "asset_id": payload.assets[0].asset_id if payload.assets else None,
            },
            "ai_disclosures": {"values": values},
        }

    async def validate_credentials(self, account: SocialAccountConnection) -> bool:
        if self._dry_run:
            return True
        if not self._publishing_enabled or not self._external_api_enabled:
            return False
        required = set(self.base_capability().required_scopes)
        if not account.credential_reference or not required <= set(account.scopes):
            return False
        try:
            token = resolve_access_token(self._secret_store, account.credential_reference)
            response = await self._client.get(
                f"{self._api_base_url}/user_account",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (OAuthError, httpx.HTTPError):
            return False
        if not response.is_success:
            return False
        try:
            remote = response.json()
        except ValueError:
            return False
        if not isinstance(remote, dict):
            return False
        remote_ids = {
            str(remote.get("id") or ""),
            str(remote.get("username") or ""),
        }
        return account.account_id in remote_ids

    async def validate_content(self, payload: PublishPayload) -> list[str]:
        problems = await super().validate_content(payload)
        board_id = str(payload.platform_metadata.get("board_id") or "")
        board_section_id = str(payload.platform_metadata.get("board_section_id") or "")
        alt_text = str(payload.platform_metadata.get("alt_text") or "")
        if not board_id.isdigit():
            problems.append("Pinterest board IDは数字で指定してください。")
        if board_section_id and not board_section_id.isdigit():
            problems.append("Pinterest board section IDは数字で指定してください。")
        if len(alt_text) > 500:
            problems.append("Pinterest alt textは500文字以内にしてください。")
        if payload.links and len(payload.links[0]) > 2048:
            problems.append("Pinterest linkは2048文字以内にしてください。")
        return problems

    async def publish(self, payload: PublishPayload) -> PublishResult:
        if payload.dry_run or self._dry_run:
            return await super().publish(payload)
        if not self._publishing_enabled or not self._external_api_enabled:
            raise PublisherError(
                "Pinterest外部投稿ゲートがOFFです。",
                code="PUBLISHING_GATE_DISABLED",
                retryable=False,
            )
        if payload.platform is not self.platform:
            raise PublisherError(
                "Pinterest以外のPayloadは処理できません。",
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
        live_payload = self._build_live_payload(payload)
        try:
            response = await self._client.post(
                f"{self._api_base_url}/pins",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=live_payload,
            )
        except httpx.TimeoutException as exc:
            raise PublisherError(
                "Pinterest APIがタイムアウトしました。外部側を確認するまで自動再送しません。",
                code="PINTEREST_TIMEOUT",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "create_pin"},
            ) from exc
        except httpx.RequestError as exc:
            raise PublisherError(
                "Pinterest APIとの通信が中断されました。外部側を確認するまで自動再送しません。",
                code="PINTEREST_CONNECTION",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "create_pin"},
            ) from exc
        if not response.is_success:
            code = self._error_code(response)
            raise PublisherError(
                f"Pinterest APIでHTTP {response.status_code}エラーが発生しました。",
                code=code,
                retryable=response.status_code in {429, 500, 502, 503, 504},
                outcome_unknown=response.status_code >= 500,
                request_id=response.headers.get("x-request-id", ""),
                metadata={"phase": "create_pin", "http_status": response.status_code},
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PublisherError(
                "Pinterest API応答をJSONとして読めません。",
                code="PINTEREST_RESPONSE_INVALID",
                retryable=False,
                outcome_unknown=True,
                request_id=response.headers.get("x-request-id", ""),
                metadata={"phase": "create_pin", "http_status": response.status_code},
            ) from exc
        pin_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        if not pin_id:
            raise PublisherError(
                "Pinterest API応答にPin IDがありません。",
                code="PINTEREST_PIN_ID_MISSING",
                retryable=False,
                outcome_unknown=True,
                request_id=response.headers.get("x-request-id", ""),
                metadata={"phase": "create_pin", "http_status": response.status_code},
            )
        request_id = response.headers.get("x-request-id") or f"pin-{payload.job_id}"
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=payload.snapshot_id,
            target_account_id=payload.target_account_id,
            remote_post_id=pin_id,
            remote_url=str(data.get("link") or f"https://www.pinterest.com/pin/{pin_id}/"),
            published_at=datetime.now(UTC),
            status=PublishingStatus.PUBLISHED,
            request_id=request_id,
            response_metadata={
                "http_status": response.status_code,
                "ai_disclosures": self._ai_disclosures(payload),
            },
        )

    def _build_live_payload(self, payload: PublishPayload) -> dict[str, object]:
        asset = payload.assets[0]
        source_url = str(asset.metadata.get("source_url") or "")
        if source_url:
            parsed = urlsplit(source_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise PublisherError(
                    "Pinterest media source URLはHTTPSで指定してください。",
                    code="MEDIA_URL_INVALID",
                    retryable=False,
                )
            media_source: dict[str, object] = {
                "source_type": "image_url",
                "url": source_url,
            }
        else:
            path = Path(asset.file_path)
            if not path.is_file():
                raise PublisherError(
                    "Pinterest画像ファイルが見つかりません。",
                    code="MEDIA_FILE_MISSING",
                    retryable=False,
                )
            if path.stat().st_size > 20 * 1024 * 1024:
                raise PublisherError(
                    "Pinterest画像は20MB以下にしてください。",
                    code="MEDIA_FILE_TOO_LARGE",
                    retryable=False,
                )
            media_source = {
                "source_type": "image_base64",
                "content_type": asset.mime_type,
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        body: dict[str, object] = {
            "board_id": str(payload.platform_metadata["board_id"]),
            "title": payload.title or None,
            "description": payload.description or payload.caption or None,
            "link": payload.links[0] if payload.links else None,
            "alt_text": payload.platform_metadata.get("alt_text") or None,
            "media_source": media_source,
        }
        board_section_id = payload.platform_metadata.get("board_section_id")
        if board_section_id:
            body["board_section_id"] = str(board_section_id)
        disclosures = self._ai_disclosures(payload)
        if disclosures:
            body["ai_disclosures"] = {"values": disclosures}
        return {key: value for key, value in body.items() if value is not None}

    @staticmethod
    def _ai_disclosures(payload: PublishPayload) -> list[str]:
        values: list[str] = []
        if payload.disclosure.ai_generated:
            values.append("AI_MODIFIED")
        if payload.disclosure.extra.get("synthetic_performer"):
            values.append("SYNTHETIC_PERFORMER")
        return values

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        if response.status_code in {401, 403}:
            return "PINTEREST_PERMISSION_DENIED"
        if response.status_code == 429:
            return "PINTEREST_RATE_LIMITED"
        if response.status_code >= 500:
            return "PINTEREST_UNAVAILABLE"
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("code"):
                return f"PINTEREST_{str(payload['code'])[:60]}"
        except ValueError:
            pass
        return "PINTEREST_REQUEST_REJECTED"
