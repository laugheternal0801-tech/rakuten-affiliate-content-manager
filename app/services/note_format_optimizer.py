from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from defusedxml import ElementTree as ET

LOGGER = logging.getLogger(__name__)

NOTE_HOST = "note.com"
NOTE_ROBOTS_URL = "https://note.com/robots.txt"
DEFAULT_USER_AGENT = (
    "RakutenAffiFormatResearch/1.0 "
    "(+https://github.com/laugheternal0801-tech/rakuten-affiliate-content-manager)"
)
DEFAULT_SOURCE_URL = "https://note.com/notemagazine/m/mf2e92ffd6658/rss"
DEFAULT_PLAYBOOK_PATH = Path(__file__).resolve().parents[2] / "data" / "note_format_playbook.json"
ARTICLE_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]+/n/n[0-9a-f]+$")
BODY_RE = re.compile(r'\bbody:"((?:\\.|[^"\\])*)",separator:', re.DOTALL)
LIKE_COUNT_RE = re.compile(r"\blikeCount:(\d+),anonymousLikeCount:")
VISIBLE_LIKE_RE = re.compile(
    r'<[^>]+class="[^"]*\bo-noteLikeV3__count\b[^"]*"[^>]*>\s*([\d,]+)\s*</',
    re.DOTALL,
)
PUBLISHED_RE = re.compile(r'"datePublished":"([^"]+)"')


@dataclass(frozen=True)
class FormatFeatures:
    title_chars: int
    body_chars: int
    intro_chars: int
    h2_count: int
    h3_count: int
    paragraph_count: int
    median_paragraph_chars: int
    list_count: int
    list_item_count: int
    image_count: int
    blockquote_count: int
    separator_count: int


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h2_count = 0
        self.h3_count = 0
        self.list_count = 0
        self.list_item_count = 0
        self.image_count = 0
        self.blockquote_count = 0
        self.separator_count = 0
        self._stack: list[str] = []
        self._paragraph_text: list[str] | None = None
        self._body_text: list[str] = []
        self._intro_text: list[str] = []
        self._seen_heading = False
        self.paragraph_lengths: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        self._stack.append(tag)
        if tag == "h2":
            self.h2_count += 1
            self._seen_heading = True
        elif tag == "h3":
            self.h3_count += 1
            self._seen_heading = True
        elif tag == "p":
            self._paragraph_text = []
        elif tag in {"ul", "ol"}:
            self.list_count += 1
        elif tag == "li":
            self.list_item_count += 1
        elif tag == "img":
            self.image_count += 1
        elif tag == "blockquote":
            self.blockquote_count += 1
        elif tag == "hr":
            self.separator_count += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "p" and self._paragraph_text is not None:
            length = len("".join(self._paragraph_text).strip())
            if length:
                self.paragraph_lengths.append(length)
            self._paragraph_text = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        self._body_text.append(clean)
        if not self._seen_heading:
            self._intro_text.append(clean)
        if self._paragraph_text is not None:
            self._paragraph_text.append(clean)

    def features(self, title_chars: int) -> FormatFeatures:
        median_paragraph = (
            round(statistics.median(self.paragraph_lengths)) if self.paragraph_lengths else 0
        )
        return FormatFeatures(
            title_chars=max(0, title_chars),
            body_chars=len("".join(self._body_text)),
            intro_chars=len("".join(self._intro_text)),
            h2_count=self.h2_count,
            h3_count=self.h3_count,
            paragraph_count=len(self.paragraph_lengths),
            median_paragraph_chars=median_paragraph,
            list_count=self.list_count,
            list_item_count=self.list_item_count,
            image_count=self.image_count,
            blockquote_count=self.blockquote_count,
            separator_count=self.separator_count,
        )


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_note_article_url(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname != NOTE_HOST:
        return None
    if not ARTICLE_PATH_RE.fullmatch(parsed.path):
        return None
    return f"https://{NOTE_HOST}{parsed.path}"


def _decode_js_string(value: str) -> str:
    decoded = json.loads(f'"{value}"')
    if not isinstance(decoded, str):
        raise ValueError("記事本文を文字列として読み取れませんでした")
    return decoded


def extract_public_article_snapshot(
    html: str,
    *,
    url: str,
    title: str = "",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Extract transient structural metrics; article text is never returned or stored."""
    safe_url = _safe_note_article_url(url)
    if not safe_url:
        raise ValueError("許可されていないnote記事URLです")
    if len(html.encode("utf-8")) > 3_000_000:
        raise ValueError("note記事ページが取得上限を超えています")

    body_match = BODY_RE.search(html)
    if body_match is None:
        raise ValueError("公開本文の構造を読み取れませんでした")
    body_html = _decode_js_string(body_match.group(1))
    parser = _StructureParser()
    parser.feed(body_html)
    features = parser.features(len(title.strip()))
    if features.body_chars < 100:
        raise ValueError("分析できる公開本文が不足しています")

    like_match = LIKE_COUNT_RE.search(html)
    if like_match is not None:
        likes = int(like_match.group(1))
    else:
        visible_match = VISIBLE_LIKE_RE.search(html)
        if visible_match is None:
            raise ValueError("公開されているスキ数を読み取れませんでした")
        likes = int(visible_match.group(1).replace(",", ""))

    published_match = PUBLISHED_RE.search(html)
    published_at = published_match.group(1) if published_match else ""
    observed = observed_at or datetime.now(UTC)
    return {
        "observed_at": _isoformat(observed),
        "likes": max(0, likes),
        "published_at": published_at,
        "features": asdict(features),
    }


def parse_rss_article_urls(xml_text: str, limit: int = 30) -> list[dict[str, str]]:
    if len(xml_text.encode("utf-8")) > 1_000_000 or "<!DOCTYPE" in xml_text.upper():
        raise ValueError("RSSの形式またはサイズが安全上の制限を超えています")
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for element in root.findall("./channel/item"):
        url = _safe_note_article_url(element.findtext("link", default=""))
        if not url:
            continue
        items.append(
            {
                "url": url,
                "title": element.findtext("title", default="").strip(),
                "published_at": element.findtext("pubDate", default="").strip(),
            }
        )
        if len(items) >= max(1, min(limit, 50)):
            break
    return items


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
    return loaded if isinstance(loaded, dict) else default


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _robots_parser(text: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.set_url(NOTE_ROBOTS_URL)
    parser.parse(text.splitlines())
    return parser


def _weighted_median(values: Iterable[tuple[float, float]]) -> int:
    ordered = sorted((float(value), max(float(weight), 0.0)) for value, weight in values)
    total = sum(weight for _, weight in ordered)
    if not ordered or total <= 0:
        return 0
    midpoint = total / 2
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= midpoint:
            return round(value)
    return round(ordered[-1][0])


def build_format_playbook(
    history: Mapping[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    generated_at = now or datetime.now(UTC)
    candidates: list[dict[str, Any]] = []
    articles = history.get("articles", {})
    if isinstance(articles, dict):
        for article in articles.values():
            if not isinstance(article, dict):
                continue
            observations = article.get("observations", [])
            if not isinstance(observations, list) or len(observations) < 2:
                continue
            valid = [item for item in observations if isinstance(item, dict)]
            valid.sort(key=lambda item: str(item.get("observed_at", "")))
            previous, latest = valid[-2:]
            try:
                hours = (
                    _parse_datetime(str(latest["observed_at"]))
                    - _parse_datetime(str(previous["observed_at"]))
                ).total_seconds() / 3600
                delta = int(latest["likes"]) - int(previous["likes"])
                features = latest["features"]
            except (KeyError, TypeError, ValueError):
                continue
            if hours < 6 or delta <= 0 or not isinstance(features, dict):
                continue
            growth_per_day = min(delta / hours * 24, 10_000.0)
            candidates.append(
                {
                    "growth_per_day": growth_per_day,
                    "weight": 1.0 + math.log1p(growth_per_day),
                    "features": features,
                }
            )

    candidates.sort(key=lambda item: float(item["growth_per_day"]), reverse=True)
    selected = candidates[:20]
    sample_size = len(selected)
    if sample_size < 5:
        return {
            "version": 1,
            "updated_at": _isoformat(generated_at),
            "status": "warming_up",
            "confidence": "low",
            "sample_size": sample_size,
            "method": "public_like_growth_and_structure_only",
            "recommendations": {},
        }

    def metric(name: str) -> int:
        pairs: list[tuple[float, float]] = []
        for item in selected:
            features = item["features"]
            value = features.get(name)
            if isinstance(value, int | float):
                pairs.append((float(value), float(item["weight"])))
        return _weighted_median(pairs)

    recommendations = {
        "title_chars": min(max(metric("title_chars"), 20), 50),
        "intro_chars": min(max(metric("intro_chars"), 120), 320),
        "paragraph_chars": min(max(metric("median_paragraph_chars"), 45), 150),
        "h2_count": min(max(metric("h2_count"), 4), 10),
        "h3_count": min(max(metric("h3_count"), 0), 12),
        "list_count": min(max(metric("list_count"), 0), 8),
        "separator_count": min(max(metric("separator_count"), 0), 5),
    }
    return {
        "version": 1,
        "updated_at": _isoformat(generated_at),
        "status": "active",
        "confidence": "high" if sample_size >= 10 else "medium",
        "sample_size": sample_size,
        "method": "public_like_growth_and_structure_only",
        "recommendations": recommendations,
    }


def _sanitize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    sources = config.get("sources", [])
    safe_sources: list[str] = []
    if isinstance(sources, list):
        for source in sources:
            value = source.get("url") if isinstance(source, dict) else source
            if not isinstance(value, str):
                continue
            parsed = urlparse(value)
            if parsed.scheme == "https" and parsed.hostname == NOTE_HOST and parsed.path.endswith(
                "/rss"
            ):
                safe_sources.append(value)
    return {
        "sources": safe_sources or [DEFAULT_SOURCE_URL],
        "max_articles_per_run": min(max(int(config.get("max_articles_per_run", 30)), 5), 50),
        "tracking_days": min(max(int(config.get("tracking_days", 10)), 2), 30),
        "request_delay_seconds": min(
            max(float(config.get("request_delay_seconds", 0.8)), 0.2), 5.0
        ),
    }


def update_note_format_data(
    *,
    config_path: Path,
    snapshots_path: Path,
    playbook_path: Path,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Collect public metrics politely and refresh the aggregate format playbook."""
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    config = _sanitize_config(_load_json(config_path, {}))
    history = _load_json(snapshots_path, {"version": 1, "articles": {}})
    articles = history.setdefault("articles", {})
    if not isinstance(articles, dict):
        articles = {}
        history["articles"] = articles
    owned_client = client is None
    active_client = client or httpx.Client(
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ja"},
    )

    try:
        robots_response = active_client.get(NOTE_ROBOTS_URL)
        robots_response.raise_for_status()
        robots = _robots_parser(robots_response.text)
        discovered: dict[str, str] = {}
        for source_url in config["sources"]:
            if not robots.can_fetch(DEFAULT_USER_AGENT, source_url):
                LOGGER.warning("robots.txtによりRSS取得を中止しました: %s", source_url)
                continue
            response = active_client.get(source_url)
            response.raise_for_status()
            for item in parse_rss_article_urls(
                response.text, limit=int(config["max_articles_per_run"])
            ):
                discovered[item["url"]] = item["title"]
            sleep(float(config["request_delay_seconds"]))

        cutoff = observed - timedelta(days=int(config["tracking_days"]))
        targets = dict(discovered)
        for url, article in articles.items():
            if url in targets or not isinstance(article, dict):
                continue
            try:
                first_seen = _parse_datetime(str(article.get("first_seen", "")))
            except ValueError:
                continue
            if first_seen >= cutoff:
                targets[url] = ""
        targets = dict(list(targets.items())[: int(config["max_articles_per_run"])])

        successful = 0
        for url, title in targets.items():
            if not robots.can_fetch(DEFAULT_USER_AGENT, url):
                continue
            try:
                response = active_client.get(url)
                response.raise_for_status()
                snapshot = extract_public_article_snapshot(
                    response.text,
                    url=url,
                    title=title,
                    observed_at=observed,
                )
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                LOGGER.warning("note記事の構造分析を見送りました (%s): %s", url, exc)
                continue
            article = articles.setdefault(
                url,
                {
                    "url_hash": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                    "first_seen": _isoformat(observed),
                    "observations": [],
                },
            )
            observations = article.setdefault("observations", [])
            if not isinstance(observations, list):
                observations = []
                article["observations"] = observations
            if not title and observations:
                previous = observations[-1]
                previous_features = (
                    previous.get("features", {}) if isinstance(previous, dict) else {}
                )
                previous_title_chars = (
                    previous_features.get("title_chars")
                    if isinstance(previous_features, dict)
                    else None
                )
                if isinstance(previous_title_chars, int) and previous_title_chars > 0:
                    snapshot["features"]["title_chars"] = previous_title_chars
            observations.append(snapshot)
            article["published_at"] = snapshot.get("published_at", "")
            article["observations"] = observations[-14:]
            successful += 1
            sleep(float(config["request_delay_seconds"]))

        retention_cutoff = observed - timedelta(days=45)
        for url, article in list(articles.items()):
            try:
                first_seen = _parse_datetime(str(article.get("first_seen", "")))
            except (AttributeError, ValueError):
                first_seen = datetime.min.replace(tzinfo=UTC)
            if first_seen < retention_cutoff:
                del articles[url]

        history["version"] = 1
        history["updated_at"] = _isoformat(observed)
        history["source"] = "note公式『今日の注目記事』RSS"
        history["collection_policy"] = "本文非保存・公開構造指標とスキ数のみ"
        history["last_run"] = {
            "discovered": len(discovered),
            "checked": len(targets),
            "successful": successful,
        }
        playbook = build_format_playbook(history, observed)
        _write_json(snapshots_path, history)
        _write_json(playbook_path, playbook)
        return playbook
    finally:
        if owned_client:
            active_client.close()


def _validated_playbook(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("version") != 1 or payload.get("status") != "active":
        return None
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, dict):
        return None
    limits = {
        "title_chars": (20, 50),
        "intro_chars": (120, 320),
        "paragraph_chars": (45, 150),
        "h2_count": (4, 10),
        "h3_count": (0, 12),
        "list_count": (0, 8),
        "separator_count": (0, 5),
    }
    clean: dict[str, int] = {}
    for key, (minimum, maximum) in limits.items():
        value = recommendations.get(key)
        if not isinstance(value, int | float):
            return None
        clean[key] = min(max(round(value), minimum), maximum)
    sample_size = payload.get("sample_size", 0)
    if not isinstance(sample_size, int) or sample_size < 5:
        return None
    return {
        "sample_size": min(sample_size, 100),
        "confidence": (
            payload.get("confidence")
            if payload.get("confidence") in {"medium", "high"}
            else "medium"
        ),
        "recommendations": clean,
    }


@lru_cache(maxsize=16)
def _load_remote_playbook(
    remote_url: str, time_bucket: int, timeout_seconds: float
) -> dict[str, Any]:
    del time_bucket
    parsed = urlparse(remote_url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        return {}
    try:
        response = httpx.get(
            remote_url,
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
        if len(response.content) > 100_000:
            return {}
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_note_format_guidance(
    remote_url: str = "",
    timeout_seconds: float = 3.0,
    *,
    now: datetime | None = None,
) -> str:
    """Return a bounded instruction string; raw remote strings never enter the LLM prompt."""
    local = _load_json(DEFAULT_PLAYBOOK_PATH, {})
    payload = local
    if remote_url.strip():
        timestamp = (now or datetime.now(UTC)).timestamp()
        bucket = int(timestamp // (6 * 3600))
        remote = _load_remote_playbook(remote_url.strip(), bucket, timeout_seconds)
        if remote:
            payload = remote
    validated = _validated_playbook(payload)
    if validated is None:
        return ""
    rec = validated["recommendations"]
    return (
        f"公開反応の伸びが確認できたnote記事{validated['sample_size']}件の構造集計"
        f"（信頼度: {validated['confidence']}）を構成上の参考にする。"
        f"タイトルは約{rec['title_chars']}字以内、導入は約{rec['intro_chars']}字、"
        f"1段落は約{rec['paragraph_chars']}字を目安にする。"
        f"H2は{rec['h2_count']}個前後、H3は{rec['h3_count']}個前後、"
        f"箇条書きブロックは{rec['list_count']}個前後、区切り線は"
        f"{rec['separator_count']}個前後を上限目安にする。"
        "ただし、指定済みの7部構成、事実性、安全ルール、文字数上限を常に優先する。"
        "参考記事の文言・固有表現・言い回しは模倣しない。"
    )
