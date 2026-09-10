from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from app.market_intelligence.connectors.base import ConnectorQuery, SocialSourceConnector
from app.market_intelligence.connectors.common import generic_normalize
from app.market_intelligence.schemas import (
    AvailabilityStatus,
    ConnectorCapabilities,
    ConnectorHealth,
    DataMode,
    RawSocialItem,
    SocialItem,
    SourceName,
)

MAX_IMPORT_BYTES = 25 * 1024 * 1024


class ManualImportConnector(SocialSourceConnector):
    def __init__(self, source: SourceName, import_dir: Path | None) -> None:
        self.source = source
        self._import_dir = import_dir

    def _candidate(self) -> Path | None:
        if self._import_dir is None:
            return None
        for suffix in ("json", "csv"):
            candidate = self._import_dir / f"{self.source.value}.{suffix}"
            if candidate.is_file():
                return candidate
        return None

    async def health_check(self) -> ConnectorHealth:
        candidate = self._candidate()
        enabled = candidate is not None
        status = AvailabilityStatus.AVAILABLE if enabled else AvailabilityStatus.NOT_CONFIGURED
        return ConnectorHealth(
            source=self.source,
            status=status,
            capabilities=ConnectorCapabilities(
                source=self.source,
                status=status,
                mode=DataMode.MANUAL_IMPORT,
                enabled=enabled,
                historical_search=True,
                recent_search=True,
                metrics=True,
                comments=True,
                manual_import=True,
                details={"expected_file": f"{self.source.value}.json or .csv"},
            ),
            message=(
                f"承認済み手動インポート: {candidate.name}"
                if candidate
                else f"{self.source.value}.json/.csv が未配置です。"
            ),
        )

    def _load_rows(self, path: Path) -> list[dict[str, Any]]:
        if path.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError("手動インポートファイルは25MB以下にしてください。")
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = payload.get("items", [])
            if not isinstance(payload, list):
                raise ValueError("JSONは配列またはitems配列を持つオブジェクトにしてください。")
            return [row for row in payload if isinstance(row, dict)]
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    async def search(self, query: ConnectorQuery) -> list[RawSocialItem]:
        candidate = self._candidate()
        if candidate is None:
            return []
        rows = self._load_rows(candidate)
        tokens = [token.casefold() for token in query.query.split() if len(token) >= 2]
        results: list[RawSocialItem] = []
        for row in rows:
            searchable = " ".join(
                str(row.get(key, "")) for key in ("title", "text", "body", "description")
            ).casefold()
            if tokens and not any(token in searchable for token in tokens):
                continue
            source_url = str(row.get("source_url") or row.get("url") or "")
            source_id = str(row.get("source_id") or row.get("id") or "")
            if not source_id:
                source_id = hashlib.sha256(f"{source_url}|{searchable}".encode()).hexdigest()[:24]
            results.append(
                RawSocialItem(
                    research_run_id=query.research_run_id,
                    platform=self.source,
                    source_id=source_id,
                    source_url=source_url,
                    query=query.query,
                    raw_payload=row,
                    data_mode=DataMode.MANUAL_IMPORT,
                    is_mock=False,
                )
            )
            if len(results) >= query.max_items:
                break
        return results

    async def normalize(self, raw: RawSocialItem, market: str) -> SocialItem:
        return generic_normalize(raw, market)
