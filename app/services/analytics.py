from __future__ import annotations

import io
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import pandas as pd

CANONICAL_FIELDS = [
    "日付",
    "クリック数",
    "注文件数",
    "売上金額",
    "成果報酬",
    "商品名",
    "ショップ名",
    "URL",
    "媒体",
    "ステータス",
]

ALIASES: dict[str, list[str]] = {
    "日付": ["日付", "年月日", "date", "発生日", "確定日"],
    "クリック数": ["クリック数", "clicks", "クリック"],
    "注文件数": ["注文件数", "件数", "orders", "注文数", "売上件数"],
    "売上金額": ["売上金額", "売上", "sales", "購入金額", "金額"],
    "成果報酬": ["成果報酬", "報酬", "reward", "成果報酬額"],
    "商品名": ["商品名", "item", "product", "商品"],
    "ショップ名": ["ショップ名", "店舗名", "shop"],
    "URL": ["URL", "url", "リンク先"],
    "媒体": ["媒体", "channel", "メディア"],
    "ステータス": ["ステータス", "status", "状態"],
}


class CSVImportError(ValueError):
    pass


def read_report_csv(data: bytes, filename: str, max_bytes: int = 10 * 1024 * 1024) -> pd.DataFrame:
    if not filename.lower().endswith(".csv"):
        raise CSVImportError("CSVファイルだけをアップロードできます。")
    if len(data) > max_bytes:
        raise CSVImportError("CSVファイルは10MB以下にしてください。")
    if not data:
        raise CSVImportError("CSVファイルが空です。")
    for encoding in ["utf-8-sig", "cp932", "utf-8"]:
        try:
            frame = pd.read_csv(io.BytesIO(data), encoding=encoding, dtype=str)
            frame.columns = [str(column).strip() for column in frame.columns]
            if frame.empty:
                raise CSVImportError("CSVにデータ行がありません。")
            return frame
        except UnicodeDecodeError:
            continue
        except pd.errors.ParserError as exc:
            raise CSVImportError("CSVの形式を読み取れませんでした。") from exc
    raise CSVImportError("文字コードを判定できません。UTF-8またはShift-JISで保存してください。")


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    normalized = {column: re.sub(r"[\s_\-]", "", column).lower() for column in columns}
    for field, aliases in ALIASES.items():
        for column, normalized_column in normalized.items():
            if any(re.sub(r"[\s_\-]", "", alias).lower() in normalized_column for alias in aliases):
                mapping[field] = column
                break
    return mapping


def apply_mapping(frame: pd.DataFrame, mapping: Mapping[str, str]) -> pd.DataFrame:
    required = {"日付", "クリック数", "注文件数", "売上金額", "成果報酬"}
    missing = [field for field in required if not mapping.get(field)]
    if missing:
        raise CSVImportError(f"必須項目の列マッピングがありません: {', '.join(missing)}")
    invalid = [column for column in mapping.values() if column and column not in frame.columns]
    if invalid:
        raise CSVImportError("存在しないCSV列がマッピングされています。")
    mapped = pd.DataFrame(index=frame.index)
    for field in CANONICAL_FIELDS:
        column = mapping.get(field, "")
        mapped[field] = frame[column] if column else ""
    mapped["日付"] = pd.to_datetime(mapped["日付"], errors="coerce").dt.date
    if mapped["日付"].isna().any():
        raise CSVImportError("日付として読み取れない行があります。")
    for field in ["クリック数", "注文件数", "売上金額", "成果報酬"]:
        cleaned = mapped[field].astype(str).str.replace(r"[^0-9.\-]", "", regex=True)
        mapped[field] = pd.to_numeric(cleaned, errors="coerce").fillna(0)
    mapped["クリック数"] = mapped["クリック数"].astype(int).clip(lower=0)
    mapped["注文件数"] = mapped["注文件数"].astype(int).clip(lower=0)
    mapped["売上金額"] = mapped["売上金額"].astype(float).clip(lower=0)
    mapped["成果報酬"] = mapped["成果報酬"].astype(float).clip(lower=0)
    return mapped


def rows_for_database(frame: pd.DataFrame, source_file: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    safe_source = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠._-]", "_", source_file)[:255]
    for row in frame.to_dict(orient="records"):
        row_date = row["日付"]
        if not isinstance(row_date, date):
            raise CSVImportError("日付の変換に失敗しました。")
        rows.append(
            {
                "date": row_date,
                "channel": str(row["媒体"])[:100],
                "clicks": int(row["クリック数"]),
                "orders": int(row["注文件数"]),
                "sales": float(row["売上金額"]),
                "reward": float(row["成果報酬"]),
                "product_name": str(row["商品名"])[:1000],
                "shop_name": str(row["ショップ名"])[:500],
                "url": str(row["URL"])[:5000],
                "status": str(row["ステータス"])[:100],
                "source_file": safe_source,
            }
        )
    return rows


def performance_summary(frame: pd.DataFrame) -> dict[str, float]:
    clicks = float(frame["クリック数"].sum()) if not frame.empty else 0
    orders = float(frame["注文件数"].sum()) if not frame.empty else 0
    sales = float(frame["売上金額"].sum()) if not frame.empty else 0
    reward = float(frame["成果報酬"].sum()) if not frame.empty else 0
    return {
        "clicks": clicks,
        "orders": orders,
        "sales": sales,
        "reward": reward,
        "purchase_conversion_rate": orders / clicks if clicks else 0,
        "reward_per_order": reward / orders if orders else 0,
    }
