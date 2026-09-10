from __future__ import annotations

from collections import Counter

from app.market_intelligence.normalization import tokenize
from app.market_intelligence.schemas import QuantitativeSummary, SocialItem, SourceName


def compute_quantitative_summary(
    items: list[SocialItem],
    competitors: list[str],
    platform_weights: dict[str, float],
) -> QuantitativeSummary:
    platform_counts = Counter(item.platform for item in items)
    keyword_frequency: Counter[str] = Counter()
    competitor_mentions = dict.fromkeys(competitors, 0)
    daily_mentions: Counter[str] = Counter()
    for item in items:
        content = f"{item.title} {item.text}".casefold()
        keyword_frequency.update(tokenize(content))
        for competitor in competitors:
            if competitor.casefold() in content:
                competitor_mentions[competitor] += 1
        timestamp = item.created_at or item.retrieved_at
        daily_mentions[timestamp.date().isoformat()] += 1

    weighted = {
        source: platform_counts[source] * max(0.0, platform_weights.get(source.value, 1.0))
        for source in SourceName
    }
    total_weighted = sum(weighted.values()) or 1.0
    distribution = {source: round(value / total_weighted, 4) for source, value in weighted.items()}
    return QuantitativeSummary(
        total_items=len(items),
        platform_counts={source: platform_counts[source] for source in SourceName},
        total_engagement=sum(item.engagement for item in items),
        keyword_frequency=dict(keyword_frequency.most_common(30)),
        competitor_mentions=competitor_mentions,
        platform_distribution=distribution,
        daily_mentions=dict(sorted(daily_mentions.items())),
        source_quality_average=(
            round(sum(item.quality_score for item in items) / len(items), 4) if items else 0.0
        ),
    )
