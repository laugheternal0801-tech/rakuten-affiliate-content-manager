from __future__ import annotations

from functools import partial
from typing import Any

import pandas as pd
import streamlit as st

from app.config import get_settings
from app.models import Product
from app.services.rakuten_api import RakutenAPIClient


@st.cache_resource
def get_rakuten_client() -> RakutenAPIClient:
    return RakutenAPIClient(get_settings())


def product_dataframe(products: list[Product] | list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for product in products:
        read = product.get if isinstance(product, dict) else partial(getattr, product)
        rows.append(
            {
                "id": read("id", 0),
                "画像": read("image_url", ""),
                "商品名": read("item_name", ""),
                "価格": read("item_price", 0),
                "ショップ": read("shop_name", ""),
                "送料無料": read("postage_flag", 1) == 0,
                "在庫": read("availability", 0) == 1,
                "料率": read("affiliate_rate", 0),
                "レビュー件数": read("review_count", 0),
                "平均評価": read("review_average", 0),
                "ポイント": read("point_rate", 1),
                "セール開始": read("sale_start"),
                "セール終了": read("sale_end"),
                "商品ページ": read("item_url", ""),
                "アフィリエイトURL": read("affiliate_url", ""),
                "評価点": read("score", 0),
                "item_code": read("item_code", ""),
            }
        )
    return pd.DataFrame(rows)


PRODUCT_COLUMN_CONFIG: dict[str, Any] = {
    "id": None,
    "item_code": None,
    "画像": st.column_config.ImageColumn("画像", width="small"),
    "商品名": st.column_config.TextColumn("商品名", pinned=True, width="large"),
    "価格": st.column_config.NumberColumn("価格", format="¥%d"),
    "料率": st.column_config.NumberColumn("料率", format="%.1f%%"),
    "平均評価": st.column_config.NumberColumn("平均評価", format="%.1f"),
    "ポイント": st.column_config.NumberColumn("ポイント", format="%.1f倍"),
    "評価点": st.column_config.ProgressColumn("評価点", min_value=0, max_value=100, format="%.1f"),
    "商品ページ": st.column_config.LinkColumn("商品ページ", display_text="楽天市場で確認"),
    "アフィリエイトURL": st.column_config.LinkColumn("アフィリエイトURL", display_text="リンク"),
    "セール開始": st.column_config.DatetimeColumn("セール開始", format="YYYY/MM/DD HH:mm"),
    "セール終了": st.column_config.DatetimeColumn("セール終了", format="YYYY/MM/DD HH:mm"),
}


def show_compliance_report(report: dict[str, Any]) -> None:
    status = report.get("status", "要確認")
    if status == "OK":
        st.success("チェック結果: OK", icon=":material/check_circle:")
    elif status == "投稿不可":
        st.error("チェック結果: 投稿不可", icon=":material/block:")
    else:
        st.warning("チェック結果: 要確認", icon=":material/warning:")
    for issue in report.get("issues", []):
        label = "投稿不可" if issue.get("level") == "blocking" else "要確認"
        with st.expander(f"{label}: {issue.get('message', '')}", icon=":material/report:"):
            st.write(issue.get("suggestion", ""))
    st.caption(report.get("disclaimer", "最終確認は投稿者本人が行ってください。"))
