from __future__ import annotations

from collections import Counter
from threading import Lock
from typing import Literal

from app.market_intelligence.schemas import (
    EvidenceRecord,
    ResearchRequest,
    SocialItem,
    SourceName,
)


class EvidenceStore:
    def __init__(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._lock = Lock()

    @property
    def records(self) -> list[EvidenceRecord]:
        return list(self._records.values())

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self._records.get(evidence_id)

    def add_for_items(
        self,
        *,
        run_id: str,
        request: ResearchRequest,
        claim: str,
        items: list[SocialItem],
        support_score: float,
        query: str = "",
        metrics: dict[str, float | int | str | None] | None = None,
    ) -> EvidenceRecord:
        if not items:
            raise ValueError("Evidenceには1件以上の元データが必要です。")
        platforms = Counter(item.platform for item in items)
        platform: SourceName | Literal["cross_source"] = (
            next(iter(platforms)) if len(platforms) == 1 else "cross_source"
        )
        record = EvidenceRecord(
            research_run_id=run_id,
            platform=platform,
            source_item_ids=[item.id for item in items],
            claim=claim,
            sample_size=len(items),
            query=query or " / ".join(dict.fromkeys(item.query for item in items)),
            date_from=request.date_from,
            date_to=request.date_to,
            metrics={
                "platform_count": len(platforms),
                "average_quality": round(sum(item.quality_score for item in items) / len(items), 4),
                **(metrics or {}),
            },
            support_score=max(0.0, min(1.0, support_score)),
        )
        with self._lock:
            self._records[record.evidence_id] = record
        return record
