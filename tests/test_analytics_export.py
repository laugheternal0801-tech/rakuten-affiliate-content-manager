from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from app.models import Content, Product
from app.services.analytics import (
    apply_mapping,
    read_report_csv,
    rows_for_database,
    suggest_mapping,
)
from app.services.exporter import csv_bytes, export_content_bundle


def test_csv_import_and_column_mapping() -> None:
    data = "年月日,クリック,注文数,売上,報酬\n2026-08-01,10,2,3000,300\n".encode("utf-8-sig")
    frame = read_report_csv(data, "report.csv")
    suggested = suggest_mapping(list(frame.columns))
    mapped = apply_mapping(frame, suggested)
    rows = rows_for_database(mapped, "report.csv")
    assert rows[0]["date"] == date(2026, 8, 1)
    assert rows[0]["clicks"] == 10
    assert rows[0]["reward"] == 300


def test_csv_export_has_bom_and_formula_injection_protection() -> None:
    data = csv_bytes([{"title": '=HYPERLINK("bad")', "value": 1}], bom=True)
    assert data.startswith(b"\xef\xbb\xbf")
    assert "'=HYPERLINK" in data.decode("utf-8-sig")


def test_markdown_and_json_export(tmp_path: Path) -> None:
    product = Product(
        id=1,
        item_code="shop:1",
        item_name="テスト商品",
        item_price=1000,
        item_url="https://item.rakuten.co.jp/shop/1/",
        affiliate_url="https://hb.afl.rakuten.co.jp/example",
        fetched_at=datetime.now().astimezone(),
        score=80,
    )
    content = Content(
        id=1,
        channel="note",
        theme="比較",
        title="テスト",
        draft_body="# 下書き",
        approved_body="# 確認済み",
        status="approved",
        compliance_report={"status": "OK"},
    )
    target = export_content_bundle(
        contents=[content],
        products=[product],
        export_root=tmp_path,
        theme="../比較",
        today=date(2026, 8, 6),
    )
    assert target.is_relative_to(tmp_path)
    assert (target / "note.md").read_text(encoding="utf-8") == "# 確認済み"
    metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["automatic_posting"] is False
    assert (target / "products.csv").exists()
