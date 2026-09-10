from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.market_intelligence.schemas import SocialItem

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_\-]+|[ぁ-んァ-ヶ一-龠々ー]{2,}")
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
SPAM_MARKERS = ("click here", "今すぐ", "限定", "無料登録", "dm me", "稼げる")
AD_MARKERS = ("#pr", "#ad", "sponsored", "提供", "広告", "アフィリエイト")


def tokenize(text: str) -> list[str]:
    return [match.casefold() for match in TOKEN_PATTERN.findall(text)]


def normalize_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    ]
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/") or "/",
            urlencode(query),
            "",
        )
    )


def detect_language(text: str) -> str | None:
    if not text.strip():
        return None
    japanese = len(re.findall(r"[ぁ-んァ-ヶ一-龠々ー]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if japanese > latin * 0.15:
        return "ja"
    if latin:
        return "en"
    return None


def _simhash(tokens: list[str]) -> int:
    if not tokens:
        return 0
    vector = [0] * 64
    for token in set(tokens):
        digest = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            vector[bit] += 1 if digest & (1 << bit) else -1
    result = 0
    for bit, value in enumerate(vector):
        if value >= 0:
            result |= 1 << bit
    return result


def _duplicate_groups(items: list[SocialItem]) -> dict[str, str]:
    exact: dict[str, list[str]] = defaultdict(list)
    bands: dict[tuple[int, int], list[tuple[str, int]]] = defaultdict(list)
    item_hashes: dict[str, int] = {}
    for item in items:
        content = f"{item.title} {item.text}".casefold().strip()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        exact[digest].append(item.id)
        simhash = _simhash(tokenize(content))
        item_hashes[item.id] = simhash
        for band in range(4):
            bands[(band, (simhash >> (band * 16)) & 0xFFFF)].append((item.id, simhash))

    parent = {item.id: item.id for item in items}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for ids in exact.values():
        for item_id in ids[1:]:
            union(ids[0], item_id)
    compared: set[tuple[str, str]] = set()
    for candidates in bands.values():
        for index, (left_id, left_hash) in enumerate(candidates):
            for right_id, right_hash in candidates[index + 1 :]:
                pair = (left_id, right_id) if left_id <= right_id else (right_id, left_id)
                if pair in compared:
                    continue
                compared.add(pair)
                if (left_hash ^ right_hash).bit_count() <= 3:
                    union(left_id, right_id)

    members: dict[str, list[str]] = defaultdict(list)
    for item_id in parent:
        members[find(item_id)].append(item_id)
    result: dict[str, str] = {}
    for group in members.values():
        if len(group) < 2:
            continue
        group_id = "DUP-" + hashlib.sha256("|".join(sorted(group)).encode()).hexdigest()[:12]
        result.update(dict.fromkeys(group, group_id))
    return result


class NormalizationPipeline:
    def process(
        self,
        items: list[SocialItem],
        *,
        market: str,
        keywords: list[str],
    ) -> list[SocialItem]:
        author_counts = Counter(item.author_id or item.author_name for item in items)
        market_tokens = set(tokenize(" ".join([market, *keywords])))
        interim: list[SocialItem] = []
        for item in items:
            title = " ".join(item.title.split())
            text = " ".join(item.text.split())
            combined = f"{title} {text}".strip()
            tokens = tokenize(combined)
            token_set = set(tokens)
            overlap = len(market_tokens & token_set)
            relevance = overlap / max(1, min(len(market_tokens), 6))
            relevance = min(
                1.0, relevance + (0.25 if item.query.casefold() in combined.casefold() else 0)
            )
            link_count = len(URL_PATTERN.findall(combined))
            repeated_ratio = 1 - (len(set(tokens)) / max(1, len(tokens)))
            marker_hits = sum(marker in combined.casefold() for marker in SPAM_MARKERS)
            spam_score = min(1.0, link_count * 0.12 + repeated_ratio * 0.5 + marker_hits * 0.15)
            author_key = item.author_id or item.author_name
            author_frequency = author_counts.get(author_key, 0) if author_key else 0
            bot_score = min(1.0, max(0, author_frequency - 8) / 30 + repeated_ratio * 0.25)
            ad_hits = sum(marker in combined.casefold() for marker in AD_MARKERS)
            advertisement = min(1.0, ad_hits * 0.35 + link_count * 0.08)
            length_quality = min(1.0, math.log1p(len(combined)) / math.log1p(800))
            metadata_quality = (
                sum(
                    value is not None
                    for value in (item.created_at, item.author_name, item.likes, item.comments)
                )
                / 4
            )
            quality = max(
                0.0,
                min(
                    1.0,
                    length_quality * 0.55
                    + metadata_quality * 0.25
                    + relevance * 0.20
                    - spam_score * 0.25,
                ),
            )
            language = item.language or detect_language(combined)
            interim.append(
                item.model_copy(
                    update={
                        "title": title,
                        "text": text,
                        "source_url": normalize_url(item.source_url),
                        "language": language,
                        "keywords": sorted(market_tokens & token_set),
                        "spam_score": round(spam_score, 4),
                        "bot_score": round(bot_score, 4),
                        "relevance_score": round(relevance, 4),
                        "quality_score": round(quality, 4),
                        "advertisement_likelihood": round(advertisement, 4),
                    }
                )
            )
        duplicates = _duplicate_groups(interim)
        return [
            item.model_copy(update={"duplicate_group": duplicates.get(item.id)}) for item in interim
        ]
