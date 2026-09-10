from __future__ import annotations

from pathlib import Path

from app.market_intelligence.connectors.manual_connector import ManualImportConnector
from app.market_intelligence.schemas import SourceName


class InstagramConnector(ManualImportConnector):
    """Meta/approved-provider/manual import adapter without global-search claims."""

    def __init__(self, import_dir: Path | None) -> None:
        super().__init__(SourceName.INSTAGRAM, import_dir)
