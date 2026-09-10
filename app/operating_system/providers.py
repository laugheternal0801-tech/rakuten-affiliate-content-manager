from __future__ import annotations

from app.config import Settings
from app.operating_system.schemas import ProviderProfile
from app.services.ai_council import (
    AnthropicMessagesProvider,
    DemoProvider,
    GeminiGenerateContentProvider,
    LLMProvider,
    OpenAIResponsesProvider,
    ProviderRegistry,
)


def build_provider_registry(
    settings: Settings,
    *,
    include_demo: bool = True,
    maximum_quality: bool = False,
) -> tuple[ProviderRegistry | None, dict[str, str]]:
    """Create all text adapters in one place without exposing credentials."""

    providers: list[LLMProvider] = []
    models: dict[str, str] = {}
    openai_model = (
        settings.ai_council_openai_model if maximum_quality else settings.openai_model
    ).strip()
    anthropic_model = (
        settings.ai_council_anthropic_model if maximum_quality else settings.anthropic_model
    ).strip()
    gemini_model = (
        settings.ai_council_gemini_model if maximum_quality else settings.gemini_model
    ).strip()
    if include_demo:
        providers.append(DemoProvider())
        models["demo"] = "local-demo"
    if settings.openai_api_key and openai_model:
        providers.append(
            OpenAIResponsesProvider(
                settings.openai_api_key,
                timeout_seconds=settings.ai_council_timeout_seconds,
                maximum_quality=maximum_quality,
            )
        )
        models["openai"] = openai_model
    if settings.claude_api_key and anthropic_model:
        providers.append(
            AnthropicMessagesProvider(
                settings.claude_api_key,
                timeout_seconds=settings.ai_council_timeout_seconds,
                maximum_quality=maximum_quality,
            )
        )
        models["anthropic"] = anthropic_model
    if settings.effective_gemini_api_key and gemini_model:
        providers.append(
            GeminiGenerateContentProvider(
                settings.effective_gemini_api_key,
                timeout_seconds=settings.ai_council_timeout_seconds,
                maximum_quality=maximum_quality,
            )
        )
        models["gemini"] = gemini_model
    return (ProviderRegistry(providers) if providers else None), models


def build_provider_profiles(
    settings: Settings,
    registry: ProviderRegistry,
    models: dict[str, str],
) -> list[ProviderProfile]:
    """Build router metadata; costs are configurable planning reserves, not price quotes."""

    default_quality = {"demo": 0.35, "openai": 0.88, "anthropic": 0.90, "gemini": 0.82}
    default_latency = {"demo": 10, "openai": 5_000, "anthropic": 5_500, "gemini": 3_500}
    default_context = {
        "demo": 2_000_000,
        "openai": 200_000,
        "anthropic": 200_000,
        "gemini": 1_000_000,
    }
    profiles: list[ProviderProfile] = []
    for provider in registry.keys:
        model = models[provider]
        profiles.append(
            ProviderProfile(
                provider=provider,
                model=model,
                quality=settings.ai_provider_quality.get(
                    provider, default_quality.get(provider, 0.7)
                ),
                planning_cost_usd=settings.ai_provider_cost_reserves_usd.get(provider, 0.05),
                typical_latency_ms=settings.ai_provider_latency_ms.get(
                    provider, default_latency.get(provider, 6_000)
                ),
                max_context_tokens=settings.ai_provider_context_tokens.get(
                    provider, default_context.get(provider, 100_000)
                ),
                supports_web_search=provider == "openai",
                external=provider != "demo",
                healthy=registry.get(provider).health_check(model),
            )
        )
    return profiles
