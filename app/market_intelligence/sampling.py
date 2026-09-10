from __future__ import annotations

import hashlib
import random

from app.market_intelligence.schemas import SocialItem, SourceName

NEGATIVE_MARKERS = ("不満", "困る", "高い", "遅い", "壊", "bad", "problem", "hate", "difficult")
POSITIVE_MARKERS = ("満足", "便利", "良い", "おすすめ", "love", "good", "best", "easy")


class BalancedSampler:
    """Mix recent, engagement, random, sentiment and platform samples."""

    def sample(self, items: list[SocialItem], max_items: int, seed: str) -> list[SocialItem]:
        if len(items) <= max_items:
            return list(items)
        target_each = max(1, max_items // 6)
        selected: dict[str, SocialItem] = {}

        def take(candidates: list[SocialItem], limit: int = target_each) -> None:
            for item in candidates:
                if len(selected) >= max_items:
                    return
                if item.id not in selected:
                    selected[item.id] = item
                    limit -= 1
                    if limit <= 0:
                        return

        take(sorted(items, key=lambda item: item.created_at or item.retrieved_at, reverse=True))
        take(sorted(items, key=lambda item: item.engagement, reverse=True))
        take(
            [
                item
                for item in items
                if any(marker in item.text.casefold() for marker in NEGATIVE_MARKERS)
            ]
        )
        take(
            [
                item
                for item in items
                if any(marker in item.text.casefold() for marker in POSITIVE_MARKERS)
            ]
        )
        for platform in SourceName:
            take([item for item in items if item.platform is platform], max(1, target_each // 2))

        remaining = [item for item in items if item.id not in selected]
        numeric_seed = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
        random.Random(numeric_seed).shuffle(remaining)  # noqa: S311 - reproducible sample
        take(remaining, max_items - len(selected))
        return list(selected.values())[:max_items]
