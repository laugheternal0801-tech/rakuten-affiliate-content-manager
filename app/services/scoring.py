from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScoreResult:
    total: float
    details: dict[str, dict[str, float | str]]


def normalize_review_count(review_count: int, reference_count: int = 10_000) -> float:
    """Log-normalize review volume so viral products do not dominate the score."""
    count = max(0, int(review_count))
    reference = max(1, int(reference_count))
    return min(1.0, math.log1p(count) / math.log1p(reference))


def _tokens(value: str) -> set[str]:
    normalized = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶー一-龠]+", " ", value.lower())
    words = {word for word in normalized.split() if word}
    compact = normalized.replace(" ", "")
    if compact:
        words.add(compact)
    return words


def keyword_match_ratio(keyword: str, text: str) -> float:
    keywords = _tokens(keyword)
    if not keywords:
        return 0.0
    normalized_text = text.lower()
    matched = sum(1 for token in keywords if token in normalized_text)
    return matched / len(keywords)


def price_fit_ratio(price: int, minimum: int | None, maximum: int | None) -> float:
    if minimum is None and maximum is None:
        return 0.5
    if minimum is not None and maximum is not None:
        if minimum <= price <= maximum:
            return 1.0
        span = max(maximum - minimum, max(maximum, 1) * 0.25, 1)
        distance = minimum - price if price < minimum else price - maximum
        return max(0.0, 1.0 - distance / span)
    if minimum is not None:
        return 1.0 if price >= minimum else max(0.0, price / minimum)
    assert maximum is not None
    return 1.0 if price <= maximum else max(0.0, maximum / max(price, 1))


def calculate_score(
    product: Mapping[str, Any],
    *,
    keyword: str,
    target_min_price: int | None,
    target_max_price: int | None,
    weights: Mapping[str, float],
) -> ScoreResult:
    factors = {
        "affiliate_rate": min(max(float(product.get("affiliate_rate", 0)) / 10, 0), 1),
        "review_count": normalize_review_count(int(product.get("review_count", 0))),
        "review_average": min(max(float(product.get("review_average", 0)) / 5, 0), 1),
        "price_fit": price_fit_ratio(
            int(product.get("item_price", 0)), target_min_price, target_max_price
        ),
        "free_shipping": 1.0 if int(product.get("postage_flag", 1)) == 0 else 0.0,
        "keyword_match": keyword_match_ratio(
            keyword,
            " ".join(
                [
                    str(product.get("item_name", "")),
                    str(product.get("catchcopy", "")),
                    str(product.get("item_caption", "")),
                ]
            ),
        ),
    }
    labels = {
        "affiliate_rate": "アフィリエイト料率",
        "review_count": "レビュー件数（対数正規化）",
        "review_average": "平均評価",
        "price_fit": "希望価格帯との一致",
        "free_shipping": "送料無料",
        "keyword_match": "キーワード一致度",
    }
    weight_total = sum(max(0.0, float(weights.get(key, 0))) for key in factors) or 1.0
    details: dict[str, dict[str, float | str]] = {}
    total = 0.0
    for key, ratio in factors.items():
        normalized_weight = max(0.0, float(weights.get(key, 0))) * 100 / weight_total
        points = ratio * normalized_weight
        total += points
        details[key] = {
            "label": labels[key],
            "points": round(points, 2),
            "max_points": round(normalized_weight, 2),
            "ratio": round(ratio, 4),
        }
    return ScoreResult(total=round(total, 2), details=details)
