from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import Settings
from app.publishing.oauth import (
    OAuthClient,
    OAuthError,
    OAuthProviderConfig,
    build_instagram_oauth_config,
    build_tiktok_oauth_config,
    build_x_oauth_config,
    resolve_access_token,
    resolve_token_bundle,
)
from app.publishing.secret_store import (
    CompositeSecretStore,
    EnvironmentSecretStore,
    MemorySecretStore,
    SecretStoreError,
)


def _config(*, use_pkce: bool = True) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider="pinterest",
        authorization_url="https://www.pinterest.com/oauth/",
        token_url="https://api.pinterest.com/v5/oauth/token",  # noqa: S106
        client_id="client-id",
        redirect_uri="https://localhost:8501/publishing",
        scopes=("boards:read", "boards:write", "pins:read", "pins:write"),
        use_pkce=use_pkce,
        token_parameters={"continuous_refresh": "true"},
    )


def test_environment_secret_store_is_reference_only_and_read_only() -> None:
    store = EnvironmentSecretStore({"PINTEREST_ACCESS_TOKEN": "token-value"})

    assert store.get("env://PINTEREST_ACCESS_TOKEN") == "token-value"
    with pytest.raises(SecretStoreError, match="読み取り専用"):
        store.set("env://PINTEREST_ACCESS_TOKEN", "replacement")


def test_oauth_begin_uses_state_and_pkce_without_exposing_verifier() -> None:
    memory = MemorySecretStore()
    client = OAuthClient(
        memory,
        pending_reference_prefix="memory://oauth/pending",
    )

    request = client.begin_authorization(_config())
    query = parse_qs(urlsplit(request.authorization_url).query)

    assert query["state"] == [request.state]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert "code_verifier" not in query
    assert memory.get(f"memory://oauth/pending/{request.state}")


def test_x_oauth_config_uses_pkce_and_space_separated_scopes() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        x_client_id="x-client-id",
        x_client_secret="x-client-secret",  # noqa: S106
        x_redirect_uri="https://localhost:8501/publishing",
    )
    config = build_x_oauth_config(settings)
    client = OAuthClient(MemorySecretStore(), pending_reference_prefix="memory://oauth/x")

    request = client.begin_authorization(config)
    query = parse_qs(urlsplit(request.authorization_url).query)

    assert config.use_pkce is True
    assert query["scope"] == ["tweet.read tweet.write users.read offline.access"]
    assert query["code_challenge_method"] == ["S256"]


def test_oauth_redirect_allows_loopback_http_but_rejects_remote_http() -> None:
    loopback = _config(use_pkce=False)
    OAuthProviderConfig(
        **{
            **loopback.__dict__,
            "redirect_uri": "http://127.0.0.1:8501/publishing-center",
        }
    )

    with pytest.raises(ValueError, match="HTTPS"):
        OAuthProviderConfig(
            **{
                **loopback.__dict__,
                "redirect_uri": "http://example.com/publishing-center",
            }
        )


def test_instagram_oauth_config_uses_business_login_scopes() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        meta_app_id="meta-app-id",
        meta_app_secret="meta-app-secret",  # noqa: S106
        meta_redirect_uri="https://example.com/publishing-center",
        publishing_meta_graph_api_version="v25.0",
    )

    config = build_instagram_oauth_config(settings)
    client = OAuthClient(MemorySecretStore(), pending_reference_prefix="memory://oauth/ig")
    query = parse_qs(urlsplit(client.begin_authorization(config).authorization_url).query)

    assert config.authorization_url == "https://www.instagram.com/oauth/authorize"
    assert query["scope"] == ["instagram_business_basic,instagram_business_content_publish"]


def test_tiktok_oauth_uses_client_key_and_hex_pkce() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        tiktok_client_key="tiktok-client-key",
        tiktok_client_secret="tiktok-client-secret",  # noqa: S106
        tiktok_redirect_uri="http://127.0.0.1:8501/publishing_center",
    )
    memory = MemorySecretStore()
    config = build_tiktok_oauth_config(settings)
    client = OAuthClient(memory, pending_reference_prefix="memory://oauth/tiktok")

    request = client.begin_authorization(config)
    query = parse_qs(urlsplit(request.authorization_url).query)
    pending = client.pending_authorization(request.state)

    assert "client_id" not in query
    assert query["client_key"] == ["tiktok-client-key"]
    assert query["scope"] == ["user.info.basic,video.publish"]
    assert query["code_challenge"] == [
        hashlib.sha256(pending.code_verifier.encode("ascii")).hexdigest()
    ]


def test_oauth_exchange_stores_bundle_and_state_is_single_use() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        expected = base64.b64encode(b"client-id:client-secret").decode("ascii")
        assert request.headers["Authorization"] == f"Basic {expected}"
        assert b"continuous_refresh=true" in request.content
        return httpx.Response(
            200,
            json={
                "access_token": "access-value",
                "refresh_token": "refresh-value",
                "token_type": "bearer",
                "expires_in": 3600,
                "scope": "boards:read boards:write pins:read pins:write",
            },
        )

    memory = MemorySecretStore()
    transport = httpx.MockTransport(handler)
    oauth = OAuthClient(
        memory,
        client=httpx.Client(transport=transport),
        pending_reference_prefix="memory://oauth/pending",
    )
    config = _config(use_pkce=False)
    authorization = oauth.begin_authorization(config)
    credential_reference = "memory://credentials/pinterest"

    bundle = oauth.exchange_authorization_code(
        config,
        client_secret="client-secret",  # noqa: S106
        state=authorization.state,
        code="authorization-code",
        credential_reference=credential_reference,
    )

    assert len(calls) == 1
    assert bundle.refresh_token == "refresh-value"  # noqa: S105
    assert resolve_access_token(memory, credential_reference) == "access-value"
    stored_bundle = resolve_token_bundle(memory, credential_reference)
    assert stored_bundle is not None
    assert stored_bundle.scopes == (
        "boards:read",
        "boards:write",
        "pins:read",
        "pins:write",
    )
    with pytest.raises(OAuthError, match="使用済み"):
        oauth.exchange_authorization_code(
            config,
            client_secret="client-secret",  # noqa: S106
            state=authorization.state,
            code="authorization-code",
            credential_reference=credential_reference,
        )


def test_composite_store_rejects_unknown_reference_scheme() -> None:
    store = CompositeSecretStore([MemorySecretStore()])

    with pytest.raises(SecretStoreError, match="未対応"):
        store.get("file://unsafe-token")
