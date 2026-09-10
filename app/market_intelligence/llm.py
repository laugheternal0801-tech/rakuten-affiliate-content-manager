from __future__ import annotations

from app.config import Settings
from app.operating_system.providers import build_provider_registry
from app.services.ai_council import ProviderRegistry


def build_market_llm_registry(
    settings: Settings,
) -> tuple[ProviderRegistry | None, dict[str, str]]:
    return build_provider_registry(settings, include_demo=False)
