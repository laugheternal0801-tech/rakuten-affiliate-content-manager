from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.oauth import (
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthProviderConfig,
    OAuthTokenBundle,
    build_instagram_oauth_config,
    build_tiktok_oauth_config,
    build_x_oauth_config,
    resolve_access_token,
    resolve_token_bundle,
)
from app.publishing.repositories import (
    get_account_by_external_id,
    save_account,
    save_audit,
)
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    CapabilityAvailability,
    PublishingPlatform,
    SocialAccountConnection,
)
from app.publishing.secret_store import (
    SecretStore,
    SecretStoreError,
    build_secret_store,
    default_writable_secret_prefix,
)


class SocialConnectionError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class RemoteIdentity:
    account_id: str
    username: str
    display_name: str


@dataclass(frozen=True)
class ConnectionDiagnostic:
    platform: PublishingPlatform
    item: str
    ready: bool
    status: str
    required_action: str


class SocialConnectionService:
    """Completes official OAuth flows without storing token bodies in the database."""

    _REQUIRED_SCOPES: ClassVar[dict[PublishingPlatform, tuple[str, ...]]] = {
        PublishingPlatform.X: ("tweet.read", "tweet.write", "users.read"),
        PublishingPlatform.INSTAGRAM: (
            "instagram_business_basic",
            "instagram_business_content_publish",
        ),
        PublishingPlatform.TIKTOK: ("user.info.basic", "video.publish"),
    }

    def __init__(
        self,
        settings: Settings,
        *,
        secret_store: SecretStore | None = None,
        client: httpx.Client | None = None,
        oauth_client: OAuthClient | None = None,
        writable_reference_prefix: str | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._writable_secret_prefix = (
            writable_reference_prefix or default_writable_secret_prefix()
        ).rstrip("/")
        self._secret_store = secret_store or build_secret_store(
            x_oauth_access_token=settings.x_oauth_access_token,
            meta_access_token=settings.meta_access_token,
            pinterest_access_token=settings.pinterest_access_token,
        )
        self._client = client or httpx.Client(timeout=settings.publishing_oauth_timeout_seconds)
        self._oauth = oauth_client or OAuthClient(
            self._secret_store,
            timeout_seconds=settings.publishing_oauth_timeout_seconds,
            client=self._client,
            pending_reference_prefix=f"{self._writable_secret_prefix}/oauth/pending",
        )
        self._validate_official_endpoints()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def begin(self, platform: PublishingPlatform) -> OAuthAuthorizationRequest:
        config = self._config(platform)
        return self._oauth.begin_authorization(config)

    def pending_platform(self, state: str) -> PublishingPlatform:
        pending = self._oauth.pending_authorization(state)
        try:
            return PublishingPlatform(pending.provider)
        except ValueError as exc:
            raise SocialConnectionError(
                "未対応のOAuth providerです。",
                code="PROVIDER_NOT_SUPPORTED",
            ) from exc

    def cancel_callback(self, state: str) -> bool:
        return self._oauth.discard_pending_authorization(state)

    def complete_callback(
        self,
        session: Session,
        *,
        state: str,
        code: str,
    ) -> SocialAccountConnection:
        platform = self.pending_platform(state)
        config = self._config(platform)
        credential_reference = self._credential_reference(platform, state)
        try:
            if platform is PublishingPlatform.X:
                bundle = self._oauth.exchange_authorization_code(
                    config,
                    client_secret=self._settings.x_client_secret,
                    state=state,
                    code=code,
                    credential_reference=credential_reference,
                )
            elif platform is PublishingPlatform.INSTAGRAM:
                bundle = self._exchange_instagram(
                    config,
                    state=state,
                    code=code,
                    credential_reference=credential_reference,
                )
            elif platform is PublishingPlatform.TIKTOK:
                bundle = self._oauth.exchange_authorization_code(
                    config,
                    client_secret=self._settings.tiktok_client_secret,
                    state=state,
                    code=code,
                    credential_reference=credential_reference,
                )
            else:
                raise SocialConnectionError(
                    "このPlatformのOAuth接続は未対応です。",
                    code="PLATFORM_NOT_SUPPORTED",
                )
            identity = self._fetch_identity(platform, credential_reference)
            provider_metadata = (
                self._query_tiktok_creator_info(credential_reference)
                if platform is PublishingPlatform.TIKTOK
                else {}
            )
            account = self._upsert_account(
                session,
                platform=platform,
                identity=identity,
                credential_reference=credential_reference,
                bundle=bundle,
                connected_via="oauth",
                provider_metadata=provider_metadata,
            )
        except Exception:
            self._delete_secret_safely(credential_reference)
            raise
        self._audit(session, account, AuditEventType.ACCOUNT_CONNECTED, "oauth_callback")
        return account

    def connect_environment_token(
        self,
        session: Session,
        platform: PublishingPlatform,
    ) -> SocialAccountConnection:
        credential_reference = self._environment_reference(platform)
        token = resolve_access_token(self._secret_store, credential_reference)
        if not token:
            raise SocialConnectionError(
                "Environment Access Tokenが未設定です。",
                code="ENVIRONMENT_TOKEN_NOT_CONFIGURED",
            )
        identity = self._fetch_identity(platform, credential_reference)
        bundle = OAuthTokenBundle(
            access_token=token,
            scopes=self._REQUIRED_SCOPES[platform],
            provider=platform.value,
        )
        account = self._upsert_account(
            session,
            platform=platform,
            identity=identity,
            credential_reference=credential_reference,
            bundle=bundle,
            connected_via="environment",
        )
        self._audit(session, account, AuditEventType.ACCOUNT_CONNECTED, "environment_token")
        return account

    def refresh_account(self, account: SocialAccountConnection) -> SocialAccountConnection:
        if account.platform not in self._REQUIRED_SCOPES:
            raise SocialConnectionError(
                "このPlatformのToken更新は未対応です。",
                code="REFRESH_NOT_SUPPORTED",
            )
        if account.credential_reference.startswith("env://"):
            raise SocialConnectionError(
                "環境変数Tokenはアプリから更新できません。再発行後に.envを更新してください。",
                code="ENVIRONMENT_TOKEN_READ_ONLY",
            )
        if account.platform is PublishingPlatform.X:
            bundle = self._oauth.refresh(
                build_x_oauth_config(self._settings),
                client_secret=self._settings.x_client_secret,
                credential_reference=account.credential_reference,
            )
        elif account.platform is PublishingPlatform.INSTAGRAM:
            bundle = self._refresh_instagram(account.credential_reference)
        else:
            bundle = self._oauth.refresh(
                build_tiktok_oauth_config(self._settings),
                client_secret=self._settings.tiktok_client_secret,
                credential_reference=account.credential_reference,
            )
        identity = self._fetch_identity(account.platform, account.credential_reference)
        provider_metadata = (
            self._query_tiktok_creator_info(account.credential_reference)
            if account.platform is PublishingPlatform.TIKTOK
            else {}
        )
        return account.model_copy(
            update={
                "account_id": identity.account_id,
                "display_name": identity.display_name,
                "status": CapabilityAvailability.AVAILABLE,
                "scopes": list(bundle.scopes or account.scopes),
                "token_expiry": bundle.expires_at,
                "last_verified_at": datetime.now(UTC),
                "metadata": {
                    **account.metadata,
                    "username": identity.username,
                    "last_token_refresh_at": datetime.now(UTC).isoformat(),
                    **provider_metadata,
                },
            }
        )

    def refresh_and_save(
        self,
        session: Session,
        account: SocialAccountConnection,
        *,
        actor: str = "operator",
    ) -> SocialAccountConnection:
        updated = self.refresh_account(account)
        self.save_refreshed(session, updated, actor=actor)
        return updated

    def save_refreshed(
        self,
        session: Session,
        updated: SocialAccountConnection,
        *,
        actor: str,
    ) -> None:
        save_account(session, updated)
        self._audit(session, updated, AuditEventType.ACCOUNT_REFRESHED, actor)

    def refresh_tiktok_creator_info_and_save(
        self,
        session: Session,
        account: SocialAccountConnection,
        *,
        actor: str = "operator",
    ) -> SocialAccountConnection:
        updated = self.refresh_tiktok_creator_info(account)
        self.save_refreshed(session, updated, actor=actor)
        return updated

    def refresh_tiktok_creator_info(
        self,
        account: SocialAccountConnection,
    ) -> SocialAccountConnection:
        if account.platform is not PublishingPlatform.TIKTOK:
            raise SocialConnectionError(
                "Creator Info更新はTikTok接続だけが対象です。",
                code="TIKTOK_ACCOUNT_REQUIRED",
            )
        metadata = self._query_tiktok_creator_info(account.credential_reference)
        updated = account.model_copy(
            update={
                "status": CapabilityAvailability.AVAILABLE,
                "last_verified_at": datetime.now(UTC),
                "metadata": {**account.metadata, **metadata},
            }
        )
        return updated

    def should_refresh(
        self,
        account: SocialAccountConnection,
        *,
        within: timedelta = timedelta(hours=24),
    ) -> bool:
        return bool(
            account.status is CapabilityAvailability.AVAILABLE
            and account.token_expiry
            and account.token_expiry <= datetime.now(UTC) + within
            and not account.credential_reference.startswith("env://")
            and account.metadata.get("supports_refresh")
        )

    def disconnect(
        self,
        session: Session,
        account: SocialAccountConnection,
        *,
        actor: str,
        revoke_remote: bool = True,
    ) -> SocialAccountConnection:
        if not actor.strip():
            raise SocialConnectionError("操作担当者が必要です。", code="ACTOR_REQUIRED")
        remote_revoked = False
        reference = account.credential_reference
        if revoke_remote and account.platform in {
            PublishingPlatform.X,
            PublishingPlatform.TIKTOK,
        } and reference:
            if account.platform is PublishingPlatform.X:
                self._revoke_x(reference)
            else:
                self._revoke_tiktok(reference)
            remote_revoked = True
        local_secret_removed = self._delete_secret_safely(reference) if reference else False
        updated = account.model_copy(
            update={
                "status": CapabilityAvailability.NOT_CONFIGURED,
                "scopes": [],
                "credential_reference": "",
                "token_expiry": None,
                "metadata": {
                    **account.metadata,
                    "disconnected_at": datetime.now(UTC).isoformat(),
                    "remote_revoked": remote_revoked,
                    "local_secret_removed": local_secret_removed,
                },
            }
        )
        save_account(session, updated)
        self._audit(session, updated, AuditEventType.ACCOUNT_DISCONNECTED, actor)
        return updated

    def _exchange_instagram(
        self,
        config: OAuthProviderConfig,
        *,
        state: str,
        code: str,
        credential_reference: str,
    ) -> OAuthTokenBundle:
        self._oauth.consume_pending_authorization(config, state=state, code=code)
        short_payload = self._request_json(
            "POST",
            config.token_url,
            data={
                "client_id": config.client_id,
                "client_secret": self._settings.meta_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": config.redirect_uri,
                "code": code,
            },
            phase="INSTAGRAM_SHORT_TOKEN",
        )
        short_entry = self._instagram_token_entry(short_payload)
        short_token = str(short_entry.get("access_token") or "")
        if not short_token:
            raise SocialConnectionError(
                "Instagram短期Token応答にaccess_tokenがありません。",
                code="INSTAGRAM_SHORT_TOKEN_MISSING",
            )
        scopes = self._scope_values(short_entry.get("permissions")) or config.scopes
        long_payload = self._request_json(
            "GET",
            self._settings.instagram_long_lived_token_url,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self._settings.meta_app_secret,
                "access_token": short_token,
            },
            phase="INSTAGRAM_LONG_TOKEN",
        )
        bundle = self._bundle_from_payload("instagram", long_payload, scopes=scopes)
        self._assert_required_scopes(PublishingPlatform.INSTAGRAM, bundle.scopes)
        self._secret_store.set(credential_reference, bundle.to_json())
        return bundle

    def _refresh_instagram(self, credential_reference: str) -> OAuthTokenBundle:
        current = resolve_token_bundle(self._secret_store, credential_reference)
        if current is None:
            raise SocialConnectionError(
                "Instagram TokenがSecret Storeにありません。",
                code="INSTAGRAM_TOKEN_NOT_FOUND",
            )
        payload = self._request_json(
            "GET",
            self._settings.instagram_refresh_token_url,
            params={
                "grant_type": "ig_refresh_token",
                "access_token": current.access_token,
            },
            phase="INSTAGRAM_TOKEN_REFRESH",
        )
        bundle = self._bundle_from_payload(
            "instagram",
            payload,
            scopes=current.scopes or self._REQUIRED_SCOPES[PublishingPlatform.INSTAGRAM],
        )
        self._secret_store.set(credential_reference, bundle.to_json())
        return bundle

    def _fetch_identity(
        self,
        platform: PublishingPlatform,
        credential_reference: str,
    ) -> RemoteIdentity:
        token = resolve_access_token(self._secret_store, credential_reference)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if platform is PublishingPlatform.X:
            payload = self._request_json(
                "GET",
                f"{self._settings.publishing_x_api_base_url.rstrip('/')}/users/me",
                headers=headers,
                phase="X_IDENTITY",
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else None
        elif platform is PublishingPlatform.INSTAGRAM:
            version = self._settings.publishing_meta_graph_api_version.strip().lstrip("/")
            if not version:
                raise SocialConnectionError(
                    "Instagram Graph API versionが未設定です。",
                    code="INSTAGRAM_VERSION_NOT_CONFIGURED",
                )
            payload = self._request_json(
                "GET",
                f"{self._settings.publishing_instagram_api_base_url.rstrip('/')}/{version}/me",
                params={"fields": "id,username,name"},
                headers=headers,
                phase="INSTAGRAM_IDENTITY",
            )
            data = payload
        elif platform is PublishingPlatform.TIKTOK:
            payload = self._request_json(
                "GET",
                f"{self._settings.publishing_tiktok_api_base_url.rstrip('/')}/user/info/",
                params={"fields": "open_id,union_id,avatar_url,display_name"},
                headers=headers,
                phase="TIKTOK_IDENTITY",
            )
            self._assert_tiktok_ok(payload, phase="TIKTOK_IDENTITY")
            response_data = payload.get("data")
            data = response_data.get("user") if isinstance(response_data, dict) else None
        else:
            raise SocialConnectionError(
                "このPlatformの本人確認は未対応です。",
                code="IDENTITY_NOT_SUPPORTED",
            )
        if not isinstance(data, dict):
            raise SocialConnectionError("本人確認応答が正しくありません。", code="IDENTITY_INVALID")
        account_id = str(data.get("open_id") or data.get("id") or "")
        username = str(data.get("display_name") or data.get("username") or "")
        if not account_id or not username:
            raise SocialConnectionError(
                "本人確認応答にAccount IDまたはusernameがありません。",
                code="IDENTITY_FIELDS_MISSING",
            )
        name = str(data.get("name") or data.get("display_name") or "").strip()
        display_name = name or f"@{username}"
        if platform is not PublishingPlatform.TIKTOK and name:
            display_name = f"{name} (@{username})"
        return RemoteIdentity(account_id=account_id, username=username, display_name=display_name)

    def _upsert_account(
        self,
        session: Session,
        *,
        platform: PublishingPlatform,
        identity: RemoteIdentity,
        credential_reference: str,
        bundle: OAuthTokenBundle,
        connected_via: str,
        provider_metadata: dict[str, object] | None = None,
    ) -> SocialAccountConnection:
        required = self._REQUIRED_SCOPES[platform]
        scopes = bundle.scopes or required
        self._assert_required_scopes(platform, scopes)
        existing = get_account_by_external_id(session, platform, identity.account_id)
        final_credential_reference = credential_reference
        if (
            existing
            and connected_via == "oauth"
            and existing.credential_reference
            and existing.credential_reference != credential_reference
            and self._secret_store.supports(existing.credential_reference)
            and not existing.credential_reference.startswith("env://")
        ):
            stored_bundle = self._secret_store.get(credential_reference)
            if stored_bundle:
                self._secret_store.set(existing.credential_reference, stored_bundle)
                self._delete_secret_safely(credential_reference)
                final_credential_reference = existing.credential_reference
        now = datetime.now(UTC)
        metadata = {
            **(existing.metadata if existing else {}),
            "username": identity.username,
            "connected_via": connected_via,
            "scope_source": "provider_response" if bundle.scopes else "requested_scopes",
            "supports_refresh": connected_via == "oauth"
            and (platform is PublishingPlatform.INSTAGRAM or bool(bundle.refresh_token)),
            "credential_storage": final_credential_reference.split("://", maxsplit=1)[0],
            "connected_at": now.isoformat(),
            **(provider_metadata or {}),
        }
        account_values: dict[str, Any] = {
            "platform": platform,
            "account_id": identity.account_id,
            "display_name": identity.display_name,
            "status": CapabilityAvailability.AVAILABLE,
            "scopes": list(scopes),
            "credential_reference": final_credential_reference,
            "token_expiry": bundle.expires_at,
            "last_verified_at": now,
            "metadata": metadata,
            "created_at": existing.created_at if existing else now,
        }
        if existing:
            account_values["connection_id"] = existing.connection_id
        account = SocialAccountConnection(**account_values)
        save_account(session, account)
        return account

    def _revoke_x(self, credential_reference: str) -> None:
        bundle = resolve_token_bundle(self._secret_store, credential_reference)
        if bundle is None:
            raise SocialConnectionError("X Tokenが見つかりません。", code="X_TOKEN_NOT_FOUND")
        try:
            response = self._client.post(
                self._settings.x_oauth_revoke_url,
                data={"token": bundle.access_token},
                auth=httpx.BasicAuth(
                    self._settings.x_client_id,
                    self._settings.x_client_secret,
                ),
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise SocialConnectionError(
                "X Token失効処理がタイムアウトしました。",
                code="X_REVOKE_TIMEOUT",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise SocialConnectionError(
                "X Token失効Endpointへ接続できません。",
                code="X_REVOKE_CONNECTION",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise SocialConnectionError(
                f"X Token失効処理でHTTP {response.status_code}エラーが発生しました。",
                code="X_REVOKE_REJECTED",
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )

    def _revoke_tiktok(self, credential_reference: str) -> None:
        token = resolve_access_token(self._secret_store, credential_reference)
        try:
            response = self._client.post(
                self._settings.tiktok_oauth_revoke_url,
                data={
                    "client_key": self._settings.tiktok_client_key,
                    "client_secret": self._settings.tiktok_client_secret,
                    "token": token,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise SocialConnectionError(
                "TikTok Token失効処理がタイムアウトしました。",
                code="TIKTOK_REVOKE_TIMEOUT",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise SocialConnectionError(
                "TikTok Token失効Endpointへ接続できません。",
                code="TIKTOK_REVOKE_CONNECTION",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise SocialConnectionError(
                f"TikTok Token失効処理でHTTP {response.status_code}エラーが発生しました。",
                code="TIKTOK_REVOKE_REJECTED",
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )

    def _query_tiktok_creator_info(self, credential_reference: str) -> dict[str, object]:
        token = resolve_access_token(self._secret_store, credential_reference)
        payload = self._request_json(
            "POST",
            f"{self._settings.publishing_tiktok_api_base_url.rstrip('/')}/post/publish/creator_info/query/",
            json_body={},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json; charset=UTF-8",
            },
            phase="TIKTOK_CREATOR_INFO",
        )
        self._assert_tiktok_ok(payload, phase="TIKTOK_CREATOR_INFO")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SocialConnectionError(
                "TikTok Creator Info応答が正しくありません。",
                code="TIKTOK_CREATOR_INFO_INVALID",
            )
        privacy_options = data.get("privacy_level_options")
        if not isinstance(privacy_options, list) or not privacy_options:
            raise SocialConnectionError(
                "TikTok Creator Infoに公開範囲がありません。",
                code="TIKTOK_PRIVACY_OPTIONS_MISSING",
            )
        return {
            "creator_info_checked_at": datetime.now(UTC).isoformat(),
            "privacy_level_options": [str(value) for value in privacy_options],
            "comment_disabled": bool(data.get("comment_disabled", False)),
            "duet_disabled": bool(data.get("duet_disabled", False)),
            "stitch_disabled": bool(data.get("stitch_disabled", False)),
            "max_video_post_duration_sec": int(
                data.get("max_video_post_duration_sec", 0) or 0
            ),
            "creator_username": str(data.get("creator_username") or ""),
            "creator_nickname": str(data.get("creator_nickname") or ""),
            "client_audited": self._settings.tiktok_client_audited,
        }

    @staticmethod
    def _assert_tiktok_ok(payload: dict[str, Any], *, phase: str) -> None:
        error = payload.get("error")
        if not isinstance(error, dict):
            return
        code = str(error.get("code") or "")
        if code and code != "ok":
            raise SocialConnectionError(
                f"{phase}がTikTokに拒否されました: {error.get('message') or code}",
                code=f"{phase}_{code[:60]}",
                retryable=code in {"rate_limit_exceeded", "internal_error"},
            )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        phase: str,
        data: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                url,
                data=data,
                json=json_body,
                params=params,
                headers=headers or {"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise SocialConnectionError(
                f"{phase}がタイムアウトしました。",
                code=f"{phase}_TIMEOUT",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise SocialConnectionError(
                f"{phase}へ接続できません。",
                code=f"{phase}_CONNECTION",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise SocialConnectionError(
                f"{phase}でHTTP {response.status_code}エラーが発生しました。",
                code=f"{phase}_REJECTED",
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SocialConnectionError(
                f"{phase}応答をJSONとして読めません。",
                code=f"{phase}_RESPONSE_INVALID",
            ) from exc
        if not isinstance(payload, dict):
            raise SocialConnectionError(
                f"{phase}応答がJSON objectではありません。",
                code=f"{phase}_RESPONSE_INVALID",
            )
        return payload

    def _config(self, platform: PublishingPlatform) -> OAuthProviderConfig:
        try:
            if platform is PublishingPlatform.X:
                if not self._settings.x_oauth_configured:
                    raise SocialConnectionError(
                        "X Client ID・Secret・Redirect URIが未設定です。",
                        code="X_OAUTH_NOT_CONFIGURED",
                    )
                return build_x_oauth_config(self._settings)
            if platform is PublishingPlatform.INSTAGRAM:
                if not self._settings.instagram_publishing_configured:
                    raise SocialConnectionError(
                        "Instagram App・Redirect URI・Graph API versionが未設定です。",
                        code="INSTAGRAM_OAUTH_NOT_CONFIGURED",
                    )
                return build_instagram_oauth_config(self._settings)
            if platform is PublishingPlatform.TIKTOK:
                if not self._settings.tiktok_oauth_configured:
                    raise SocialConnectionError(
                        "TikTok Client Key・Secret・Redirect URIが未設定です。",
                        code="TIKTOK_OAUTH_NOT_CONFIGURED",
                    )
                return build_tiktok_oauth_config(self._settings)
        except ValueError as exc:
            raise SocialConnectionError(str(exc), code="OAUTH_CONFIG_INVALID") from exc
        raise SocialConnectionError(
            "このPlatformのOAuth接続は未対応です。",
            code="PLATFORM_NOT_SUPPORTED",
        )

    @staticmethod
    def _instagram_token_entry(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return payload

    @staticmethod
    def _scope_values(value: object) -> tuple[str, ...]:
        if isinstance(value, list):
            return tuple(str(item) for item in value if str(item))
        return tuple(part for part in str(value or "").replace(",", " ").split() if part)

    @classmethod
    def _bundle_from_payload(
        cls,
        provider: str,
        payload: dict[str, Any],
        *,
        scopes: tuple[str, ...],
    ) -> OAuthTokenBundle:
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise SocialConnectionError(
                "Token応答にaccess_tokenがありません。",
                code="ACCESS_TOKEN_MISSING",
            )
        expires_in = int(payload.get("expires_in", 0) or 0)
        return OAuthTokenBundle(
            access_token=access_token,
            token_type=str(payload.get("token_type") or "Bearer"),
            scopes=scopes,
            expires_at=(datetime.now(UTC) + timedelta(seconds=expires_in)) if expires_in else None,
            provider=provider,
        )

    @classmethod
    def _assert_required_scopes(
        cls,
        platform: PublishingPlatform,
        scopes: tuple[str, ...] | list[str],
    ) -> None:
        missing = set(cls._REQUIRED_SCOPES[platform]) - set(scopes)
        if missing:
            raise SocialConnectionError(
                "必要Scopeが不足しています: " + ", ".join(sorted(missing)),
                code="REQUIRED_SCOPE_MISSING",
            )

    def _credential_reference(self, platform: PublishingPlatform, state: str) -> str:
        suffix = state[-40:]
        return f"{self._writable_secret_prefix}/oauth/credentials/{platform.value}/{suffix}"

    @staticmethod
    def _environment_reference(platform: PublishingPlatform) -> str:
        if platform is PublishingPlatform.X:
            return "env://X_OAUTH_ACCESS_TOKEN"
        if platform is PublishingPlatform.INSTAGRAM:
            return "env://META_ACCESS_TOKEN"
        raise SocialConnectionError(
            "このPlatformのEnvironment Token接続は未対応です。",
            code="PLATFORM_NOT_SUPPORTED",
        )

    def _delete_secret_safely(self, reference: str) -> bool:
        if not reference:
            return False
        try:
            return self._secret_store.delete(reference)
        except SecretStoreError:
            return False

    @staticmethod
    def _audit(
        session: Session,
        account: SocialAccountConnection,
        event_type: AuditEventType,
        actor: str,
    ) -> None:
        save_audit(
            session,
            AuditLog(
                event_type=event_type,
                actor=actor,
                platform=account.platform,
                target_account_id=account.connection_id,
                provider=f"{account.platform.value}_official",
                status=account.status.value,
                metadata={
                    "external_account_id": account.account_id,
                    "credential_reference_scheme": (
                        account.credential_reference.split("://", maxsplit=1)[0]
                        if account.credential_reference
                        else ""
                    ),
                },
            ),
        )

    def _validate_official_endpoints(self) -> None:
        checks = (
            (self._settings.publishing_x_api_base_url, "https://api.x.com/2"),
            (self._settings.x_oauth_authorization_url, "https://x.com/i/oauth2/authorize"),
            (self._settings.x_oauth_token_url, "https://api.x.com/2/oauth2/token"),
            (self._settings.x_oauth_revoke_url, "https://api.x.com/2/oauth2/revoke"),
            (
                self._settings.publishing_instagram_api_base_url,
                "https://graph.instagram.com",
            ),
            (
                self._settings.instagram_oauth_authorization_url,
                "https://www.instagram.com/oauth/authorize",
            ),
            (
                self._settings.instagram_oauth_token_url,
                "https://api.instagram.com/oauth/access_token",
            ),
            (
                self._settings.instagram_long_lived_token_url,
                "https://graph.instagram.com/access_token",
            ),
            (
                self._settings.instagram_refresh_token_url,
                "https://graph.instagram.com/refresh_access_token",
            ),
            (
                self._settings.publishing_tiktok_api_base_url,
                "https://open.tiktokapis.com/v2",
            ),
            (
                self._settings.tiktok_oauth_authorization_url,
                "https://www.tiktok.com/v2/auth/authorize",
            ),
            (
                self._settings.tiktok_oauth_token_url,
                "https://open.tiktokapis.com/v2/oauth/token",
            ),
            (
                self._settings.tiktok_oauth_revoke_url,
                "https://open.tiktokapis.com/v2/oauth/revoke",
            ),
        )
        for value, expected in checks:
            parsed = urlsplit(value)
            if (
                parsed.username
                or parsed.password
                or parsed.fragment
                or value.rstrip("/") != expected
            ):
                raise ValueError(f"OAuth/API endpointは公式URL {expected} のみ利用できます。")


def connection_diagnostics(
    settings: Settings,
    accounts: list[SocialAccountConnection],
) -> list[ConnectionDiagnostic]:
    live_accounts = {
        platform: [
            account
            for account in accounts
            if account.platform is platform
            and account.status is CapabilityAvailability.AVAILABLE
            and bool(account.credential_reference)
        ]
        for platform in (
            PublishingPlatform.X,
            PublishingPlatform.INSTAGRAM,
            PublishingPlatform.TIKTOK,
        )
    }
    return [
        ConnectionDiagnostic(
            platform=PublishingPlatform.X,
            item="Developer App",
            ready=settings.x_oauth_configured,
            status="設定済み" if settings.x_oauth_configured else "未設定",
            required_action="X Developer PortalでClient ID・Secret・Redirect URIを設定",
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.X,
            item="Target Account",
            ready=bool(live_accounts[PublishingPlatform.X]),
            status=(
                f"{len(live_accounts[PublishingPlatform.X])}件接続済み"
                if live_accounts[PublishingPlatform.X]
                else "未接続"
            ),
            required_action="画面のX認証リンクから対象アカウントで同意",
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.INSTAGRAM,
            item="Meta App・Graph version",
            ready=settings.instagram_publishing_configured,
            status="設定済み" if settings.instagram_publishing_configured else "未設定",
            required_action="Meta App、Redirect URI、Graph API versionを設定",
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.INSTAGRAM,
            item="Professional Account",
            ready=bool(live_accounts[PublishingPlatform.INSTAGRAM]),
            status=(
                f"{len(live_accounts[PublishingPlatform.INSTAGRAM])}件接続済み"
                if live_accounts[PublishingPlatform.INSTAGRAM]
                else "未接続"
            ),
            required_action="画面のInstagram認証リンクからProfessional Accountで同意",
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.INSTAGRAM,
            item="投稿画像",
            ready=True,
            status="承認画面を実装済み",
            required_action="各投稿の承認時に公開HTTPS JPEG URLを確認",
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.TIKTOK,
            item="Developer App・Content Posting API",
            ready=settings.tiktok_oauth_configured,
            status="設定済み" if settings.tiktok_oauth_configured else "未設定",
            required_action=(
                "TikTok Developer PortalでLogin Kit・Content Posting API・Redirect URIを設定"
            ),
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.TIKTOK,
            item="Target Account",
            ready=bool(live_accounts[PublishingPlatform.TIKTOK]),
            status=(
                f"{len(live_accounts[PublishingPlatform.TIKTOK])}件接続済み"
                if live_accounts[PublishingPlatform.TIKTOK]
                else "未接続"
            ),
            required_action="画面のTikTok認証リンクから投稿対象アカウントで同意",
        ),
        ConnectionDiagnostic(
            platform=PublishingPlatform.TIKTOK,
            item="Creator Info",
            ready=any(
                bool(account.metadata.get("creator_info_checked_at"))
                for account in live_accounts[PublishingPlatform.TIKTOK]
            ),
            status=(
                "確認済み"
                if any(
                    bool(account.metadata.get("creator_info_checked_at"))
                    for account in live_accounts[PublishingPlatform.TIKTOK]
                )
                else "未確認"
            ),
            required_action="TikTok接続後にCreator Infoを更新して公開範囲を同期",
        ),
    ]
