from __future__ import annotations

from datetime import date

import streamlit as st

from app.config import get_settings
from app.database import session_scope
from app.repositories import create_content, get_setting, list_products
from app.services.compliance import check_content
from app.services.content_generation import CHANNELS, GenerationContext, get_content_generator
from app.streamlit_support import show_compliance_report

with session_scope() as session:
    products = list_products(session)
    disclosure = get_setting(
        session,
        "affiliate_disclosure",
        "この記事にはアフィリエイト広告が含まれています。",
    )
    pr_policy = get_setting(session, "pr_policy", "always")

if not products:
    st.info("保存商品がありません。先に商品検索画面で保存してください。")
    st.stop()

settings = get_settings()
with st.form("generation_form"):
    selected_ids = st.multiselect(
        "対象商品",
        [product.id for product in products],
        format_func=lambda product_id: next(p.item_name for p in products if p.id == product_id),
    )
    theme = st.text_input("テーマ", placeholder="例: 自宅で楽しむコーヒー選び")
    channel = st.segmented_control("媒体", CHANNELS, default="note")
    mode = st.segmented_control(
        "文章生成方式",
        ["テンプレート", "LLM拡張"],
        default="テンプレート",
        help="LLM APIキーがない場合は自動的にテンプレートへ切り替わります。",
    )
    link_mode_label = st.segmented_control(
        "リンクの入れ方",
        ["プレースホルダー", "実際のURL"],
        default="プレースホルダー",
    )
    pr_required = st.checkbox("PR必須案件", value=False)
    generated = st.form_submit_button(
        "下書きを生成", icon=":material/auto_awesome:", type="primary"
    )

if generated:
    if not selected_ids or not theme.strip() or not channel:
        st.error("対象商品、テーマ、媒体を入力してください。")
    else:
        selected_products = [product for product in products if product.id in selected_ids]
        if any(product.is_sample for product in selected_products):
            st.warning("架空のサンプル商品を含む下書きは、操作確認専用で公開できません。")
        effective_pr = pr_required or pr_policy == "always"
        generator = get_content_generator(
            "llm" if mode == "LLM拡張" else "template",
            provider=settings.llm_provider,
            api_key=settings.llm_api_key,
        )
        output = generator.generate(
            str(channel),
            GenerationContext(
                products=selected_products,
                theme=theme.strip(),
                link_mode="direct" if link_mode_label == "実際のURL" else "placeholder",
                disclosure=disclosure,
                pr_required=effective_pr,
            ),
        )
        st.session_state.generated_content = {
            **output.model_dump(),
            "product_ids": selected_ids,
            "theme": theme.strip(),
            "pr_required": effective_pr,
        }
        if mode == "LLM拡張" and not settings.llm_configured:
            st.info("LLM設定がないため、安全にテンプレート生成へ切り替えました。")

draft = st.session_state.get("generated_content")
if draft:
    st.subheader("生成した下書き")
    title = st.text_input("タイトル", value=draft["title"], key="generated_title")
    body = st.text_area("本文", value=draft["body"], height=600, key="generated_body")
    selected_products = [product for product in products if product.id in draft["product_ids"]]
    verified_dates = [
        product.experience.verified_at
        for product in selected_products
        if product.experience and product.experience.verified_at
    ]
    info_verified_at = min(verified_dates) if verified_dates else None
    report = check_content(
        body,
        selected_products,
        affiliate_disclosure_required=True,
        info_verified_at=info_verified_at,
        comparison_basis_saved=all(
            bool(product.experience and product.experience.compared_products)
            for product in selected_products
        ),
    )
    show_compliance_report(report.to_dict())
    st.caption("情報が不足する箇所は『要確認』『体験情報を入力してください』のまま残しています。")
    confirm = st.checkbox(
        "内容と取得元を確認しました（下書き保存であり、公開は行われません）",
        key="confirm_draft_save",
    )
    if st.button(
        "確認待ちとして保存",
        icon=":material/save:",
        type="primary",
        disabled=not confirm,
    ):
        affiliate_url = next(
            (product.affiliate_url for product in selected_products if product.affiliate_url), ""
        )
        with session_scope() as session:
            content = create_content(
                session,
                channel=draft["channel"],
                theme=draft["theme"],
                title=title,
                body=body,
                product_ids=draft["product_ids"],
                affiliate_url=affiliate_url,
                pr_required=draft["pr_required"],
                compliance_status=report.status,
                compliance_report=report.to_dict(),
                info_verified_at=info_verified_at if isinstance(info_verified_at, date) else None,
            )
        st.success(f"投稿ID {content.id} として確認待ちに保存しました。自動投稿は行いません。")
        st.session_state.generated_content = None
        st.rerun()
