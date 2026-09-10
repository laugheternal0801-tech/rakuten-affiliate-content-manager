from __future__ import annotations

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


class XPublisher(DryRunOfficialPublisher):
    key = "x_official"
    platform = PublishingPlatform.X
    live_adapter_connected = True

    def __init__(
        self,
        *,
        dry_run: bool,
        publishing_enabled: bool,
        external_api_enabled: bool,
        capability_overrides: dict[str, object] | None = None,
        secret_store: SecretStore | None = None,
        api_base_url: str = "https://api.x.com/2",
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
            or parsed.hostname != "api.x.com"
            or parsed.path.rstrip("/") != "/2"
        ):
            raise ValueError("X API base URLは公式HTTPS v2 hostのみ利用できます。")
        self._api_base_url = api_base_url.rstrip("/")
        self._secret_store = secret_store or build_secret_store()
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.NOT_CONFIGURED,
            operations=["text_post"],
            content_types=["text"],
            max_text_length=280,
            required_scopes=["tweet.read", "tweet.write", "users.read"],
            constraints={
                "text_only_live_adapter": True,
                "media_upload_connected": False,
                "reply_connected": False,
                "thread_connected": False,
                "made_with_ai_supported": True,
            },
        )

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        body: dict[str, object] = {"text": payload.text}
        if payload.disclosure.ai_generated:
            body["made_with_ai"] = True
        return body

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
                f"{self._api_base_url}/users/me",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (OAuthError, httpx.HTTPError):
            return False
        if not response.is_success:
            return False
        try:
            response_payload = response.json()
        except ValueError:
            return False
        data = response_payload.get("data") if isinstance(response_payload, dict) else None
        if not isinstance(data, dict):
            return False
        expected = account.account_id.removeprefix("@").casefold()
        remote_ids = {
            str(data.get("id") or "").casefold(),
            str(data.get("username") or "").casefold(),
        }
        return expected in remote_ids

    async def validate_content(self, payload: PublishPayload) -> list[str]:
        problems = await super().validate_content(payload)
        if not payload.text.strip():
            problems.append("Xテキスト投稿には本文が必要です。")
        if len(payload.text) > 280 and not any("280" in problem for problem in problems):
            problems.append("本文が上限280文字を超えています。")
        if payload.assets:
            problems.append("X Live Publisherは現在テキスト投稿だけに対応しています。")
        return problems

    async def publish(self, payload: PublishPayload) -> PublishResult:
        if payload.dry_run or self._dry_run:
            return await super().publish(payload)
        if not self._publishing_enabled or not self._external_api_enabled:
            raise PublisherError(
                "X外部投稿ゲートがOFFです。",
                code="PUBLISHING_GATE_DISABLED",
                retryable=False,
            )
        if payload.platform is not self.platform:
            raise PublisherError(
                "X以外のPayloadは処理できません。",
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
        request_payload = self.build_request_payload(payload)
        try:
            response = await self._client.post(
                f"{self._api_base_url}/tweets",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=request_payload,
            )
        except httpx.TimeoutException as exc:
            raise PublisherError(
                "X APIがタイムアウトしました。外部側を確認するまで自動再送しません。",
                code="X_TIMEOUT",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "create_post"},
            ) from exc
        except httpx.RequestError as exc:
            raise PublisherError(
                "X APIとの通信が中断されました。外部側を確認するまで自動再送しません。",
                code="X_CONNECTION",
                retryable=False,
                outcome_unknown=True,
                metadata={"phase": "create_post"},
            ) from exc
        if not response.is_success:
            raise PublisherError(
                f"X APIでHTTP {response.status_code}エラーが発生しました。",
                code=self._error_code(response),
                retryable=response.status_code in {429, 500, 502, 503, 504},
                outcome_unknown=response.status_code >= 500,
                request_id=response.headers.get("x-request-id", ""),
                metadata={"phase": "create_post", "http_status": response.status_code},
            )
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise PublisherError(
                "X API応答をJSONとして読めません。",
                code="X_RESPONSE_INVALID",
                retryable=False,
                outcome_unknown=True,
                request_id=response.headers.get("x-request-id", ""),
                metadata={"phase": "create_post", "http_status": response.status_code},
            ) from exc
        data = response_payload.get("data") if isinstance(response_payload, dict) else None
        post_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        if not post_id:
            raise PublisherError(
                "X API応答にPost IDがありません。",
                code="X_POST_ID_MISSING",
                retryable=False,
                outcome_unknown=True,
                request_id=response.headers.get("x-request-id", ""),
                metadata={"phase": "create_post", "http_status": response.status_code},
            )
        request_id = response.headers.get("x-request-id") or f"x-{payload.job_id}"
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=payload.snapshot_id,
            target_account_id=payload.target_account_id,
            remote_post_id=post_id,
            remote_url=f"https://x.com/i/web/status/{post_id}",
            published_at=datetime.now(UTC),
            status=PublishingStatus.PUBLISHED,
            request_id=request_id,
            response_metadata={
                "http_status": response.status_code,
                "made_with_ai": bool(request_payload.get("made_with_ai")),
                "content_mode": "text_only",
            },
        )

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        if response.status_code in {401, 403}:
            return "X_PERMISSION_DENIED"
        if response.status_code == 429:
            return "X_RATE_LIMITED"
        if response.status_code >= 500:
            return "X_UNAVAILABLE"
        try:
            payload = response.json()
            errors = payload.get("errors") if isinstance(payload, dict) else None
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                value = errors[0].get("type") or errors[0].get("title")
                if value:
                    normalized = str(value).rsplit("/", maxsplit=1)[-1]
                    return f"X_{normalized[:60].upper().replace('-', '_')}"
        except ValueError:
            pass
        return "X_REQUEST_REJECTED"
