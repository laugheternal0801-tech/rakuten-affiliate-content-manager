from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from app.config import get_settings
from app.database import session_scope
from app.repositories import get_setting, list_products, save_product
from app.schemas import ScoreWeights, SearchCriteria
from app.services.rakuten_api import RakutenAPIError
from app.services.scoring import calculate_score
from app.streamlit_support import PRODUCT_COLUMN_CONFIG, get_rakuten_client, product_dataframe

settings = get_settings()
if not settings.rakuten_configured:
    st.warning(
        "楽天API認証情報が未設定のため、架空のサンプルデータを表示します。公開には使用できません。",
        icon=":material/science:",
    )

sort_options = {
    "楽天標準": "standard",
    "価格が安い順": "+itemPrice",
    "価格が高い順": "-itemPrice",
    "レビュー件数が多い順": "-reviewCount",
    "平均評価が高い順": "-reviewAverage",
    "料率が高い順": "-affiliateRate",
    "更新が新しい順": "-updateTimestamp",
}

with st.form("product_search"):
    keyword = st.text_input("検索キーワード", placeholder="例: コーヒー 豆")
    genre_id = st.text_input("ジャンルID", placeholder="例: 100356")
    row1 = st.columns(3)
    min_price = row1[0].number_input("最低価格", min_value=0, step=100)
    max_price = row1[1].number_input("最高価格", min_value=0, step=100)
    min_review_count = row1[2].number_input("最低レビュー件数", min_value=0, step=10)
    row2 = st.columns(3)
    min_review_average = row2[0].number_input(
        "最低平均評価", min_value=0.0, max_value=5.0, step=0.1
    )
    min_affiliate_rate = row2[1].number_input(
        "最低アフィリエイト料率（%）", min_value=0.0, max_value=100.0, step=0.5
    )
    hits = row2[2].number_input("取得件数", min_value=1, max_value=30, value=20)
    excluded = st.text_input("除外キーワード", help="カンマ区切り")
    sort_label = st.selectbox("並び順", list(sort_options))
    flags = st.columns(3)
    free_shipping = flags[0].checkbox("送料無料のみ")
    available_only = flags[1].checkbox("在庫ありのみ", value=True)
    image_only = flags[2].checkbox("画像ありのみ", value=True)
    submitted = st.form_submit_button("商品を検索", icon=":material/search:", type="primary")

if submitted:
    try:
        criteria = SearchCriteria(
            keyword=keyword,
            genre_id=genre_id,
            min_price=int(min_price) or None,
            max_price=int(max_price) or None,
            min_review_count=int(min_review_count),
            min_review_average=float(min_review_average),
            min_affiliate_rate=float(min_affiliate_rate),
            free_shipping_only=free_shipping,
            available_only=available_only,
            image_only=image_only,
            excluded_keywords=[term.strip() for term in excluded.split(",") if term.strip()],
            sort=sort_options[sort_label],
            hits=int(hits),
        )
        with session_scope() as session:
            weights = ScoreWeights(**get_setting(session, "score_weights", {})).model_dump()
            if settings.rakuten_configured:
                result = get_rakuten_client().search(criteria)
                products = result["products"]
            else:
                products = [
                    {
                        column.name: getattr(product, column.name)
                        for column in product.__table__.columns
                    }
                    for product in list_products(session)
                    if product.is_sample
                    and (
                        not criteria.keyword
                        or criteria.keyword.lower() in product.item_name.lower()
                    )
                ]
            for product in products:
                score = calculate_score(
                    product,
                    keyword=criteria.keyword,
                    target_min_price=criteria.min_price,
                    target_max_price=criteria.max_price,
                    weights=weights,
                )
                product["score"] = score.total
                product["score_details"] = score.details
            st.session_state.search_results = products
    except (ValidationError, ValueError) as exc:
        st.error(f"検索条件を確認してください: {exc}")
    except RakutenAPIError as exc:
        st.error(str(exc), icon=":material/error:")

results = st.session_state.get("search_results", [])
if results:
    st.subheader(f"検索結果（{len(results)}件）")
    frame = product_dataframe(results)
    event = st.dataframe(
        frame,
        hide_index=True,
        column_config=PRODUCT_COLUMN_CONFIG,
        on_select="rerun",
        selection_mode="multi-row",
        key="search_result_table",
    )
    selected_rows = event.selection.rows
    with st.container(horizontal=True):
        if st.button(
            "選択した商品を一括保存",
            icon=":material/save:",
            type="primary",
            disabled=not selected_rows,
        ):
            with session_scope() as session:
                for index in selected_rows:
                    save_product(session, results[index])
            st.success(f"{len(selected_rows)}件の商品を保存しました。")
        st.caption("行を選択して保存します。評価点だけで商品を自動決定しません。")
else:
    st.caption("検索条件を入力して商品を検索してください。")
