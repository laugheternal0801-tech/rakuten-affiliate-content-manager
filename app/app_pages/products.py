from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.database import session_scope
from app.repositories import delete_product, get_product, list_products, upsert_experience
from app.schemas import ExperienceInput

with session_scope() as session:
    products = list_products(session)

if not products:
    st.info("保存商品がありません。商品検索画面で候補を保存してください。")
    st.stop()

selected_id = st.selectbox(
    "商品",
    [product.id for product in products],
    format_func=lambda product_id: next(p.item_name for p in products if p.id == product_id),
)

delete_requested = False
selected_product_name = ""
with session_scope() as session:
    product = get_product(session, int(selected_id))
    assert product is not None
    experience = product.experience
    if product.is_sample:
        st.error(
            "この商品は架空のサンプルデータです。公開用コンテンツには使用できません。",
            icon=":material/science:",
        )
    left, right = st.columns([1, 2])
    with left.container(border=True):
        if product.image_url:
            st.image(product.image_url, width="stretch")
        else:
            st.caption("商品画像URLはありません。画像のダウンロード・加工は行いません。")
        st.link_button("楽天市場の商品ページ", product.item_url, icon=":material/open_in_new:")
        if product.affiliate_url:
            st.link_button("アフィリエイトURLを確認", product.affiliate_url)
    with right.container(border=True):
        st.subheader(product.item_name)
        st.write(product.catchcopy)
        with st.container(horizontal=True):
            st.metric("価格", f"¥{product.item_price:,}")
            st.metric("レビュー", f"{product.review_average:.1f} / {product.review_count:,}件")
            st.metric("料率", f"{product.affiliate_rate:.1f}%")
            st.metric("自動評価", f"{product.score:.1f}/100")
        details = [
            {
                "項目": value.get("label", key),
                "得点": value.get("points", 0),
                "配点": value.get("max_points", 0),
            }
            for key, value in (product.score_details or {}).items()
        ]
        if details:
            st.dataframe(pd.DataFrame(details), hide_index=True)
        st.caption("評価点は候補整理の補助であり、商品の自動決定には使いません。")

    st.subheader("所有・使用経験を記録")
    st.caption("未使用の商品について使用感を推測で補完しません。")
    with st.form("experience_form"):
        owns_options = {"未入力": None, "所有している": True, "所有していない": False}
        used_options = {"未入力": None, "実際に使用した": True, "使用していない": False}
        owns_default = next(
            (k for k, v in owns_options.items() if experience and v == experience.owns_product),
            "未入力",
        )
        used_default = next(
            (k for k, v in used_options.items() if experience and v == experience.has_used),
            "未入力",
        )
        first = st.columns(3)
        owns = first[0].selectbox(
            "商品を所有しているか", list(owns_options), index=list(owns_options).index(owns_default)
        )
        used = first[1].selectbox(
            "実際に使用したか", list(used_options), index=list(used_options).index(used_default)
        )
        verified = first[2].date_input(
            "情報確認日",
            value=experience.verified_at if experience and experience.verified_at else date.today(),
        )
        usage_period = st.text_input(
            "使用期間", value=experience.usage_period if experience else ""
        )
        usage_scene = st.text_area(
            "使用した場面", value=experience.usage_scene if experience else ""
        )
        positive = st.text_area(
            "よかった点", value=experience.positive_points if experience else ""
        )
        negative = st.text_area(
            "気になった点", value=experience.negative_points if experience else ""
        )
        second = st.columns(2)
        suitable = second[0].text_area(
            "向いている人", value=experience.suitable_for if experience else ""
        )
        unsuitable = second[1].text_area(
            "向いていない人", value=experience.unsuitable_for if experience else ""
        )
        compared = st.text_area(
            "比較した商品・比較根拠", value=experience.compared_products if experience else ""
        )
        memo = st.text_area("自由メモ", value=experience.memo if experience else "")
        saved = st.form_submit_button("体験情報を保存", icon=":material/save:", type="primary")
    if saved:
        payload = ExperienceInput(
            owns_product=owns_options[owns],
            has_used=used_options[used],
            usage_period=usage_period,
            usage_scene=usage_scene,
            positive_points=positive,
            negative_points=negative,
            suitable_for=suitable,
            unsuitable_for=unsuitable,
            compared_products=compared,
            memo=memo,
            verified_at=verified,
        )
        upsert_experience(session, product.id, payload)
        st.success("体験情報を保存しました。")
        st.rerun()

    st.divider()
    with st.expander("保存商品を削除", icon=":material/delete:"):
        st.warning(
            "削除すると、この商品の体験情報も削除されます。過去の投稿本文と成果履歴は残ります。",
            icon=":material/warning:",
        )
        confirm_delete = st.checkbox(
            f"「{product.item_name}」を削除することを確認しました",
            key=f"confirm_product_delete_{product.id}",
        )
        delete_requested = st.button(
            "この保存商品を削除",
            icon=":material/delete_forever:",
            disabled=not confirm_delete,
            key=f"delete_product_{product.id}",
        )
        selected_product_name = product.item_name

if delete_requested:
    with session_scope() as session:
        deleted = delete_product(session, int(selected_id))
    if deleted:
        st.success(f"「{selected_product_name}」を保存商品から削除しました。")
        st.rerun()
    else:
        st.warning("商品はすでに削除されています。画面を更新します。")
        st.rerun()
