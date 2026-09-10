from __future__ import annotations

from decimal import Decimal
from hashlib import blake2s
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.database import session_scope
from app.research_catalog.briefs import (
    DEFAULT_DISCLOSURE_POLICY,
    brief_handoff_payload,
    build_article_brief,
)
from app.research_catalog.models import ResearchOffer, ResearchProduct
from app.research_catalog.outcomes import summarize_plan_performance
from app.research_catalog.quality import assess_product_quality, plan_ready_blockers
from app.research_catalog.repositories import (
    get_article_brief,
    get_research_plan,
    list_article_briefs,
    list_research_plans,
    list_research_products,
    save_article_brief,
    save_published_article_reference,
    save_research_plan,
)
from app.research_catalog.schemas import ResearchPlanInput, ResearchPlanStatus
from app.research_catalog.time import format_jst

PLAN_STATUS_LABELS = {
    "draft": "下書き",
    "researching": "調査中",
    "ready": "制作可能",
    "on_hold": "保留",
    "archived": "アーカイブ",
}
PRODUCT_STATUS_LABELS = {
    "candidate": "候補",
    "researching": "調査中",
    "comparable": "比較可能",
    "on_hold": "保留",
    "archived": "アーカイブ",
}
EVALUATION_CRITERIA = (
    "読者の悩みとの一致",
    "紹介可能な商品と報酬条件",
    "根拠のある比較ができるか",
    "競合に対して追加できる情報",
    "制作費と作業負担",
    "継続的に企画を作れるか",
)


def _primary_offer(product: ResearchProduct) -> ResearchOffer | None:
    return next((offer for offer in product.offers if offer.is_primary), None) or (
        product.offers[0] if product.offers else None
    )


def _amount(value: Decimal | None, currency: str) -> str:
    return "未確認" if value is None else f"{value} {currency}"


def _rate(value: float | None) -> str:
    return "未確認" if value is None else f"{value}%"


st.caption(
    "商品候補を根拠と一緒に比較し、制作企画と記事作成用ブリーフを保存します。"
    "AIによる不透明な点数付けや検索数の推測は行いません。"
)
freshness_days = int(
    st.number_input(
        "価格・報酬条件の再確認期限（日）",
        min_value=1,
        max_value=365,
        value=30,
        key="research_plan_freshness_days",
        help="制作可能にする際、代表販売先の条件確認日時をこの期限で判定します。",
    )
)

with session_scope() as session:
    products = list_research_products(session)
    plans = list_research_plans(session)

if plans:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "企画ID": plan.plan_code,
                    "タイトル": plan.title,
                    "状態": PLAN_STATUS_LABELS.get(plan.status, plan.status),
                    "商品数": len(plan.product_links),
                    "版": plan.version,
                    "ブリーフ数": len(plan.briefs),
                    "更新日時（JST）": format_jst(plan.updated_at),
                }
                for plan in plans
            ]
        ),
        hide_index=True,
        column_config={"タイトル": st.column_config.TextColumn(width="large")},
    )
else:
    st.info(
        "企画はまだありません。商品・調査管理で2商品以上を登録した後、"
        "この画面の「新規企画」から比較を始めてください。",
        icon=":material/compare_arrows:",
    )

st.subheader("比較・企画の登録", anchor=False)
plan_options = [0, *[plan.id for plan in plans]]
plan_selection_key = "research_plan_editor_selection"
next_plan_selection = st.session_state.pop("research_plan_editor_selection_next", None)
if next_plan_selection in plan_options:
    st.session_state[plan_selection_key] = next_plan_selection
selected_plan_id = int(
    st.selectbox(
        "編集対象",
        plan_options,
        format_func=lambda value: (
            "新規企画"
            if value == 0
            else next(
                f"{plan.plan_code}｜{plan.title}" for plan in plans if plan.id == value
            )
        ),
        key=plan_selection_key,
    )
)
current_plan = next((plan for plan in plans if plan.id == selected_plan_id), None)
editor_key = str(selected_plan_id or "new")

if current_plan:
    current_product_ids = [link.product_id for link in current_plan.product_links]
    saved_comparison_values = {
        link.product_id: dict(link.comparison_values_json)
        for link in current_plan.product_links
    }
else:
    current_product_ids = []
    saved_comparison_values = {}

eligible_product_ids = {product.id for product in products}
default_product_ids = [
    product_id for product_id in current_product_ids if product_id in eligible_product_ids
]
selected_product_ids = st.multiselect(
    "比較する商品（2商品以上）",
    [product.id for product in products],
    default=default_product_ids,
    format_func=lambda product_id: next(
        (
            f"{product.product_code}｜{product.product_name}｜"
            f"{product.model_number or '型番未確認'}｜{product.variant or 'サイズ未確認'}"
        )
        for product in products
        if product.id == product_id
    ),
    key=f"research_plan_products_{editor_key}",
    help="異なる型番・サイズは別の商品候補として表示します。",
)
selected_products = [
    product for product in products if product.id in set(selected_product_ids)
]

axes_text = st.text_area(
    "比較軸（1行に1項目・自由追加）",
    value=(
        "\n".join(current_plan.comparison_axes_json)
        if current_plan
        else "価格\n送料\n報酬条件"
    ),
    placeholder="例：容量\n必要な付属品\n手入れ",
    key=f"research_plan_axes_{editor_key}",
    help="ジャンル固有の項目も自由に追加できます。空欄の値は判定材料不足として扱います。",
)
comparison_axes = list(
    dict.fromkeys(
        line.strip()[:200] for line in axes_text.splitlines() if line.strip()
    )
)

if selected_products:
    common_rows = []
    for product in selected_products:
        offer = _primary_offer(product)
        quality = assess_product_quality(product, freshness_days=freshness_days)
        currency = offer.currency if offer else "JPY"
        confirmed_count = sum(
            item.verification_status == "confirmed_fact" for item in product.evidence
        )
        common_rows.append(
            {
                "商品ID": product.product_code,
                "商品名": product.product_name,
                "メーカー": product.manufacturer or "未確認",
                "型番": product.model_number or "未確認",
                "サイズ・種類": product.variant or "未確認",
                "状態": PRODUCT_STATUS_LABELS.get(product.status, product.status),
                "代表販売先": offer.seller_name if offer else "未確認",
                "価格": _amount(offer.price, currency) if offer else "未確認",
                "送料": _amount(offer.shipping_fee, currency) if offer else "未確認",
                "報酬率": _rate(offer.commission_rate) if offer else "未確認",
                "確認済み根拠": confirmed_count or "判定材料不足",
                "未確認・仮説": sum(
                    item.verification_status != "confirmed_fact"
                    for item in product.evidence
                ),
                "制作準備": quality.label,
                "不足・期限切れ": "／".join(issue.message for issue in quality.issues)
                or "なし",
            }
        )
    st.markdown("**登録情報の比較**")
    st.dataframe(pd.DataFrame(common_rows), hide_index=True, width="stretch")

    evidence_rows: list[dict[str, object]] = []
    for product in selected_products:
        if product.evidence:
            evidence_rows.extend(
                {
                    "商品ID": product.product_code,
                    "確認内容": item.summary,
                    "情報状態": item.verification_status,
                    "出典種類": item.source_type,
                    "実使用": "あり" if item.actually_used else "なし／未確認",
                    "使用条件・観察": "／".join(
                        value
                        for value in (item.use_conditions, item.observations)
                        if value
                    )
                    or "未記録",
                    "出典URL": item.source_url,
                }
                for item in product.evidence
            )
        else:
            evidence_rows.append(
                {
                    "商品ID": product.product_code,
                    "確認内容": "判定材料不足",
                    "情報状態": "unverified",
                    "出典種類": "未登録",
                    "実使用": "なし／未確認",
                    "使用条件・観察": "未記録",
                    "出典URL": "",
                }
            )
    with st.expander("商品の調査根拠を見比べる", expanded=True):
        st.dataframe(
            pd.DataFrame(evidence_rows),
            hide_index=True,
            width="stretch",
            column_config={"出典URL": st.column_config.LinkColumn()},
        )

    custom_rows = []
    for product in selected_products:
        row = {
            "内部ID": product.id,
            "商品ID": product.product_code,
            "商品名": product.product_name,
        }
        previous_values = saved_comparison_values.get(product.id, {})
        row.update({axis: previous_values.get(axis, "") for axis in comparison_axes})
        custom_rows.append(row)
    editor_fingerprint = blake2s(
        repr((selected_product_ids, comparison_axes)).encode("utf-8")
    ).hexdigest()[:10]
    st.markdown("**ジャンル固有の比較値**")
    comparison_editor = st.data_editor(
        pd.DataFrame(custom_rows),
        hide_index=True,
        width="stretch",
        disabled=["内部ID", "商品ID", "商品名"],
        column_config={"内部ID": None},
        key=f"research_comparison_values_{editor_key}_{editor_fingerprint}",
    )
    missing_cells = sum(
        not str(row.get(axis, "")).strip()
        for row in comparison_editor.to_dict(orient="records")
        for axis in comparison_axes
    )
    if missing_cells:
        st.warning(
            f"比較値に{missing_cells}件の空欄があります。空欄は「判定材料不足」として保存されます。",
            icon=":material/help:",
        )
else:
    comparison_editor = pd.DataFrame()
    st.info("比較する商品を選ぶと、登録済みの条件と根拠を横並びで確認できます。")

with st.form(f"research_plan_form_{editor_key}"):
    title = st.text_input("企画タイトル", value=current_plan.title if current_plan else "")
    audience = st.text_area(
        "想定読者", value=current_plan.audience if current_plan else "", height=80
    )
    pain_point = st.text_area(
        "解決する具体的な悩み",
        value=current_plan.pain_point if current_plan else "",
        height=80,
    )
    article_purpose = st.text_area(
        "記事の目的",
        value=current_plan.article_purpose if current_plan else "",
        height=80,
    )
    st.markdown("**企画判断のメモと根拠**")
    st.caption("入力がない欄は、ブリーフ上で「判定材料不足」と明示されます。")
    evaluation: dict[str, dict[str, str]] = {}
    for index, criterion in enumerate(EVALUATION_CRITERIA):
        previous = (
            dict(current_plan.evaluation_json).get(criterion, {})
            if current_plan
            else {}
        )
        st.markdown(f"{index + 1}. {criterion}")
        evaluation_columns = st.columns(2)
        memo = evaluation_columns[0].text_area(
            "評価メモ",
            value=str(previous.get("memo", "")),
            key=f"research_eval_memo_{editor_key}_{index}",
            height=80,
        )
        basis = evaluation_columns[1].text_area(
            "判断根拠",
            value=str(previous.get("basis", "")),
            key=f"research_eval_basis_{editor_key}_{index}",
            height=80,
        )
        evaluation[criterion] = {"memo": memo, "basis": basis}
    adoption_reason = st.text_area(
        "採用理由", value=current_plan.adoption_reason if current_plan else "", height=100
    )
    weaknesses = st.text_area(
        "弱点", value=current_plan.weaknesses if current_plan else "", height=100
    )
    additional_checks = st.text_area(
        "追加確認事項",
        value=current_plan.additional_checks if current_plan else "",
        height=100,
    )
    status = st.selectbox(
        "状態",
        list(PLAN_STATUS_LABELS),
        index=(
            list(PLAN_STATUS_LABELS).index(current_plan.status)
            if current_plan and current_plan.status in PLAN_STATUS_LABELS
            else 0
        ),
        format_func=lambda value: PLAN_STATUS_LABELS[value],
    )
    save_plan_button = st.form_submit_button(
        "比較企画を保存", icon=":material/save:", type="primary"
    )

if save_plan_button:
    try:
        comparison_values: dict[int, dict[str, str]] = {}
        for edited_row in comparison_editor.to_dict(orient="records"):
            product_id = int(str(edited_row["内部ID"]))
            comparison_values[product_id] = {
                axis: str(edited_row.get(axis, "")).strip() for axis in comparison_axes
            }
        plan_payload = ResearchPlanInput(
            title=title,
            audience=audience,
            pain_point=pain_point,
            article_purpose=article_purpose,
            comparison_axes=comparison_axes,
            evaluation=evaluation,
            adoption_reason=adoption_reason,
            weaknesses=weaknesses,
            additional_checks=additional_checks,
            status=ResearchPlanStatus(status),
        )
        if plan_payload.status is ResearchPlanStatus.READY:
            blockers = plan_ready_blockers(
                selected_products,
                comparison_axes,
                comparison_values,
                evaluation,
                freshness_days=freshness_days,
            )
            if blockers:
                preview = "／".join(blockers[:8])
                suffix = f"（ほか{len(blockers) - 8}件）" if len(blockers) > 8 else ""
                raise ValueError(
                    "制作可能にする前に次を確認してください: " + preview + suffix
                )
        with session_scope() as session:
            saved_plan = save_research_plan(
                session,
                plan_payload,
                comparison_values,
                plan_id=selected_plan_id or None,
            )
        st.session_state["research_plan_editor_selection_next"] = saved_plan.id
        st.toast(f"比較企画 {saved_plan.plan_code} の第{saved_plan.version}版を保存しました。")
        st.rerun()
    except (ValidationError, ValueError) as exc:
        st.error(f"比較企画を保存できません：{exc}")

if current_plan is None:
    st.info("企画を保存すると、APIを使わずに記事作成用ブリーフを生成できます。")
    st.stop()

with session_scope() as session:
    performance = summarize_plan_performance(session, current_plan.id)

st.divider()
st.subheader("取り込み済み成果との照合", anchor=False)
st.caption(
    "楽天成果CSVを正確一致で関連付けた保存商品、またはこの企画から作った"
    "制作コンテンツに紐づく行だけを集計します。成果のない商品を推測で評価しません。"
)
with st.container(horizontal=True):
    st.metric("クリック", f"{performance.clicks:,}", border=True)
    st.metric("注文", f"{performance.orders:,}", border=True)
    st.metric("売上", f"¥{performance.sales:,.0f}", border=True)
    st.metric("報酬", f"¥{performance.reward:,.0f}", border=True)
    st.metric(
        "購入転換率",
        (
            f"{performance.conversion_rate:.2%}"
            if performance.conversion_rate is not None
            else "判定材料不足"
        ),
        border=True,
    )
if performance.rows:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "日付": row.date,
                    "媒体": row.channel or "未設定",
                    "商品名": row.product_name or "未設定",
                    "クリック": row.clicks,
                    "注文": row.orders,
                    "売上": row.sales,
                    "報酬": row.reward,
                    "対応根拠": row.attribution,
                    "取込元": row.source_file,
                }
                for row in performance.rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.info(
        "この企画へ関連付けられた成果データはまだありません。成果分析でCSVを取り込み、"
        "保存商品との照合を実行できます。"
    )

st.divider()
st.subheader("記事作成用ブリーフ", anchor=False)
st.caption(
    f"{current_plan.plan_code}｜現在第{current_plan.version}版｜"
    f"保存済み改訂履歴 {len(current_plan.revisions)}件"
)
disclosure_policy = st.text_input(
    "紹介料を受け取る旨の明示方針",
    value=DEFAULT_DISCLOSURE_POLICY,
    key=f"research_disclosure_{current_plan.id}",
)
if st.button(
    "登録情報からブリーフを生成・保存",
    icon=":material/description:",
    type="primary",
    key=f"generate_research_brief_{current_plan.id}",
):
    with session_scope() as session:
        fresh_plan = get_research_plan(session, current_plan.id)
        if fresh_plan is None:
            st.error("比較企画が見つかりません。")
            st.stop()
        brief_json, markdown = build_article_brief(fresh_plan, disclosure_policy)
        brief = save_article_brief(
            session,
            plan_id=fresh_plan.id,
            title=fresh_plan.title,
            markdown=markdown,
            brief_json=brief_json,
            disclosure_policy=brief_json["disclosure_policy"],
        )
    st.session_state[f"research_brief_selection_next_{current_plan.id}"] = brief.id
    st.toast(f"記事作成用ブリーフ {brief.brief_code} を保存しました。")
    st.rerun()

with session_scope() as session:
    briefs = list_article_briefs(session, plan_id=current_plan.id)

if not briefs:
    st.info(
        "ブリーフはまだありません。上のボタンで生成しても記事や投稿は作成されず、"
        "外部APIも呼び出しません。"
    )
    st.stop()

brief_options = [brief.id for brief in briefs]
brief_selection_key = f"research_brief_selection_{current_plan.id}"
next_brief_selection = st.session_state.pop(
    f"research_brief_selection_next_{current_plan.id}", None
)
if next_brief_selection in brief_options:
    st.session_state[brief_selection_key] = next_brief_selection
selected_brief_id = int(
    st.selectbox(
        "表示するブリーフ",
        brief_options,
        format_func=lambda value: next(
            f"{brief.brief_code}｜第{brief.version}版｜{format_jst(brief.created_at)}"
            for brief in briefs
            if brief.id == value
        ),
        key=brief_selection_key,
    )
)
selected_brief = next(brief for brief in briefs if brief.id == selected_brief_id)

st.code(selected_brief.markdown, language="markdown", wrap_lines=True)
st.caption("右上のコピーアイコンで全文をコピーできます。")
st.download_button(
    "Markdown保存",
    data=selected_brief.markdown.encode("utf-8"),
    file_name=f"{selected_brief.brief_code}.md",
    mime="text/markdown",
    icon=":material/download:",
    key=f"download_research_brief_{selected_brief.id}",
)

linked_content_ids = [link.content_id for link in selected_brief.content_links]
if linked_content_ids:
    st.success(
        "このブリーフから保存された制作コンテンツ: "
        + ", ".join(f"投稿ID {content_id}" for content_id in linked_content_ids)
    )
else:
    st.caption("このブリーフから保存された制作コンテンツはまだありません。")

st.markdown("**公開済みnote記事との照合（Pinterest制作へ渡す場合のみ）**")
with st.form(f"published_note_reference_{selected_brief.id}"):
    published_url = st.text_input(
        "公開済みnote記事URL",
        value=selected_brief.published_article_url,
        placeholder="https://note.com/...",
    )
    published_title = st.text_input(
        "実際に公開した記事タイトル", value=selected_brief.published_article_title
    )
    published_summary = st.text_area(
        "実際に公開した記事の要約",
        value=selected_brief.published_article_summary,
        height=100,
    )
    match_confirmed = st.checkbox(
        "企画の商品・比較軸・事実と、公開記事の内容が一致することを確認しました",
        value=selected_brief.published_match_confirmed_at is not None,
    )
    save_reference = st.form_submit_button(
        "公開記事情報を保存", icon=":material/verified:"
    )
if save_reference:
    try:
        with session_scope() as session:
            save_published_article_reference(
                session,
                selected_brief.id,
                url=published_url,
                title=published_title,
                summary=published_summary,
                match_confirmed=match_confirmed,
            )
        st.toast("公開済みnote記事との照合情報を保存しました。")
        st.rerun()
    except ValueError as exc:
        st.error(f"公開記事情報を保存できません：{exc}")

st.markdown("**既存のコンテンツ制作へ引き継ぐ**")
st.caption(
    "引き継ぎは入力欄を準備するだけです。この画面から記事生成・画像生成・投稿は行いません。"
)
handoff_columns = st.columns(2)
if handoff_columns[0].button(
    "note記事制作へ",
    icon=":material/edit_note:",
    key=f"handoff_note_{selected_brief.id}",
):
    with session_scope() as session:
        fresh_brief = get_article_brief(session, selected_brief.id)
        if fresh_brief is None:
            st.error("記事作成用ブリーフが見つかりません。")
            st.stop()
        st.session_state["research_brief_handoff"] = brief_handoff_payload(
            fresh_brief, channel="note"
        )
    st.switch_page(Path(__file__).resolve().with_name("content_creation.py"))

pinterest_ready = selected_brief.published_match_confirmed_at is not None
if handoff_columns[1].button(
    "Pinterest制作へ",
    icon=":material/push_pin:",
    disabled=not pinterest_ready,
    help=(
        None
        if pinterest_ready
        else "公開済みnote記事のURL・タイトル・要約を保存し、一致確認してください。"
    ),
    key=f"handoff_pinterest_{selected_brief.id}",
):
    try:
        with session_scope() as session:
            fresh_brief = get_article_brief(session, selected_brief.id)
            if fresh_brief is None:
                raise ValueError("記事作成用ブリーフが見つかりません")
            st.session_state["research_brief_handoff"] = brief_handoff_payload(
                fresh_brief, channel="Pinterest"
            )
        st.switch_page(Path(__file__).resolve().with_name("content_creation.py"))
    except ValueError as exc:
        st.error(str(exc))
