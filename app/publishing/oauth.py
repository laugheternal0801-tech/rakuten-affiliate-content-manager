from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx

from app.publishing.secret_store import (
    SecretStore,
    SecretStoreError,
    default_writable_secret_prefix,
)

if TYPE_CHECKING:
    from app.config import Settings


class OAuthError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


TokenAuthMethod = Literal["client_secret_basic", "client_secret_post"]
ClientIdParameter = Literal["client_id", "client_key"]
PkceChallengeEncoding = Literal["base64url", "hex"]


@dataclass(frozen=True)
class OAuthProviderConfig:
    provider: str
    authorization_url: str
    token_url: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    scope_separator: str = ","
    token_auth_method: TokenAuthMethod = "client_secret_basic"  # noqa: S105
    client_id_parameter: ClientIdParameter = "client_id"
    use_pkce: bool = False
    pkce_challenge_encoding: PkceChallengeEncoding = "base64url"
    authorization_parameters: dict[str, str] = field(default_factory=dict)
    token_parameters: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label, value in (
            ("authorization_url", self.authorization_url),
            ("token_url", self.token_url),
        ):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError(f"{label}はHTTPS URLで指定してください。")
        redirect = urlsplit(self.redirect_uri)
        loopback = redirect.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not redirect.netloc
            or redirect.fragment
            or redirect.username
            or redirect.password
            or redirect.scheme not in {"https", "http"}
            or (redirect.scheme == "http" and not loopback)
        ):
            raise ValueError("redirect_uriはHTTPS、またはlocalhostのHTTP URLで指定してください。")
        if not self.provider or not self.client_id.strip():
            raise ValueError("OAuth providerとclient_idが必要です。")
        if not self.scopes:
            raise ValueError("OAuth scopeを1つ以上指定してください。")
        if self.scope_separator not in {",", " "}:
            raise ValueError("OAuth scope separatorはカンマまたは空白で指定してください。")


@dataclass(frozen=True)
class OAuthAuthorizationRequest:
    provider: str
    authorization_url: str
    state: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class OAuthPendingAuthorization:
    provider: str
    state: str = field(repr=False)
    redirect_uri: str
    code_verifier: str = field(default="", repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=10))

    def to_json(self) -> str:
        return json.dumps(
            {
                "provider": self.provider,
                "state": self.state,
                "redirect_uri": self.redirect_uri,
                "code_verifier": self.code_verifier,
                "created_at": self.created_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> OAuthPendingAuthorization:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("OAuth state payload must be an object.")
        return cls(
            provider=str(payload["provider"]),
            state=str(payload["state"]),
            redirect_uri=str(payload["redirect_uri"]),
            code_verifier=str(payload.get("code_verifier", "")),
            created_at=datetime.fromisoformat(str(payload["created_at"])).astimezone(UTC),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])).astimezone(UTC),
        )


@dataclass(frozen=True)
class OAuthTokenBundle:
    access_token: str = field(repr=False)
    refresh_token: str = field(default="", repr=False)
    token_type: str = "Bearer"  # noqa: S105
    scopes: tuple[str, ...] = ()
    expires_at: datetime | None = None
    provider: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_type": self.token_type,
                "scopes": list(self.scopes),
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "provider": self.provider,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> OAuthTokenBundle:
        payload = json.loads(value)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ValueError("OAuth token bundleが正しくありません。")
        expires_at = payload.get("expires_at")
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token", "")),
            token_type=str(payload.get("token_type", "Bearer")),
            scopes=tuple(str(scope) for scope in payload.get("scopes", [])),
            expires_at=(
                datetime.fromisoformat(str(expires_at)).astimezone(UTC) if expires_at else None
            ),
            provider=str(payload.get("provider", "")),
        )


class OAuthClient:
    def __init__(
        self,
        secret_store: SecretStore,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
        pending_reference_prefix: str | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._pending_prefix = (
            pending_reference_prefix or f"{default_writable_secret_prefix()}/oauth/pending"
        ).rstrip("/")

    def begin_authorization(
        self,
        config: OAuthProviderConfig,
        *,
        ttl_seconds: int = 600,
    ) -> OAuthAuthorizationRequest:
        if not 60 <= ttl_seconds <= 1800:
            raise ValueError("OAuth stateの有効期限は60〜1800秒で指定してください。")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64) if config.use_pkce else ""
        now = datetime.now(UTC)
        pending = OAuthPendingAuthorization(
            provider=config.provider,
            state=state,
            redirect_uri=config.redirect_uri,
            code_verifier=verifier,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._secret_store.set(self._pending_reference(state), pending.to_json())
        parameters = {
            "response_type": "code",
            config.client_id_parameter: config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": config.scope_separator.join(config.scopes),
            "state": state,
            **config.authorization_parameters,
        }
        if verifier:
            digest = hashlib.sha256(verifier.encode("ascii")).digest()
            challenge = (
                digest.hex()
                if config.pkce_challenge_encoding == "hex"
                else base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            )
            parameters.update(
                {
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        return OAuthAuthorizationRequest(
            provider=config.provider,
            authorization_url=f"{config.authorization_url}?{urlencode(parameters)}",
            state=state,
            expires_at=pending.expires_at,
        )

    def exchange_authorization_code(
        self,
        config: OAuthProviderConfig,
        *,
        client_secret: str,
        state: str,
        code: str,
        credential_reference: str,
    ) -> OAuthTokenBundle:
        if not code.strip() or not client_secret.strip():
            raise OAuthError(
                "Authorization codeとClient secretが必要です。", code="MISSING_CREDENTIAL"
            )
        pending = self.consume_pending_authorization(config, state=state, code=code)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            **config.token_parameters,
        }
        if pending.code_verifier:
            data["code_verifier"] = pending.code_verifier
        response = self._token_request(config, client_secret=client_secret, data=data)
        bundle = self._bundle(config.provider, response)
        self._secret_store.set(credential_reference, bundle.to_json())
        return bundle

    def pending_authorization(self, state: str) -> OAuthPendingAuthorization:
        pending_reference = self._pending_reference(state)
        try:
            raw_pending = self._secret_store.get(pending_reference)
        except SecretStoreError as exc:
            raise OAuthError("OAuth stateを読み込めません。", code="STATE_STORE_ERROR") from exc
        if not raw_pending:
            raise OAuthError("OAuth stateが存在しないか使用済みです。", code="STATE_NOT_FOUND")
        try:
            pending = OAuthPendingAuthorization.from_json(raw_pending)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._secret_store.delete(pending_reference)
            raise OAuthError("OAuth stateが破損しています。", code="STATE_INVALID") from exc
        if not secrets.compare_digest(pending.state, state):
            self._secret_store.delete(pending_reference)
            raise OAuthError("OAuth stateが一致しません。", code="STATE_MISMATCH")
        if pending.expires_at <= datetime.now(UTC):
            self._secret_store.delete(pending_reference)
            raise OAuthError("OAuth stateの有効期限が切れています。", code="STATE_EXPIRED")
        return pending

    def consume_pending_authorization(
        self,
        config: OAuthProviderConfig,
        *,
        state: str,
        code: str,
    ) -> OAuthPendingAuthorization:
        if not code.strip():
            raise OAuthError("Authorization codeが必要です。", code="MISSING_CODE")
        pending_reference = self._pending_reference(state)
        pending = self.pending_authorization(state)
        if pending.provider != config.provider or pending.redirect_uri != config.redirect_uri:
            self._secret_store.delete(pending_reference)
            raise OAuthError(
                "OAuth callbackのProviderまたはRedirect URIが一致しません。",
                code="STATE_CONTEXT_MISMATCH",
            )
        self._secret_store.delete(pending_reference)
        return pending

    def discard_pending_authorization(self, state: str) -> bool:
        """Remove a pending callback after provider denial or operator cancellation."""
        try:
            return self._secret_store.delete(self._pending_reference(state))
        except SecretStoreError as exc:
            raise OAuthError("OAuth stateを削除できません。", code="STATE_STORE_ERROR") from exc

    def refresh(
        self,
        config: OAuthProviderConfig,
        *,
        client_secret: str,
        credential_reference: str,
    ) -> OAuthTokenBundle:
        current = resolve_token_bundle(self._secret_store, credential_reference)
        if current is None or not current.refresh_token:
            raise OAuthError("Refresh tokenが保存されていません。", code="REFRESH_TOKEN_MISSING")
        response = self._token_request(
            config,
            client_secret=client_secret,
            data={
                "grant_type": "refresh_token",
                "refresh_token": current.refresh_token,
                **config.token_parameters,
            },
        )
        updated = self._bundle(config.provider, response, fallback=current)
        self._secret_store.set(credential_reference, updated.to_json())
        return updated

    def _token_request(
        self,
        config: OAuthProviderConfig,
        *,
        client_secret: str,
        data: dict[str, str],
    ) -> dict[str, Any]:
        request_data = dict(data)
        auth: httpx.BasicAuth | None = None
        if config.token_auth_method == "client_secret_basic":  # noqa: S105
            auth = httpx.BasicAuth(config.client_id, client_secret)
        else:
            request_data.update(
                {
                    config.client_id_parameter: config.client_id,
                    "client_secret": client_secret,
                }
            )
        try:
            if auth is None:
                response = self._client.post(
                    config.token_url,
                    data=request_data,
                    headers={"Accept": "application/json"},
                )
            else:
                response = self._client.post(
                    config.token_url,
                    data=request_data,
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise OAuthError(
                "OAuth token endpointがタイムアウトしました。", code="TOKEN_TIMEOUT", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise OAuthError(
                "OAuth token endpointへ接続できません。", code="TOKEN_CONNECTION", retryable=True
            ) from exc
        if not response.is_success:
            error_code = "TOKEN_REJECTED"
            try:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("error"):
                    error_code = str(payload["error"])[:80]
            except ValueError:
                pass
            raise OAuthError(
                f"OAuth token endpointでHTTP {response.status_code}エラーが発生しました。",
                code=error_code,
                retryable=response.status_code in {429, 500, 502, 503, 504},
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthError(
                "OAuth token応答をJSONとして読めません。", code="TOKEN_RESPONSE_INVALID"
            ) from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise OAuthError(
                "OAuth token応答にaccess_tokenがありません。", code="ACCESS_TOKEN_MISSING"
            )
        return payload

    @staticmethod
    def _bundle(
        provider: str,
        payload: dict[str, Any],
        fallback: OAuthTokenBundle | None = None,
    ) -> OAuthTokenBundle:
        expires_in = int(payload.get("expires_in", 0) or 0)
        raw_scope = payload.get("scope") or payload.get("permissions") or ""
        scopes = (
            tuple(str(value) for value in raw_scope)
            if isinstance(raw_scope, list)
            else tuple(part for part in str(raw_scope).replace(",", " ").split() if part)
        )
        return OAuthTokenBundle(
            access_token=str(payload["access_token"]),
            refresh_token=str(
                payload.get("refresh_token") or (fallback.refresh_token if fallback else "")
            ),
            token_type=str(
                payload.get("token_type") or (fallback.token_type if fallback else "Bearer")
            ),
            scopes=scopes or (fallback.scopes if fallback else ()),
            expires_at=(datetime.now(UTC) + timedelta(seconds=expires_in)) if expires_in else None,
            provider=provider,
        )

    def _pending_reference(self, state: str) -> str:
        if not state or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in state
        ):
            raise OAuthError("OAuth stateの形式が正しくありません。", code="STATE_FORMAT_INVALID")
        return f"{self._pending_prefix}/{state}"


def resolve_token_bundle(
    secret_store: SecretStore,
    credential_reference: str,
) -> OAuthTokenBundle | None:
    if not credential_reference:
        return None
    raw = secret_store.get(credential_reference)
    if not raw:
        return None
    try:
        return OAuthTokenBundle.from_json(raw)
    except (ValueError, json.JSONDecodeError):
        return OAuthTokenBundle(access_token=raw)


def resolve_access_token(secret_store: SecretStore, credential_reference: str) -> str:
    bundle = resolve_token_bundle(secret_store, credential_reference)
    if bundle is None or not bundle.access_token:
        raise OAuthError(
            "Access tokenをSecret Storeから取得できません。", code="ACCESS_TOKEN_NOT_FOUND"
        )
    if bundle.expires_at and bundle.expires_at <= datetime.now(UTC):
        raise OAuthError("Access tokenの有効期限が切れています。", code="ACCESS_TOKEN_EXPIRED")
    return bundle.access_token


def build_pinterest_oauth_config(settings: Settings) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider="pinterest",
        authorization_url=settings.pinterest_oauth_authorization_url,
        token_url=settings.pinterest_oauth_token_url,
        client_id=settings.pinterest_app_id,
        redirect_uri=settings.pinterest_redirect_uri,
        scopes=(
            "boards:read",
            "boards:write",
            "pins:read",
            "pins:write",
            "user_accounts:read",
        ),
        token_auth_method="client_secret_basic",  # noqa: S106
        use_pkce=False,
        token_parameters=(
            {"continuous_refresh": "true"} if settings.pinterest_oauth_continuous_refresh else {}
        ),
    )


def build_x_oauth_config(settings: Settings) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider="x",
        authorization_url=settings.x_oauth_authorization_url,
        token_url=settings.x_oauth_token_url,
        client_id=settings.x_client_id,
        redirect_uri=settings.x_redirect_uri,
        scopes=("tweet.read", "tweet.write", "users.read", "offline.access"),
        scope_separator=" ",
        token_auth_method="client_secret_basic",  # noqa: S106
        use_pkce=True,
    )


def build_instagram_oauth_config(settings: Settings) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider="instagram",
        authorization_url=settings.instagram_oauth_authorization_url,
        token_url=settings.instagram_oauth_token_url,
        client_id=settings.meta_app_id,
        redirect_uri=settings.meta_redirect_uri,
        scopes=(
            "instagram_business_basic",
            "instagram_business_content_publish",
        ),
        scope_separator=",",
        token_auth_method="client_secret_post",  # noqa: S106
        use_pkce=False,
    )


def build_tiktok_oauth_config(settings: Settings) -> OAuthProviderConfig:
    redirect = urlsplit(settings.tiktok_redirect_uri)
    if redirect.hostname in {"localhost", "127.0.0.1", "::1"} and redirect.port is None:
        raise ValueError("TikTokのlocalhost Redirect URIにはportが必要です。")
    return OAuthProviderConfig(
        provider="tiktok",
        authorization_url=settings.tiktok_oauth_authorization_url,
        token_url=settings.tiktok_oauth_token_url,
        client_id=settings.tiktok_client_key,
        client_id_parameter="client_key",
        redirect_uri=settings.tiktok_redirect_uri,
        scopes=("user.info.basic", "video.publish"),
        scope_separator=",",
        token_auth_method="client_secret_post",  # noqa: S106
        use_pkce=settings.tiktok_oauth_use_pkce,
        pkce_challenge_encoding="hex",
    )
