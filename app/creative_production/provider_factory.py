from __future__ import annotations

from app.config import Settings
from app.creative_production.image_system import (
    ImageProviderRegistry,
    LocalPatternImageProvider,
    OpenAIImageProvider,
    UnavailableImageProvider,
)
from app.creative_production.text_providers import TextProviderRegistry, build_text_registry
from app.creative_production.video_system import (
    LocalStoryboardVideoProvider,
    UnavailableVideoProvider,
    VideoProviderRegistry,
)
from app.market_intelligence.llm import build_market_llm_registry


def create_text_registry(
    settings: Settings,
    *,
    allow_external: bool,
) -> TextProviderRegistry:
    provider_registry, models = build_market_llm_registry(settings)
    providers = (
        {key: provider_registry.get(key) for key in provider_registry.keys}
        if provider_registry
        else {}
    )
    return build_text_registry(providers, models, allow_external=allow_external)


def create_image_registry(
    settings: Settings,
    *,
    allow_external: bool,
) -> ImageProviderRegistry:
    openai = (
        OpenAIImageProvider(
            settings.openai_api_key,
            settings.creative_openai_image_model,
            timeout_seconds=settings.openai_image_timeout_seconds,
        )
        if allow_external
        else UnavailableImageProvider(
            "openai_image",
            settings.creative_openai_image_model,
            "外部API実行が無効です。",
        )
    )
    return ImageProviderRegistry(
        [
            LocalPatternImageProvider(),
            openai,
            UnavailableImageProvider(
                "google_image",
                settings.creative_google_image_model,
                "Google Image adapterは未接続です。",
            ),
            UnavailableImageProvider(
                "flux",
                settings.creative_flux_model,
                "FLUX adapterは未接続です。",
            ),
        ]
    )


def create_video_registry(
    settings: Settings,
    *,
    allow_external: bool,
) -> VideoProviderRegistry:
    external_message = (
        "Provider adapterは未接続です。" if allow_external else "外部API実行が無効です。"
    )
    return VideoProviderRegistry(
        [
            LocalStoryboardVideoProvider(),
            UnavailableVideoProvider(
                "google_video", settings.creative_google_video_model, external_message
            ),
            UnavailableVideoProvider("runway", settings.creative_runway_model, external_message),
            UnavailableVideoProvider("xai_video", settings.creative_xai_model, external_message),
        ]
    )
