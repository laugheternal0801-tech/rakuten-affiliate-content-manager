from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from app.config import get_settings
from app.database import session_scope
from app.repositories import delete_product, get_setting, list_products, save_product
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
    search_options = st.columns(2)
    sort_label = search_options[0].selectbox("並び順", list(sort_options))
    hits = search_options[1].number_input("取得件数", min_value=1, max_value=30, value=20)
    flags = st.columns(3)
    free_shipping = flags[0].checkbox("送料無料のみ")
    available_only = flags[1].checkbox("在庫ありのみ", value=True)
    image_only = flags[2].checkbox("画像ありのみ", value=True)
    submitted = st.form_submit_button("商品を検索", icon=":material/search:", type="primary")

if submitted:
    try:
        criteria = SearchCriteria(
            keyword=keyword,
            free_shipping_only=free_shipping,
            available_only=available_only,
            image_only=image_only,
            sort=sort_options[sort_label],
            hits=int(hits),
        )
        with session_scope() as session:
            weights = ScoreWeights(**get_setting(session, "score_weights", {})).model_dump()
            if settings.rakuten_configured:
                result = get_rakuten_client().search(criteria)
                products = result["products"]
                if result.get("affiliate_id_rejected"):
                    st.warning(
                        "Affiliate IDが楽天APIに拒否されたため、商品情報のみ取得しました。"
                        "アフィリエイトURLを生成するにはAffiliate IDを確認してください。",
                        icon=":material/link_off:",
                    )
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
    selected_rows = event.selection.rows  # type: ignore[attr-defined]
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

with session_scope() as session:
    saved_products = list_products(session)

with st.expander(f"保存済み商品を整理（{len(saved_products)}件）", icon=":material/inventory_2:"):
    if not saved_products:
        st.caption("保存済みの商品はありません。検索結果から商品を選んで保存できます。")
    else:
        saved_product_id = st.selectbox(
            "削除する商品",
            [product.id for product in saved_products],
            format_func=lambda product_id: next(
                product.item_name for product in saved_products if product.id == product_id
            ),
        )
        saved_product = next(
            product for product in saved_products if product.id == saved_product_id
        )
        confirm_delete = st.checkbox(
            f"「{saved_product.item_name}」を削除する",
            key=f"confirm_saved_product_delete_{saved_product.id}",
        )
        if st.button(
            "選択した商品を削除",
            icon=":material/delete:",
            disabled=not confirm_delete,
        ):
            with session_scope() as session:
                deleted = delete_product(session, saved_product.id)
            if deleted:
                st.session_state.search_results = [
                    product
                    for product in st.session_state.get("search_results", [])
                    if product.get("item_code") != saved_product.item_code
                ]
                st.success("保存済み商品から削除しました。")
                st.rerun()
            else:
                st.warning("商品はすでに削除されています。画面を更新します。")
                st.rerun()
