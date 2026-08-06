from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.models import Content, Product

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_slug(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠_-]+", "-", value.strip()).strip("-_")
    return normalized[:80] or "untitled"


def sanitize_csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_bytes(rows: Iterable[dict[str, Any]], *, bom: bool = True) -> bytes:
    items = list(rows)
    if not items:
        return b"\xef\xbb\xbf" if bom else b""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(items[0].keys()), extrasaction="ignore")
    writer.writeheader()
    for row in items:
        writer.writerow({key: sanitize_csv_cell(value) for key, value in row.items()})
    encoding = "utf-8-sig" if bom else "utf-8"
    return buffer.getvalue().encode(encoding)


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _product_rows(products: list[Product]) -> list[dict[str, Any]]:
    return [
        {
            "item_code": product.item_code,
            "item_name": product.item_name,
            "item_price": product.item_price,
            "shop_name": product.shop_name,
            "affiliate_rate": product.affiliate_rate,
            "review_count": product.review_count,
            "review_average": product.review_average,
            "item_url": product.item_url,
            "affiliate_url": product.affiliate_url,
            "score": product.score,
            "fetched_at": product.fetched_at.isoformat(),
        }
        for product in products
    ]


def export_content_bundle(
    *,
    contents: list[Content],
    products: list[Product],
    export_root: Path,
    theme: str,
    bom: bool = True,
    today: date | None = None,
) -> Path:
    day = today or date.today()
    root = export_root.resolve()
    target = (root / day.isoformat() / safe_slug(theme)).resolve()
    if not _within_root(target, root):
        raise ValueError("出力先がexportsフォルダ外です。")
    target.mkdir(parents=True, exist_ok=True)

    (target / "products.csv").write_bytes(csv_bytes(_product_rows(products), bom=bom))
    comparison = ["# 商品比較", "", "| 商品 | 価格 | 評価点 |", "|---|---:|---:|"]
    comparison.extend(
        f"| {product.item_name} | {product.item_price:,}円 | {product.score:.1f} |"
        for product in products
    )
    (target / "comparison.md").write_text("\n".join(comparison) + "\n", encoding="utf-8")

    by_channel: dict[str, list[Content]] = {}
    for content in contents:
        by_channel.setdefault(content.channel, []).append(content)

    note_text = "\n\n".join(
        content.approved_body or content.draft_body for content in by_channel.get("note", [])
    )
    (target / "note.md").write_text(note_text, encoding="utf-8")
    x_rows = [
        {
            "title": content.title,
            "body": content.approved_body or content.draft_body,
            "status": content.status,
        }
        for content in by_channel.get("X", [])
    ]
    (target / "x.csv").write_bytes(csv_bytes(x_rows, bom=bom))
    pinterest_rows = [
        {
            "title": content.title,
            "body": content.approved_body or content.draft_body,
            "status": content.status,
        }
        for content in by_channel.get("Pinterest", [])
    ]
    (target / "pinterest.csv").write_bytes(csv_bytes(pinterest_rows, bom=bom))
    instagram = "\n\n".join(
        content.approved_body or content.draft_body for content in by_channel.get("Instagram", [])
    )
    (target / "instagram.md").write_text(instagram, encoding="utf-8")
    room_rows = [
        {
            "title": content.title,
            "body": content.approved_body or content.draft_body,
            "status": content.status,
        }
        for content in by_channel.get("楽天ROOM", [])
    ]
    (target / "room.csv").write_bytes(csv_bytes(room_rows, bom=bom))

    reports = [content.compliance_report for content in contents]
    (target / "compliance_report.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    metadata = {
        "theme": theme,
        "exported_at": datetime.now().astimezone().isoformat(),
        "content_ids": [content.id for content in contents],
        "product_ids": [product.id for product in products],
        "human_review_required": True,
        "automatic_posting": False,
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target
