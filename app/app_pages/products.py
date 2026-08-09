from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.database import session_scope
from app.repositories import (
    delete_product,
    get_product,
    get_setting,
    list_products,
    set_setting,
    upsert_experience,
)
from app.schemas import ExperienceInput

with session_scope() as session:
    products = list_products(session)
    comparison_brief = get_setting(
        session,
        "comparison_article_brief",
        {
            "genre": "",
            "product_ids": [],
            "target_audience": "",
            "main_keyword": "",
        },
    )

if not products:
    st.info("保存商品がありません。商品検索画面で候補を保存してください。")
    st.stop()

eligible_products = [product for product in products if not product.is_sample]
eligible_ids = {product.id for product in eligible_products}
brief_product_ids = [
    int(product_id)
    for product_id in comparison_brief.get("product_ids", [])
    if int(product_id) in eligible_ids
]

with st.container(border=True):
    st.subheader("比較記事の準備")
    st.caption(
        "ジャンル、比較する5〜7商品、読者の悩み、狙うキーワードを自分で設定します。"
        "保存した内容は「投稿文作成」の比較記事モードに引き継がれます。"
    )
    if len(eligible_products) < 5:
        st.info(
            f"比較記事には実在の商品が5件以上必要です。現在は{len(eligible_products)}件です。"
            "先に商品検索から候補を保存してください。",
            icon=":material/info:",
        )
    with st.form("comparison_article_brief_form"):
        article_genre = st.text_input(
            "ジャンル",
            value=str(comparison_brief.get("genre", "")),
            placeholder="例：家庭用コーヒーメーカー",
        )
        comparison_ids = st.multiselect(
            "比較する商品（5〜7点）",
            [product.id for product in eligible_products],
            default=brief_product_ids,
            max_selections=7,
            format_func=lambda product_id: next(
                f"{product.item_name}｜{product.item_price:,}円"
                for product in eligible_products
                if product.id == product_id
            ),
            help="楽天の商品検索で保存した候補から、自分で5〜7点を選びます。",
        )
        target_audience = st.text_area(
            "想定読者（誰が何に困っているか）",
            value=str(comparison_brief.get("target_audience", "")),
            placeholder="例：忙しい朝でも手軽に使える1台を選べずに困っている人",
            height=90,
        )
        main_keyword = st.text_input(
            "狙うキーワード",
            value=str(comparison_brief.get("main_keyword", "")),
            placeholder="例：コーヒーメーカー おすすめ 比較",
        )
        save_brief = st.form_submit_button(
            "比較記事の設定を保存",
            icon=":material/save:",
            type="primary",
        )
    if save_brief:
        missing = []
        if not article_genre.strip():
            missing.append("ジャンル")
        if not 5 <= len(comparison_ids) <= 7:
            missing.append("比較する商品5〜7点")
        if not target_audience.strip():
            missing.append("想定読者")
        if not main_keyword.strip():
            missing.append("狙うキーワード")
        if missing:
            st.error("入力を確認してください：" + "、".join(missing))
        else:
            with session_scope() as session:
                set_setting(
                    session,
                    "comparison_article_brief",
                    {
                        "genre": article_genre.strip(),
                        "product_ids": list(comparison_ids),
                        "target_audience": target_audience.strip(),
                        "main_keyword": main_keyword.strip(),
                    },
                )
            st.success("比較記事の設定を保存しました。投稿文作成から使えます。")
            st.rerun()

if brief_product_ids:
    selected_for_article = [
        product for product in eligible_products if product.id in brief_product_ids
    ]
    verified_count = sum(
        1
        for product in selected_for_article
        if product.experience
        and product.experience.has_used is True
        and product.experience.verified_at is not None
    )
    status_columns = st.columns(2)
    status_columns[0].metric("比較記事の商品", f"{len(brief_product_ids)} / 5〜7点", border=True)
    status_columns[1].metric(
        "実使用確認済み",
        f"{verified_count} / {len(brief_product_ids)}点",
        border=True,
        help="実際に使用した商品として記録し、情報確認日がある商品の数です。",
    )
    if verified_count < len(brief_product_ids):
        st.warning(
            "実使用が未確認の商品は、Claudeが『使った』とは書きません。"
            "レビューから分かったことと自分の意見は、下の短いフォームから記事へ反映できます。",
            icon=":material/fact_check:",
        )

st.divider()
st.subheader("商品ごとの情報・体験")

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

    st.subheader("レビュー参考メモ・自分の意見")
    st.caption(
        "レビューコメントは長く転載せず、よく見かけた傾向を自分の言葉で要約してください。"
        "自分の意見は記事の判断材料に使い、未使用の商品を『使った』とは書きません。"
    )
    with st.form("experience_form"):
        source_options = {
            "レビューを参照して判断": False,
            "実際に使用した": True,
        }
        source_default = (
            "実際に使用した"
            if experience and experience.has_used is True
            else "レビューを参照して判断"
        )
        with st.container(horizontal=True):
            source_type = st.segmented_control(
                "情報のもと",
                list(source_options),
                default=source_default,
                help="実際に使った商品だけ「実際に使用した」を選んでください。",
            )
            verified = st.date_input(
                "確認日",
                value=(
                    experience.verified_at
                    if experience and experience.verified_at
                    else date.today()
                ),
            )
        review_notes = st.text_area(
            "レビューから分かったこと",
            value=experience.compared_products if experience else "",
            placeholder="例：『手入れが簡単』という声が多い一方、大きめという意見も見かけた。",
            help="個別コメントの丸写しではなく、傾向や比較材料を短くまとめます。",
            height=110,
        )
        own_opinion = st.text_area(
            "自分の意見・記事に入れたい視点",
            value=experience.memo if experience else "",
            placeholder="例：毎日使うなら価格差よりも洗いやすさを優先したい。",
            height=110,
        )
        saved = st.form_submit_button(
            "レビュー・意見を保存",
            icon=":material/save:",
            type="primary",
        )
    if saved:
        payload = ExperienceInput(
            owns_product=experience.owns_product if experience else None,
            has_used=source_options[str(source_type or source_default)],
            usage_period=experience.usage_period if experience else "",
            usage_scene=experience.usage_scene if experience else "",
            positive_points=experience.positive_points if experience else "",
            negative_points=experience.negative_points if experience else "",
            suitable_for=experience.suitable_for if experience else "",
            unsuitable_for=experience.unsuitable_for if experience else "",
            compared_products=review_notes,
            memo=own_opinion,
            verified_at=verified,
        )
        upsert_experience(session, product.id, payload)
        st.success("レビューの要約と自分の意見を保存しました。")
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
