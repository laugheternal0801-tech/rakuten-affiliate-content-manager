from __future__ import annotations

import re
from datetime import date
from uuid import uuid4

import streamlit as st

from app.config import get_settings
from app.database import session_scope
from app.repositories import (
    create_content,
    delete_note_image_asset,
    get_setting,
    list_note_image_assets,
    list_products,
    save_note_image_assets,
    select_note_image_asset,
    set_setting,
)
from app.services.article_revision import REVISION_TARGETS, ClaudeArticleRevisionService
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
from app.services.note_image_generation import (
    GPT_IMAGE_MODEL,
    NoteImageGenerationError,
    OpenAINoteImageGenerator,
)
from app.streamlit_support import show_compliance_report, show_note_posting_assistant

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
st.session_state.setdefault("pending_article_revisions", {})

clone_request = st.session_state.pop("clone_content_request", None)
if isinstance(clone_request, dict):
    st.session_state["active_clone_request"] = clone_request
    st.session_state["creation_channel"] = str(clone_request.get("channel", "note"))
active_clone = st.session_state.get("active_clone_request")
if isinstance(active_clone, dict):
    st.info(
        f"過去記事「{active_clone.get('source_title', '')}」の設定を複製しました。"
        "商品やテーマを入れ替えて、新しい記事として作成できます。",
        icon=":material/content_copy:",
    )

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
    article_genre = str(
        active_clone.get("theme", "")
        if isinstance(active_clone, dict)
        else comparison_brief.get("genre", "")
    )
    main_keyword = str(
        active_clone.get("main_keyword", active_clone.get("theme", ""))
        if isinstance(active_clone, dict)
        else comparison_brief.get("main_keyword", "")
    )
    eligible_products = [product for product in products if not product.is_sample]
    eligible_ids = {product.id for product in eligible_products}
    requested_default_ids = (
        active_clone.get("product_ids", [])
        if isinstance(active_clone, dict)
        else comparison_brief.get("product_ids", [])
    )
    default_comparison_ids = [
        int(product_id)
        for product_id in requested_default_ids
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
                value=str(
                    active_clone.get("target_audience", "")
                    if isinstance(active_clone, dict)
                    else comparison_brief.get("target_audience", "")
                ),
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
            create_image_together = st.checkbox(
                "記事と一緒にアイキャッチ画像も1枚作る",
                value=False,
                disabled=not settings.note_image_generation_configured,
                help="記事が完成したあと、そのタイトルをテーマにGPT Image 2で1枚生成します。",
            )
            together_image_motifs = st.text_input(
                "アイキャッチに入れたい要素（任意）",
                placeholder="例：白いカップ、木製テーブル、朝の自然光",
                disabled=not settings.note_image_generation_configured,
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
    create_image_together = False
    together_image_motifs = ""
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
                default=(
                    [
                        int(product_id)
                        for product_id in active_clone.get("product_ids", [])
                        if int(product_id) in {product.id for product in products}
                    ]
                    if isinstance(active_clone, dict)
                    else []
                ),
            )
            theme = st.text_input(
                "投稿テーマ",
                value=(
                    str(active_clone.get("theme", ""))
                    if isinstance(active_clone, dict)
                    else ""
                ),
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
                note_format_playbook_url=str(settings.note_format_playbook_url),
                note_format_playbook_timeout_seconds=(
                    settings.note_format_playbook_timeout_seconds
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
            st.session_state.pop("active_clone_request", None)
            if comparison_mode and create_image_together:
                try:
                    image_generator = OpenAINoteImageGenerator(
                        settings.openai_api_key,
                        timeout_seconds=settings.openai_image_timeout_seconds,
                    )
                    with st.spinner(
                        "記事が完成しました。続けてGPT Image 2がアイキャッチを作成しています…"
                    ):
                        generated_image = image_generator.generate(
                            outputs[0].title,
                            together_image_motifs,
                        )
                    with session_scope() as session:
                        saved_assets = save_note_image_assets(
                            session,
                            batch_id=f"{generation_id}_0",
                            article_title=outputs[0].title,
                            theme=outputs[0].title,
                            motifs=together_image_motifs,
                            prompt=generated_image.prompt,
                            model=generated_image.model,
                            image_data=[generated_image.image_bytes],
                        )
                    st.session_state[f"selected_note_image_{generation_id}_0"] = saved_assets[0].id
                    st.success("記事とアイキャッチ画像をまとめて作成しました。")
                except (NoteImageGenerationError, ValueError) as exc:
                    st.warning(
                        f"記事は作成できましたが、画像生成だけ失敗しました：{exc}",
                        icon=":material/image_not_supported:",
                    )

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
    draft_state_key = f"{generation_id}_{draft_index}"
    title_widget_key = f"generated_title_{draft_state_key}"
    body_widget_key = f"generated_body_{draft_state_key}"
    apply_revision = st.session_state.pop("apply_article_revision", None)
    if isinstance(apply_revision, dict) and apply_revision.get("draft_key") == draft_state_key:
        draft["title"] = str(apply_revision["title"])
        draft["body"] = str(apply_revision["body"])
        drafts[draft_index] = draft
        st.session_state.generated_variations = drafts
        st.session_state.pop(title_widget_key, None)
        st.session_state.pop(body_widget_key, None)
        pending = dict(st.session_state.pending_article_revisions)
        pending.pop(draft_state_key, None)
        st.session_state.pending_article_revisions = pending
        st.toast("選択した部分の修正文を反映しました。")

    with st.container(border=True):
        title = st.text_input(
            "タイトル",
            value=str(draft["title"]),
            key=title_widget_key,
        )
        body = st.text_area(
            "投稿本文",
            value=str(draft["body"]),
            height=520,
            key=body_widget_key,
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

    if str(draft["channel"]) == "note":
        metadata = dict(draft.get("metadata", {}))
        seo_title_key = f"seo_title_{draft_state_key}"
        seo_summary_key = f"seo_summary_{draft_state_key}"
        seo_hashtags_key = f"seo_hashtags_{draft_state_key}"
        st.session_state.setdefault(seo_title_key, str(metadata.get("SEOタイトル", title)))
        st.session_state.setdefault(seo_summary_key, str(metadata.get("記事要約", "")))
        suggested_hashtags = metadata.get("推奨ハッシュタグ", [])
        if isinstance(suggested_hashtags, list):
            suggested_hashtag_text = " ".join(str(tag) for tag in suggested_hashtags)
        else:
            suggested_hashtag_text = str(suggested_hashtags)
        st.session_state.setdefault(seo_hashtags_key, suggested_hashtag_text)

        with st.container(border=True):
            st.subheader("SEO・投稿情報")
            st.caption("Claudeが記事とは別に作成した候補です。投稿前に自由に編集できます。")
            st.text_input("SEOタイトル", key=seo_title_key)
            st.text_area("記事要約", key=seo_summary_key, height=100)
            st.text_input("推奨ハッシュタグ", key=seo_hashtags_key)

        with st.container(border=True):
            st.subheader("記事の一部分だけ再生成")
            st.caption("選んだ部分だけをClaudeが書き直します。確認してから本文へ反映できます。")
            with st.form(f"article_revision_form_{draft_state_key}"):
                revision_target = st.selectbox(
                    "再生成する部分",
                    list(REVISION_TARGETS),
                    format_func=lambda target: REVISION_TARGETS[target],
                )
                revision_instruction = st.text_input(
                    "直し方の希望（任意）",
                    placeholder="例：もう少し短く、初心者にも分かりやすく",
                )
                request_revision = st.form_submit_button(
                    "選んだ部分を再生成",
                    icon=":material/refresh:",
                    type="primary",
                    disabled=not settings.llm_configured,
                )

            if request_revision:
                try:
                    revision_service = ClaudeArticleRevisionService(
                        settings.claude_api_key,
                        model=settings.anthropic_model,
                        timeout_seconds=settings.anthropic_api_timeout_seconds,
                    )
                    with st.spinner("Claudeが選択部分だけを書き直しています…"):
                        revision = revision_service.revise(
                            title=title,
                            body=body,
                            target=str(revision_target),
                            instruction=revision_instruction,
                        )
                except (ContentGenerationError, ValueError) as exc:
                    st.error(str(exc), icon=":material/error:")
                else:
                    pending = dict(st.session_state.pending_article_revisions)
                    pending[draft_state_key] = {
                        "target_label": revision.target_label,
                        "original": revision.original,
                        "replacement": revision.replacement,
                        "summary": revision.summary,
                        "title": revision.title,
                        "body": revision.body,
                    }
                    st.session_state.pending_article_revisions = pending

            pending_revision = st.session_state.pending_article_revisions.get(draft_state_key)
            if isinstance(pending_revision, dict):
                st.info(
                    f"{pending_revision['target_label']}を修正しました："
                    f"{pending_revision['summary']}"
                )
                before_column, after_column = st.columns(2)
                before_column.text_area(
                    "変更前",
                    value=str(pending_revision["original"]),
                    height=240,
                    disabled=True,
                    key=f"revision_before_{draft_state_key}",
                )
                after_column.text_area(
                    "変更後",
                    value=str(pending_revision["replacement"]),
                    height=240,
                    disabled=True,
                    key=f"revision_after_{draft_state_key}",
                )
                with st.container(horizontal=True):
                    if st.button(
                        "修正文を本文に反映",
                        key=f"apply_revision_{draft_state_key}",
                        type="primary",
                        icon=":material/check:",
                    ):
                        st.session_state.apply_article_revision = {
                            "draft_key": draft_state_key,
                            "title": pending_revision["title"],
                            "body": pending_revision["body"],
                        }
                        st.rerun()
                    if st.button(
                        "修正文を破棄",
                        key=f"discard_revision_{draft_state_key}",
                        icon=":material/delete:",
                    ):
                        pending = dict(st.session_state.pending_article_revisions)
                        pending.pop(draft_state_key, None)
                        st.session_state.pending_article_revisions = pending
                        st.rerun()

        image_state_key = draft_state_key
        image_theme_key = f"note_image_theme_{image_state_key}"
        image_motifs_key = f"note_image_motifs_{image_state_key}"
        if image_theme_key not in st.session_state:
            st.session_state[image_theme_key] = title.strip() or str(draft["theme"])

        with st.container(border=True):
            st.subheader("noteアイキャッチ画像")
            if settings.note_image_generation_configured:
                st.badge(
                    f"{GPT_IMAGE_MODEL} 利用可能",
                    icon=":material/image:",
                    color="green",
                )
            else:
                st.info(
                    "画像生成を使うには、設定画面の「noteアイキャッチ画像」に"
                    "OpenAI APIキーを登録してください。",
                    icon=":material/key:",
                )

            with st.form(f"note_image_generation_form_{image_state_key}"):
                image_theme = st.text_input(
                    "記事テーマ",
                    key=image_theme_key,
                    placeholder="例：忙しい朝に選ぶドリップコーヒー比較",
                )
                image_motifs = st.text_area(
                    "入れたい要素（任意）",
                    key=image_motifs_key,
                    placeholder="例：白いコーヒーカップ、木製テーブル、朝の自然光",
                    help="商品ロゴ・価格・URLは入れず、雰囲気や小物を短く指定してください。",
                    height=80,
                )
                image_count = int(
                    st.segmented_control(
                        "作成する画像数",
                        [1, 3],
                        default=3,
                        format_func=lambda count: f"{count}枚",
                        help="3枚を選ぶと、1回の操作で比較候補を3案作成します。",
                    )
                    or 1
                )
                generate_note_image = st.form_submit_button(
                    "アイキャッチ画像を生成",
                    icon=":material/auto_awesome:",
                    type="primary",
                    disabled=not settings.note_image_generation_configured,
                )

            st.caption(
                "GPT Image 2のmedium品質で生成します。選んだ枚数分の画像生成として扱われ、"
                "最大2分ほどかかる場合があります。"
            )

            if generate_note_image:
                if not image_theme.strip():
                    st.error("記事テーマを入力してください。")
                else:
                    try:
                        image_generator = OpenAINoteImageGenerator(
                            settings.openai_api_key,
                            timeout_seconds=settings.openai_image_timeout_seconds,
                        )
                        with st.spinner(
                            "GPT Image 2がnote用アイキャッチを生成しています（最大2分ほど）…"
                        ):
                            generated_images = image_generator.generate_variations(
                                image_theme,
                                image_motifs,
                                count=image_count,
                            )
                    except (NoteImageGenerationError, ValueError) as exc:
                        st.error(str(exc), icon=":material/error:")
                    else:
                        batch_id = f"{image_state_key}_{uuid4().hex[:8]}"
                        with session_scope() as session:
                            saved_assets = save_note_image_assets(
                                session,
                                batch_id=batch_id,
                                article_title=title,
                                theme=image_theme,
                                motifs=image_motifs,
                                prompt=generated_images[0].prompt,
                                model=generated_images[0].model,
                                image_data=[image.image_bytes for image in generated_images],
                            )
                        st.session_state[f"current_note_image_batch_{image_state_key}"] = batch_id
                        if len(saved_assets) == 1:
                            st.session_state[f"selected_note_image_{image_state_key}"] = (
                                saved_assets[0].id
                            )
                        st.success(
                            f"note推奨サイズの画像を{len(saved_assets)}枚生成し、履歴へ保存しました。"
                        )

            with session_scope() as session:
                image_assets = list_note_image_assets(session)
            current_batch_id = st.session_state.get(
                f"current_note_image_batch_{image_state_key}", image_state_key
            )
            current_assets = [
                asset for asset in image_assets if asset.batch_id == current_batch_id
            ]
            if current_assets:
                st.markdown("**今回の候補**")
                candidate_columns = st.columns(len(current_assets))
                for candidate_number, (column, asset) in enumerate(
                    zip(candidate_columns, current_assets, strict=True), 1
                ):
                    with column:
                        st.image(asset.image_data, caption=f"候補{candidate_number}")
                        if st.button(
                            "この画像を選ぶ",
                            key=f"select_note_image_{asset.id}",
                            type="primary" if asset.is_selected else "secondary",
                            icon=":material/check_circle:",
                        ):
                            with session_scope() as session:
                                select_note_image_asset(session, asset.id)
                            st.session_state[f"selected_note_image_{image_state_key}"] = asset.id
                            st.rerun()

            selected_asset_id = st.session_state.get(f"selected_note_image_{image_state_key}")
            image_result = next(
                (asset for asset in image_assets if asset.id == selected_asset_id),
                next((asset for asset in current_assets if asset.is_selected), None),
            )
            if image_result is not None:
                st.markdown("**選択中の画像**")
                st.image(
                    image_result.image_data,
                    caption="note見出し画像｜1280×670px PNG",
                    width="stretch",
                )
                image_file_theme = re.sub(
                    r"[^\w一-龥ぁ-んァ-ヴー-]", "_", image_result.theme
                )[:40]
                st.download_button(
                    "アイキャッチ画像をPNG保存",
                    data=image_result.image_data,
                    file_name=f"{image_file_theme or 'noteアイキャッチ'}_1280x670.png",
                    mime="image/png",
                    icon=":material/download:",
                    on_click="ignore",
                )
                with st.expander("画像生成に使ったプロンプトを見る"):
                    st.code(image_result.prompt, language="text", wrap_lines=True)

            if image_assets:
                with st.expander("画像の生成履歴・削除", icon=":material/history:"):
                    history_asset_id = st.selectbox(
                        "履歴から画像を選択",
                        [asset.id for asset in image_assets],
                        format_func=lambda asset_id: next(
                            (
                                f"#{asset.id}｜{asset.created_at:%Y/%m/%d %H:%M}｜"
                                f"{asset.theme[:35]}"
                                + ("｜選択中" if asset.is_selected else "")
                            )
                            for asset in image_assets
                            if asset.id == asset_id
                        ),
                        key=f"note_image_history_{image_state_key}",
                    )
                    history_asset = next(
                        asset for asset in image_assets if asset.id == history_asset_id
                    )
                    st.image(history_asset.image_data, caption=history_asset.theme, width=480)
                    with st.container(horizontal=True):
                        if st.button(
                            "履歴の画像を選択",
                            key=f"select_history_note_image_{image_state_key}",
                            icon=":material/check:",
                        ):
                            with session_scope() as session:
                                select_note_image_asset(session, history_asset.id)
                            st.session_state[f"selected_note_image_{image_state_key}"] = (
                                history_asset.id
                            )
                            st.rerun()
                    confirm_image_delete = st.checkbox(
                        "この画像を履歴から削除することを確認",
                        key=f"confirm_delete_note_image_{image_state_key}",
                    )
                    if st.button(
                        "選択した履歴画像を削除",
                        key=f"delete_history_note_image_{image_state_key}",
                        icon=":material/delete:",
                        disabled=not confirm_image_delete,
                    ):
                        with session_scope() as session:
                            deleted = delete_note_image_asset(session, history_asset.id)
                        if deleted:
                            if selected_asset_id == history_asset.id:
                                st.session_state.pop(
                                    f"selected_note_image_{image_state_key}", None
                                )
                            st.toast("画像を履歴から削除しました。")
                            st.rerun()

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
    if str(draft["channel"]) == "note":
        show_note_posting_assistant(
            title=title,
            body=body,
            enabled=confirm and report.status != "投稿不可",
            key=f"open_note_editor_{generation_id}_{draft_index}",
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
            if str(draft["channel"]) == "note":
                set_setting(
                    session,
                    f"content_seo_{content.id}",
                    {
                        "seo_title": str(st.session_state.get(seo_title_key, "")),
                        "summary": str(st.session_state.get(seo_summary_key, "")),
                        "hashtags": str(st.session_state.get(seo_hashtags_key, "")),
                    },
                )
        st.success(f"投稿ID {content.id} として確認待ちに保存しました。自動投稿は行いません。")
        st.session_state.generated_variations = []
        st.rerun()
