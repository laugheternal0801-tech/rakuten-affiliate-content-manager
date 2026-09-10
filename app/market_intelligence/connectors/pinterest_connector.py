from __future__ import annotations

from pathlib import Path

from app.market_intelligence.connectors.manual_connector import ManualImportConnector
from app.market_intelligence.schemas import SourceName


class PinterestConnector(ManualImportConnector):
    """Approved-provider/manual import adapter for Pins, boards and trend exports."""

    def __init__(self, import_dir: Path | None) -> None:
        super().__init__(SourceName.PINTEREST, import_dir)
