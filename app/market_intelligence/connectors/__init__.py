"""Official API and approved manual-import source connectors."""

from app.market_intelligence.connectors.registry import build_source_registry

__all__ = ["build_source_registry"]
