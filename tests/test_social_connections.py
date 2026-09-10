from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base
from app.publishing.oauth import OAuthError, resolve_access_token, resolve_token_bundle
from app.publishing.repositories import get_account, list_accounts
from app.publishing.schemas import CapabilityAvailability, PublishingPlatform
from app.publishing.secret_store import MemorySecretStore
from app.publishing.social_connections import SocialConnectionService


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        x_client_id="x-client-id",
        x_client_secret="x-client-secret",  # noqa: S106
        x_redirect_uri="http://localhost:8501/publishing-center",
        meta_app_id="meta-app-id",
        meta_app_secret="meta-app-secret",  # noqa: S106
        meta_redirect_uri="https://example.com/publishing-center",
        publishing_meta_graph_api_version="v25.0",
        tiktok_client_key="tiktok-client-key",
        tiktok_client_secret="tiktok-client-secret",  # noqa: S106
        tiktok_redirect_uri="http://127.0.0.1:8501/publishing_center",
    )


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_x_callback_registers_verified_account_without_token_in_database() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/2/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code"] == ["x-code"]
            assert form["code_verifier"][0]
            return httpx.Response(
                200,
                json={
                    "access_token": "x-access-value",
                    "refresh_token": "x-refresh-value",
                    "expires_in": 7200,
                    "scope": "tweet.read tweet.write users.read offline.access",
                },
            )
        assert request.url.path == "/2/users/me"
        assert request.headers["Authorization"] == "Bearer x-access-value"
        return httpx.Response(
            200,
            json={"data": {"id": "123456", "username": "brand_jp", "name": "Brand"}},
        )

    store = MemorySecretStore()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = SocialConnectionService(
        _settings(),
        secret_store=store,
        client=client,
        writable_reference_prefix="memory://social",
    )
    authorization = service.begin(PublishingPlatform.X)

    with _session() as session:
        account = service.complete_callback(session, state=authorization.state, code="x-code")
        session.commit()
        stored = get_account(session, account.connection_id)

        assert stored is not None
        assert stored.account_id == "123456"
        assert stored.status is CapabilityAvailability.AVAILABLE
        assert "x-access-value" not in stored.model_dump_json()
        assert resolve_access_token(store, stored.credential_reference) == "x-access-value"
        assert len(list_accounts(session)) == 1

    with pytest.raises(OAuthError, match="使用済み"):
        with _session() as session:
            service.complete_callback(session, state=authorization.state, code="x-code")
    assert len(requests) == 2


def test_instagram_callback_exchanges_long_lived_token_and_refreshes() -> None:
    phases: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.instagram.com":
            phases.append("short")
            form = parse_qs(request.content.decode())
            assert form["code"] == ["ig-code"]
            assert form["client_secret"] == ["meta-app-secret"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "access_token": "ig-short-value",
                            "user_id": "17841400000000000",
                            "permissions": [
                                "instagram_business_basic",
                                "instagram_business_content_publish",
                            ],
                        }
                    ]
                },
            )
        if request.url.path == "/access_token":
            phases.append("long")
            assert request.url.params["access_token"] == "ig-short-value"  # noqa: S105
            return httpx.Response(
                200,
                json={"access_token": "ig-long-value", "token_type": "bearer", "expires_in": 3600},
            )
        if request.url.path == "/refresh_access_token":
            phases.append("refresh")
            assert request.url.params["access_token"] == "ig-long-value"  # noqa: S105
            return httpx.Response(
                200,
                json={
                    "access_token": "ig-refreshed-value",
                    "token_type": "bearer",
                    "expires_in": 5_184_000,
                },
            )
        assert request.url.path == "/v25.0/me"
        phases.append("identity")
        assert request.headers["Authorization"] in {
            "Bearer ig-long-value",
            "Bearer ig-refreshed-value",
        }
        return httpx.Response(
            200,
            json={"id": "17841400000000000", "username": "brand_jp", "name": "Brand"},
        )

    store = MemorySecretStore()
    service = SocialConnectionService(
        _settings(),
        secret_store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        writable_reference_prefix="memory://social",
    )
    authorization = service.begin(PublishingPlatform.INSTAGRAM)

    with _session() as session:
        account = service.complete_callback(session, state=authorization.state, code="ig-code")
        session.commit()
        bundle = resolve_token_bundle(store, account.credential_reference)
        assert bundle is not None
        assert bundle.access_token == "ig-long-value"  # noqa: S105
        stored_value = store.get(account.credential_reference)
        assert stored_value is not None
        assert "ig-short-value" not in stored_value

        expiring = account.model_copy(
            update={
                "token_expiry": datetime.now(UTC) + timedelta(minutes=10),
                "metadata": {**account.metadata, "supports_refresh": True},
            }
        )
        refreshed = service.refresh_and_save(session, expiring, actor="test")
        session.commit()
        assert resolve_access_token(store, refreshed.credential_reference) == "ig-refreshed-value"
        assert refreshed.token_expiry is not None
        assert refreshed.token_expiry > datetime.now(UTC) + timedelta(days=59)

    assert phases == ["short", "long", "identity", "refresh", "identity"]


def test_x_disconnect_revokes_remote_token_and_removes_local_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/2/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "x-access-value",
                    "refresh_token": "x-refresh-value",
                    "scope": "tweet.read tweet.write users.read offline.access",
                },
            )
        if request.url.path == "/2/users/me":
            return httpx.Response(
                200,
                json={"data": {"id": "123456", "username": "brand_jp"}},
            )
        assert request.url.path == "/2/oauth2/revoke"
        assert parse_qs(request.content.decode())["token"] == ["x-access-value"]
        return httpx.Response(200, json={})

    store = MemorySecretStore()
    service = SocialConnectionService(
        _settings(),
        secret_store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        writable_reference_prefix="memory://social",
    )
    authorization = service.begin(PublishingPlatform.X)
    with _session() as session:
        account = service.complete_callback(session, state=authorization.state, code="x-code")
        reference = account.credential_reference
        disconnected = service.disconnect(session, account, actor="test", revoke_remote=True)
        session.commit()

        assert disconnected.status is CapabilityAvailability.NOT_CONFIGURED
        assert disconnected.credential_reference == ""
        assert store.get(reference) is None


def test_tiktok_callback_registers_creator_info_and_rotating_refresh_token() -> None:
    phases: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/oauth/revoke/":
            form = parse_qs(request.content.decode())
            phases.append("revoke")
            assert form["token"] == ["tiktok-access-2"]
            return httpx.Response(200)
        if request.url.path == "/v2/oauth/token/":
            form = parse_qs(request.content.decode())
            phases.append(str(form["grant_type"][0]))
            assert form["client_key"] == ["tiktok-client-key"]
            assert form["client_secret"] == ["tiktok-client-secret"]
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0]
                return httpx.Response(
                    200,
                    json={
                        "access_token": "tiktok-access-1",
                        "refresh_token": "tiktok-refresh-1",
                        "expires_in": 86_400,
                        "scope": "user.info.basic,video.publish",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "tiktok-access-2",
                    "refresh_token": "tiktok-refresh-2",
                    "expires_in": 86_400,
                    "scope": "user.info.basic,video.publish",
                },
            )
        if request.url.path == "/v2/user/info/":
            phases.append("identity")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "open_id": "open-id-123",
                            "display_name": "TikTok Brand",
                        }
                    },
                    "error": {"code": "ok", "message": "", "log_id": "log-1"},
                },
            )
        assert request.url.path == "/v2/post/publish/creator_info/query/"
        phases.append("creator_info")
        return httpx.Response(
            200,
            json={
                "data": {
                    "creator_username": "brand_jp",
                    "creator_nickname": "Brand",
                    "privacy_level_options": ["SELF_ONLY"],
                    "comment_disabled": False,
                    "duet_disabled": True,
                    "stitch_disabled": False,
                    "max_video_post_duration_sec": 180,
                },
                "error": {"code": "ok", "message": "", "log_id": "log-2"},
            },
        )

    store = MemorySecretStore()
    service = SocialConnectionService(
        _settings(),
        secret_store=store,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        writable_reference_prefix="memory://social",
    )
    authorization = service.begin(PublishingPlatform.TIKTOK)

    with _session() as session:
        account = service.complete_callback(session, state=authorization.state, code="tt-code")
        assert account.account_id == "open-id-123"
        assert account.metadata["privacy_level_options"] == ["SELF_ONLY"]
        assert account.metadata["duet_disabled"] is True
        assert account.metadata["max_video_post_duration_sec"] == 180
        assert "tiktok-access-1" not in account.model_dump_json()

        refreshed = service.refresh_and_save(session, account, actor="test")
        bundle = resolve_token_bundle(store, refreshed.credential_reference)
        assert bundle is not None
        assert bundle.access_token == "tiktok-access-2"  # noqa: S105
        assert bundle.refresh_token == "tiktok-refresh-2"  # noqa: S105
        reference = refreshed.credential_reference
        disconnected = service.disconnect(session, refreshed, actor="test", revoke_remote=True)
        assert disconnected.status is CapabilityAvailability.NOT_CONFIGURED
        assert store.get(reference) is None

    assert phases == [
        "authorization_code",
        "identity",
        "creator_info",
        "refresh_token",
        "identity",
        "creator_info",
        "revoke",
    ]
