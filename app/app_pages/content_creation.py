from __future__ import annotations

import re
from datetime import date
from uuid import uuid4

import streamlit as st

from app.config import get_settings
from app.database import session_scope
from app.repositories import create_content, get_setting, list_products
from app.services.compliance import check_content
from app.services.content_generation import (
    APPEAL_POINT_OPTIONS,
    CHANNEL_PROFILES,
    CHANNELS,
    TONE_OPTIONS,
    ContentGenerationError,
    GenerationContext,
    analyze_copy,
    get_content_generator,
)
from app.streamlit_support import show_compliance_report

st.caption("商品情報と確認済みの体験情報をもとに、媒体に合う日本語の投稿案を最大3案作成します。")

with session_scope() as session:
    products = list_products(session)
    disclosure = get_setting(
        session,
        "affiliate_disclosure",
        "この記事にはアフィリエイト広告が含まれています。",
    )
    pr_policy = get_setting(session, "pr_policy", "always")
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
    st.info("保存商品がありません。先に商品検索画面で商品を保存してください。")
    st.stop()

settings = get_settings()
st.session_state.setdefault("generated_variations", [])

if settings.llm_configured:
    st.badge(
        f"Claude Sonnet 5 利用可能｜{settings.anthropic_model}",
        icon=":material/auto_awesome:",
        color="green",
    )
else:
    st.caption("LLM拡張を使うには、設定画面でClaude APIキーの設定方法を確認してください。")

channel = st.segmented_control(
    "投稿先",
    CHANNELS,
    default="note",
    key="creation_channel",
)
selected_channel = str(channel or "note")
comparison_mode = selected_channel == "note"

if comparison_mode:
    article_format = "comparison_review"
    article_genre = str(comparison_brief.get("genre", ""))
    main_keyword = str(comparison_brief.get("main_keyword", ""))
    eligible_products = [product for product in products if not product.is_sample]
    eligible_ids = {product.id for product in eligible_products}
    default_comparison_ids = [
        int(product_id)
        for product_id in comparison_brief.get("product_ids", [])
        if int(product_id) in eligible_ids
    ]
    st.info(
        "note投稿では、商品・体験情報で保存した設定と専用プロンプトを使い、"
        "Claudeが7部構成・約3,000字の比較記事を作ります。",
        icon=":material/article:",
    )
    with st.container(border=True):
        st.subheader("note比較記事の設計")
        with st.form("comparison_generation_form"):
            article_genre = st.text_input(
                "ジャンル",
                value=article_genre,
                placeholder="例：家庭用コーヒーメーカー",
            )
            selected_ids = st.multiselect(
                "比較する商品（5〜7点）",
                [product.id for product in eligible_products],
                default=default_comparison_ids,
                max_selections=7,
                format_func=lambda product_id: next(
                    f"{product.item_name}｜{product.item_price:,}円"
                    for product in eligible_products
                    if product.id == product_id
                ),
            )
            target_audience = st.text_area(
                "想定読者（誰が何に困っているか）",
                value=str(comparison_brief.get("target_audience", "")),
                placeholder="例：忙しい朝でも手軽に使える1台を選べずに困っている人",
                height=90,
            )
            main_keyword = st.text_input(
                "狙うキーワード",
                value=main_keyword,
                placeholder="例：コーヒーメーカー おすすめ 比較",
            )
            custom_message = st.text_area(
                "追加で入れたい条件（任意）",
                placeholder="例：お手入れ時間も比較したい",
                help="公開してよい、事実確認済みの内容だけを入力してください。",
                height=80,
            )
            detail_columns = st.columns(2)
            link_mode_label = detail_columns[0].selectbox(
                "リンクの入れ方",
                ["実際のURL", "プレースホルダー"],
            )
            pr_required = detail_columns[1].checkbox("PR必須案件", value=False)
            generated = st.form_submit_button(
                "note記事を作成",
                icon=":material/auto_awesome:",
                type="primary",
                disabled=not settings.llm_configured,
            )
    theme = article_genre
    tone = "信頼感のある丁寧語"
    appeal_points = tuple(APPEAL_POINT_OPTIONS)
    target_length = 3_000
    hashtag_count = 0
    variation_count = 1
    mode = "LLM拡張"
else:
    article_format = "standard"
    article_genre = ""
    main_keyword = ""
    profile = CHANNEL_PROFILES[selected_channel]
    st.info(str(profile["description"]), icon=":material/lightbulb:")

    with st.container(border=True):
        st.subheader("投稿の設計")
        with st.form("generation_form"):
            selected_ids = st.multiselect(
                "紹介する商品",
                [product.id for product in products],
                format_func=lambda product_id: next(
                    f"{product.item_name}｜{product.item_price:,}円"
                    for product in products
                    if product.id == product_id
                ),
                help="比較記事では複数商品、短いSNS投稿では1商品がおすすめです。",
            )
            theme = st.text_input(
                "投稿テーマ",
                placeholder="例：自宅で楽しむコーヒー選び",
            )
            target_audience = st.text_input(
                "想定する読者",
                value="商品選びで迷っている人",
                placeholder="例：忙しい朝でも手軽にコーヒーを楽しみたい人",
            )
            tone = str(
                st.segmented_control(
                    "文章の雰囲気",
                    TONE_OPTIONS,
                    default="信頼感のある丁寧語",
                )
                or "信頼感のある丁寧語"
            )
            appeal_points = tuple(
                str(point)
                for point in (
                    st.pills(
                        "強調するポイント",
                        APPEAL_POINT_OPTIONS,
                        default=["価格", "レビュー評価", "送料"],
                        selection_mode="multi",
                    )
                    or []
                )
            )
            custom_message = st.text_area(
                "入れたい一言（任意）",
                placeholder="例：ギフト選びにも使える点を伝えたい",
                help="公開してよい、事実確認済みの内容だけを入力してください。",
                height=90,
            )

            option_columns = st.columns(3)
            target_length = option_columns[0].number_input(
                "目標文字数",
                min_value=int(profile["min_length"]),
                max_value=int(profile["max_length"]),
                value=int(profile["target_length"]),
                step=int(profile["step"]),
                key=f"target_length_{selected_channel}",
                help="媒体の厳密な上限ではなく、文章を整えるための目安です。",
            )
            hashtag_count = option_columns[1].number_input(
                "ハッシュタグ数",
                min_value=0,
                max_value=10,
                value=int(profile["hashtag_count"]),
                step=1,
                key=f"hashtag_count_{selected_channel}",
            )
            variation_count = int(
                option_columns[2].segmented_control(
                    "作成する案数",
                    [1, 2, 3],
                    default=3,
                    format_func=lambda value: f"{value}案",
                )
                or 1
            )

            detail_columns = st.columns(3)
            mode = detail_columns[0].selectbox(
                "生成方式",
                ["標準テンプレート", "LLM拡張"],
                help="LLM拡張はClaude Sonnet 5を使います。API利用料はAnthropic側で発生します。",
            )
            link_mode_label = detail_columns[1].selectbox(
                "リンクの入れ方",
                ["実際のURL", "プレースホルダー"],
            )
            pr_required = detail_columns[2].checkbox("PR必須案件", value=False)

            generated = st.form_submit_button(
                "投稿案を作成",
                icon=":material/auto_awesome:",
                type="primary",
            )

if generated:
    validation_errors = []
    if comparison_mode:
        if not 5 <= len(selected_ids) <= 7:
            validation_errors.append("比較する商品を5〜7点選んでください")
        if not article_genre.strip():
            validation_errors.append("ジャンルを入力してください")
        if not target_audience.strip():
            validation_errors.append("想定読者を入力してください")
        if not main_keyword.strip():
            validation_errors.append("狙うキーワードを入力してください")
    elif not selected_ids or not theme.strip():
        validation_errors.append("紹介する商品と投稿テーマを入力してください")

    if validation_errors:
        st.error("入力を確認してください：" + "／".join(validation_errors))
    else:
        products_by_id = {product.id: product for product in products}
        selected_products = [products_by_id[product_id] for product_id in selected_ids]
        if any(product.is_sample for product in selected_products):
            st.warning("架空のサンプル商品を含む投稿案は、操作確認専用で公開できません。")
        effective_pr = pr_required or pr_policy == "always"
        try:
            generator = get_content_generator(
                "llm" if mode == "LLM拡張" else "template",
                provider=settings.llm_provider,
                api_key=settings.claude_api_key,
                model=settings.anthropic_model,
                timeout_seconds=(
                    max(settings.anthropic_api_timeout_seconds, 120.0)
                    if comparison_mode
                    else settings.anthropic_api_timeout_seconds
                ),
            )
            spinner_message = (
                "Claudeがnote記事を作成しています（最大2分ほどかかる場合があります）…"
                if comparison_mode
                else (
                    "Claudeが投稿案を作成しています…"
                    if mode == "LLM拡張"
                    else "投稿案を作成しています…"
                )
            )
            with st.spinner(spinner_message):
                outputs = generator.generate_variations(
                    selected_channel,
                    GenerationContext(
                        products=selected_products,
                        theme=theme.strip(),
                        link_mode=("direct" if link_mode_label == "実際のURL" else "placeholder"),
                        disclosure=str(disclosure),
                        pr_required=effective_pr,
                        target_audience=target_audience.strip() or "商品選びで迷っている人",
                        tone=str(tone or "信頼感のある丁寧語"),
                        appeal_points=tuple(str(point) for point in (appeal_points or [])),
                        custom_message=custom_message.strip(),
                        target_length=int(target_length),
                        hashtag_count=int(hashtag_count),
                        article_format=article_format,
                        article_genre=article_genre.strip(),
                        main_keyword=main_keyword.strip(),
                    ),
                    int(variation_count or 1),
                )
        except (ContentGenerationError, ValueError) as exc:
            st.error(str(exc), icon=":material/error:")
            if mode == "LLM拡張":
                st.info("設定画面でClaude APIキーとモデル名を確認してください。")
        else:
            generation_id = uuid4().hex[:10]
            st.session_state.generated_variations = [
                {
                    **output.model_dump(),
                    "product_ids": list(selected_ids),
                    "theme": theme.strip(),
                    "pr_required": effective_pr,
                    "target_length": int(target_length),
                    "requested_hashtag_count": int(hashtag_count),
                    "article_format": article_format,
                    "generation_id": generation_id,
                }
                for output in outputs
            ]

drafts = st.session_state.get("generated_variations", [])
if drafts:
    st.divider()
    st.subheader("作成した投稿案")
    generation_id = str(drafts[0]["generation_id"])
    selected_index = st.segmented_control(
        "編集・保存する案",
        list(range(len(drafts))),
        default=0,
        format_func=lambda index: f"案{index + 1}｜{drafts[index]['metadata'].get('案の型', '')}",
        key=f"selected_variation_{generation_id}",
    )
    draft = drafts[int(selected_index or 0)]
    draft_index = int(selected_index or 0)

    with st.container(border=True):
        title = st.text_input(
            "タイトル",
            value=str(draft["title"]),
            key=f"generated_title_{generation_id}_{draft_index}",
        )
        body = st.text_area(
            "投稿本文",
            value=str(draft["body"]),
            height=520,
            key=f"generated_body_{generation_id}_{draft_index}",
        )

        analysis = analyze_copy(body, int(draft["target_length"]))
        metric_columns = st.columns(4)
        metric_columns[0].metric(
            "本文文字数（目安）",
            f"{analysis.character_count:,}字",
            delta=f"目標比 {analysis.difference:+,}字",
            border=True,
        )
        metric_columns[1].metric(
            "タイトル文字数",
            f"{len(title.strip()):,}字",
            border=True,
        )
        metric_columns[2].metric(
            "ハッシュタグ",
            f"{analysis.hashtag_count}個",
            delta=f"指定 {draft['requested_hashtag_count']}個",
            border=True,
        )
        metric_columns[3].metric(
            "日本語判定",
            analysis.japanese_status,
            delta=f"日本語比率 {analysis.japanese_ratio:.0%}",
            border=True,
        )

        if analysis.target_length and analysis.difference > 0:
            st.warning(
                f"目標文字数を{analysis.difference:,}字超えています。"
                "投稿先の仕様を確認し、必要に応じて短くしてください。",
                icon=":material/format_align_left:",
            )
        if analysis.japanese_status == "日本語を確認":
            st.warning("英字の割合が高めです。日本語として読みやすいか確認してください。")

        metadata = dict(draft.get("metadata", {}))
        if metadata:
            with st.expander("制作メモを見る", icon=":material/design_services:"):
                for label, value in metadata.items():
                    st.markdown(f"**{label}**")
                    if isinstance(value, list):
                        st.markdown("\n".join(f"- {item}" for item in value))
                    else:
                        st.write(value)

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
    st.subheader("公開前チェック")
    show_compliance_report(report.to_dict())
    st.caption("価格・在庫・送料・ポイントは変動するため、公開直前に商品ページで確認してください。")

    confirm = st.checkbox(
        "本文・リンク・広告表記・商品情報を確認しました（保存しても自動投稿されません）",
        key=f"confirm_draft_save_{generation_id}_{draft_index}",
    )
    action_columns = st.columns(2)
    safe_theme = re.sub(r"[^\w一-龥ぁ-んァ-ヴー-]", "_", str(draft["theme"]))[:40]
    action_columns[0].download_button(
        "本文をテキスト保存",
        data=f"{title}\n\n{body}",
        file_name=f"{safe_theme or '投稿案'}_{draft['channel']}.txt",
        mime="text/plain",
        icon=":material/download:",
    )
    save_draft = action_columns[1].button(
        "確認待ちとして保存",
        icon=":material/save:",
        type="primary",
        disabled=not confirm,
    )
    if save_draft:
        affiliate_url = next(
            (product.affiliate_url for product in selected_products if product.affiliate_url), ""
        )
        with session_scope() as session:
            content = create_content(
                session,
                channel=str(draft["channel"]),
                theme=str(draft["theme"]),
                title=title,
                body=body,
                product_ids=list(draft["product_ids"]),
                affiliate_url=affiliate_url,
                pr_required=bool(draft["pr_required"]),
                compliance_status=report.status,
                compliance_report=report.to_dict(),
                info_verified_at=info_verified_at if isinstance(info_verified_at, date) else None,
            )
        st.success(f"投稿ID {content.id} として確認待ちに保存しました。自動投稿は行いません。")
        st.session_state.generated_variations = []
        st.rerun()
