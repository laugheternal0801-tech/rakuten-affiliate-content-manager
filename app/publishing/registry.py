from __future__ import annotations

from collections.abc import Iterable

from app.config import Settings
from app.publishing.publishers.base import PublishingProvider
from app.publishing.publishers.generic_web_publisher import GenericWebPublisher
from app.publishing.publishers.instagram_publisher import InstagramPublisher
from app.publishing.publishers.pinterest_publisher import PinterestPublisher
from app.publishing.publishers.reddit_publisher import RedditPublisher
from app.publishing.publishers.tiktok_publisher import TikTokPublisher
from app.publishing.publishers.x_publisher import XPublisher
from app.publishing.publishers.youtube_publisher import YouTubePublisher
from app.publishing.schemas import (
    PublisherCapability,
    PublishingPlatform,
    SocialAccountConnection,
)
from app.publishing.secret_store import SecretStore, build_secret_store


class PublisherRegistry:
    def __init__(self, providers: Iterable[PublishingProvider]) -> None:
        self._providers = {provider.platform: provider for provider in providers}
        if not self._providers:
            raise ValueError("Publishing Providerを1つ以上登録してください。")

    @property
    def providers(self) -> list[PublishingProvider]:
        return list(self._providers.values())

    def get(self, platform: PublishingPlatform) -> PublishingProvider:
        try:
            return self._providers[platform]
        except KeyError as exc:
            raise LookupError(f"{platform.value} Publisher is not registered.") from exc

    async def capabilities(
        self, accounts: dict[PublishingPlatform, SocialAccountConnection] | None = None
    ) -> dict[PublishingPlatform, PublisherCapability]:
        accounts = accounts or {}
        result: dict[PublishingPlatform, PublisherCapability] = {}
        for platform, provider in self._providers.items():
            result[platform] = await provider.get_capabilities(accounts.get(platform))
        return result


def build_publisher_registry(
    settings: Settings,
    *,
    secret_store: SecretStore | None = None,
) -> PublisherRegistry:
    common = {
        "dry_run": settings.publishing_dry_run,
        "publishing_enabled": settings.publishing_enabled,
        "external_api_enabled": settings.publishing_external_api_enabled,
    }

    def override(platform: PublishingPlatform) -> dict[str, object]:
        raw = settings.publishing_platform_capabilities.get(platform.value, {})
        return dict(raw)

    resolved_secret_store = secret_store or build_secret_store(
        x_oauth_access_token=settings.x_oauth_access_token,
        meta_access_token=settings.meta_access_token,
        pinterest_access_token=settings.pinterest_access_token,
    )
    return PublisherRegistry(
        [
            XPublisher(
                dry_run=settings.publishing_dry_run,
                publishing_enabled=settings.publishing_enabled,
                external_api_enabled=settings.publishing_external_api_enabled,
                capability_overrides=override(PublishingPlatform.X),
                secret_store=resolved_secret_store,
                api_base_url=settings.publishing_x_api_base_url,
            ),
            InstagramPublisher(
                dry_run=settings.publishing_dry_run,
                publishing_enabled=settings.publishing_enabled,
                external_api_enabled=settings.publishing_external_api_enabled,
                capability_overrides=override(PublishingPlatform.INSTAGRAM),
                secret_store=resolved_secret_store,
                api_base_url=settings.publishing_instagram_api_base_url,
                api_version=settings.publishing_meta_graph_api_version,
            ),
            TikTokPublisher(
                dry_run=settings.publishing_dry_run,
                publishing_enabled=settings.publishing_enabled,
                external_api_enabled=settings.publishing_external_api_enabled,
                capability_overrides=override(PublishingPlatform.TIKTOK),
                secret_store=resolved_secret_store,
                api_base_url=settings.publishing_tiktok_api_base_url,
            ),
            YouTubePublisher(**common, capability_overrides=override(PublishingPlatform.YOUTUBE)),
            PinterestPublisher(
                dry_run=settings.publishing_dry_run,
                publishing_enabled=settings.publishing_enabled,
                external_api_enabled=settings.publishing_external_api_enabled,
                capability_overrides=override(PublishingPlatform.PINTEREST),
                secret_store=resolved_secret_store,
                api_base_url=settings.publishing_pinterest_api_base_url,
            ),
            RedditPublisher(**common, capability_overrides=override(PublishingPlatform.REDDIT)),
            GenericWebPublisher(
                **common, capability_overrides=override(PublishingPlatform.GENERIC)
            ),
        ]
    )
