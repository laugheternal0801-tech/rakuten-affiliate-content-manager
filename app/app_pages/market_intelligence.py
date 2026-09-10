from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from app.config import get_settings
from app.database import session_scope
from app.market_intelligence.connectors.registry import build_source_registry
from app.market_intelligence.engine import MarketIntelligenceEngine
from app.market_intelligence.reporting import report_as_html, report_as_json
from app.market_intelligence.repositories import (
    get_evidence,
    get_items,
    get_report,
    list_runs,
)
from app.market_intelligence.schemas import (
    ALL_SOURCES,
    AvailabilityStatus,
    ResearchDepth,
    ResearchRequest,
    ResearchStatus,
    SourceName,
)

SOURCE_LABELS = {
    SourceName.X: "X",
    SourceName.REDDIT: "Reddit",
    SourceName.YOUTUBE: "YouTube",
    SourceName.TIKTOK: "TikTok",
    SourceName.INSTAGRAM: "Instagram",
    SourceName.PINTEREST: "Pinterest",
    SourceName.WEB: "Web / RSS",
}
STATUS_LABELS = {
    AvailabilityStatus.AVAILABLE: "利用可能",
    AvailabilityStatus.NOT_CONFIGURED: "未設定",
    AvailabilityStatus.UNAVAILABLE: "利用不可",
    AvailabilityStatus.DEGRADED: "縮退",
    AvailabilityStatus.ERROR: "エラー",
}


def _split_values(value: str) -> list[str]:
    return list(
        dict.fromkeys(item.strip() for item in value.replace("\n", ",").split(",") if item.strip())
    )


@st.cache_data(ttl=60, show_spinner=False)
def capability_rows() -> list[dict[str, Any]]:
    settings = get_settings()
    registry = build_source_registry(settings, mock_mode=False)
    health = asyncio.run(registry.health_check_all())
    return [
        {
            "Source": SOURCE_LABELS[source],
            "Status": STATUS_LABELS[item.status],
            "Mode": item.capabilities.mode.value,
            "Recent Search": item.capabilities.recent_search,
            "Historical Search": item.capabilities.historical_search,
            "Metrics": item.capabilities.metrics,
            "Manual Import": item.capabilities.manual_import,
            "Message": item.message,
        }
        for source, item in health.items()
    ]


def _estimate_calls(depth: ResearchDepth, provider_count: int) -> int:
    if provider_count == 0:
        return 0
    specialist = {
        ResearchDepth.QUICK: 1,
        ResearchDepth.STANDARD: 5,
        ResearchDepth.DEEP: 5 * min(provider_count, 3),
    }[depth]
    return 1 + specialist


def _show_claims(claims: list[Any]) -> None:
    if not claims:
        st.info("表示できる検証済みClaimはありません。")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Claim": claim.claim,
                    "Type": claim.type,
                    "Confidence": claim.confidence.score,
                    "Label": claim.confidence.label,
                    "Verification": claim.verification.value,
                    "Platforms": ", ".join(platform.value for platform in claim.platforms),
                    "Evidence IDs": ", ".join(claim.evidence_ids),
                }
                for claim in claims
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Confidence": st.column_config.ProgressColumn(
                "Confidence", min_value=0.0, max_value=1.0, format="%.2f"
            )
        },
    )


settings = get_settings()
st.caption(
    "複数SNS・WebのEvidenceを共通形式へ統合し、独立AgentとCriticで検証してから結論を作ります。"
)

with st.expander("Source Capability Registry", expanded=True, icon=":material/hub:"):
    rows = capability_rows()
    available = sum(row["Status"] == "利用可能" for row in rows)
    with st.container(horizontal=True):
        st.metric("登録Source", len(rows), border=True)
        st.metric("現在利用可能", available, border=True)
        st.metric("未設定・縮退", len(rows) - available, border=True)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.markdown("**LLM Capability（ローカル設定）**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Provider": "OpenAI",
                    "Status": (
                        "設定済み"
                        if settings.openai_api_key and settings.openai_model.strip()
                        else "未設定"
                    ),
                    "Model": settings.openai_model if settings.openai_api_key else "—",
                },
                {
                    "Provider": "Anthropic",
                    "Status": (
                        "設定済み"
                        if settings.claude_api_key and settings.anthropic_model.strip()
                        else "未設定"
                    ),
                    "Model": settings.anthropic_model if settings.claude_api_key else "—",
                },
                {
                    "Provider": "Gemini",
                    "Status": (
                        "設定済み"
                        if settings.effective_gemini_api_key and settings.gemini_model.strip()
                        else "未設定"
                    ),
                    "Model": (settings.gemini_model if settings.effective_gemini_api_key else "—"),
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("LLMの表示はSecretとモデル名の存在確認です。実疎通は調査実行時に検証されます。")
    st.caption(
        "TikTok・Instagram・Pinterestは承認済み手動インポートを使用します。"
        "Webは設定済みRSS/Atomだけを読み込み、任意サイトのスクレイピングは行いません。"
    )

with st.form("market_intelligence_request", border=True):
    st.subheader("Research Request", anchor=False)
    market = st.text_input("市場・カテゴリ", placeholder="例: 日本の家庭用コーヒー器具")
    purpose = st.text_area(
        "調査目的", value="市場機会、未充足ニーズ、競合リスクを把握する", height=80
    )
    location_columns = st.columns(3)
    country = location_columns[0].text_input("国", value="Japan")
    region = location_columns[1].text_input("地域", value="")
    language = location_columns[2].text_input("言語コード", value="ja")
    period_columns = st.columns(2)
    date_from = period_columns[0].date_input("開始日", value=date.today() - timedelta(days=30))
    date_to = period_columns[1].date_input("終了日", value=date.today())
    competitors = st.text_input("競合（カンマ区切り）")
    seed_keywords = st.text_input("初期キーワード（カンマ区切り）")
    excluded_keywords = st.text_input("除外キーワード（カンマ区切り）")
    target_demographic = st.text_input("対象層", placeholder="例: 20〜40代、在宅勤務者")
    option_columns = st.columns(3)
    depth_value = option_columns[0].selectbox(
        "調査深度",
        [depth.value for depth in ResearchDepth],
        index=1,
        format_func=lambda value: {"quick": "Quick", "standard": "Standard", "deep": "Deep"}[value],
    )
    max_items = option_columns[1].number_input(
        "Source別最大件数", min_value=1, max_value=10_000, value=100, step=10
    )
    mock_mode = option_columns[2].toggle(
        "Mock Mode", value=False, help="7 Sourceの検証用合成データを使用します。"
    )
    selected_values = st.multiselect(
        "対象Source",
        [source.value for source in ALL_SOURCES],
        default=[source.value for source in ALL_SOURCES],
        format_func=lambda value: SOURCE_LABELS[SourceName(value)],
    )
    notes = st.text_area("補足・制約", height=80)
    if mock_mode:
        st.error(
            "MOCK DATA — 実SNSのデータではありません。画面・処理・Evidence追跡の確認専用です。",
            icon=":material/science:",
        )
    depth = ResearchDepth(depth_value)
    live_provider_count = len(settings.ai_council_provider_models)
    expected_calls = 0 if mock_mode else _estimate_calls(depth, live_provider_count)
    st.caption(
        f"推定LLM呼び出し: {expected_calls}回。"
        "外部APIは設定済みProviderのみ使用し、Mock Modeでは呼び出しません。"
    )
    submitted = st.form_submit_button(
        "調査を開始", type="primary", icon=":material/manage_search:", width="stretch"
    )

if submitted:
    if not market.strip():
        st.error("市場・カテゴリを入力してください。")
    elif not selected_values:
        st.error("対象Sourceを1つ以上選択してください。")
    else:
        request = ResearchRequest(
            market=market,
            purpose=purpose,
            country=country,
            region=region,
            language=language,
            date_from=date_from,
            date_to=date_to,
            competitors=_split_values(competitors),
            seed_keywords=_split_values(seed_keywords),
            excluded_keywords=_split_values(excluded_keywords),
            target_demographic=target_demographic,
            research_depth=depth,
            max_items_per_source=int(max_items),
            preferred_sources=[SourceName(value) for value in selected_values],
            notes=notes,
            mock_mode=mock_mode,
        )
        progress = st.progress(0.0, text="調査を準備中")
        execution_status = st.status("調査を実行中", expanded=True)

        def update_progress(status: ResearchStatus, message: str, fraction: float) -> None:
            progress.progress(fraction, text=message)
            execution_status.write(f"{status.value}: {message}")

        try:
            with session_scope() as session:
                result = MarketIntelligenceEngine(settings).run(
                    session, request, on_progress=update_progress
                )
            st.session_state.market_intelligence_run_id = result.run_id
            if result.warnings:
                for warning in result.warnings:
                    execution_status.warning(warning)
            execution_status.update(label="調査完了", state="complete", expanded=False)
            st.success(f"Research Run {result.run_id} を保存しました。")
        except Exception as exc:
            execution_status.update(label="調査失敗", state="error", expanded=True)
            st.error(f"調査を完了できませんでした: {type(exc).__name__}: {exc}")

with session_scope() as session:
    run_rows = list_runs(session)

if not run_rows:
    st.info("保存済みのResearch Runはありません。Mock Modeで全工程を確認できます。")
    st.stop()

run_ids = [row.id for row in run_rows]
default_run_id = st.session_state.get("market_intelligence_run_id", run_ids[0])
default_index = run_ids.index(default_run_id) if default_run_id in run_ids else 0
selected_run_id = st.selectbox(
    "保存済みResearch Run",
    run_ids,
    index=default_index,
    format_func=lambda run_id: next(
        f"{row.market} · {row.status} · {row.created_at:%Y-%m-%d %H:%M} · {run_id[:12]}"
        for row in run_rows
        if row.id == run_id
    ),
)
st.session_state.market_intelligence_run_id = selected_run_id

with session_scope() as session:
    report = get_report(session, selected_run_id)
    evidence_records = get_evidence(session, selected_run_id)
    source_items = get_items(session, selected_run_id)

if report is None:
    st.warning("このRunにはまだレポートがありません。実行状態とエラー情報をDBで確認してください。")
    st.stop()

if report.is_mock:
    st.error(
        "MOCK DATA — 以下は合成データのレポートです。市場判断には利用しないでください。",
        icon=":material/science:",
    )

st.subheader(report.title, anchor=False)
with st.container(horizontal=True):
    st.metric("正規化標本", f"{report.sample_size:,}", border=True)
    st.metric("Key Findings", len(report.key_findings), border=True)
    st.metric("Evidence", len(report.evidence_ids), border=True)
    coverage_available = sum(
        status is AvailabilityStatus.AVAILABLE for status in report.source_coverage.values()
    )
    st.metric("Source Coverage", f"{coverage_available}/{len(report.source_coverage)}", border=True)

overview_tab, claims_tab, evidence_tab, raw_tab, export_tab = st.tabs(
    ["概要", "Claim", "Evidence Viewer", "Raw Data Overview", "出力"]
)
with overview_tab:
    st.markdown(report.executive_summary)
    coverage_frame = pd.DataFrame(
        [
            {"Source": SOURCE_LABELS[source], "Status": STATUS_LABELS[status]}
            for source, status in report.source_coverage.items()
        ]
    )
    st.dataframe(coverage_frame, hide_index=True, width="stretch")
    chart_columns = st.columns(2)
    platform_frame = pd.DataFrame(
        [
            {"Source": SOURCE_LABELS[source], "Items": count}
            for source, count in report.quantitative.platform_counts.items()
        ]
    )
    chart_columns[0].bar_chart(platform_frame, x="Source", y="Items")
    daily_frame = pd.DataFrame(
        [
            {"Date": key, "Mentions": value}
            for key, value in report.quantitative.daily_mentions.items()
        ]
    )
    if not daily_frame.empty:
        chart_columns[1].line_chart(daily_frame, x="Date", y="Mentions")
    with st.expander("Data Limitations", expanded=True):
        for limitation in report.data_limitations:
            st.write(f"- {limitation}")

with claims_tab:
    _show_claims(report.key_findings)
    st.subheader("Counter Evidence / 不十分なClaim", anchor=False)
    if report.counter_evidence:
        for value in report.counter_evidence:
            st.write(f"- {value}")
    else:
        st.write("該当なし")

with evidence_tab:
    if not evidence_records:
        st.info("Evidenceはありません。")
    else:
        evidence_by_id = {record.evidence_id: record for record in evidence_records}
        selected_evidence_id = st.selectbox("Evidence ID", list(evidence_by_id))
        selected_evidence = evidence_by_id[selected_evidence_id]
        st.write(selected_evidence.claim)
        evidence_columns = st.columns(4)
        evidence_columns[0].metric("Sample", selected_evidence.sample_size)
        evidence_columns[1].metric("Support", f"{selected_evidence.support_score:.2f}")
        evidence_columns[2].metric("Platform", str(selected_evidence.platform))
        evidence_columns[3].metric(
            "Period", f"{selected_evidence.date_from} — {selected_evidence.date_to}"
        )
        st.caption(f"Query: {selected_evidence.query}")
        item_by_id = {item.id: item for item in source_items}
        linked = [
            item_by_id[item_id]
            for item_id in selected_evidence.source_item_ids
            if item_id in item_by_id
        ]
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Platform": SOURCE_LABELS[item.platform],
                        "Created": item.created_at,
                        "Title": item.title,
                        "Text": item.text[:500],
                        "Likes": item.likes,
                        "Comments": item.comments,
                        "Shares": item.shares,
                        "Views": item.views,
                        "Quality": item.quality_score,
                        "URL": item.source_url,
                    }
                    for item in linked
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"URL": st.column_config.LinkColumn("URL")},
        )

with raw_tab:
    duplicate_count = sum(item.duplicate_group is not None for item in source_items)
    with st.container(horizontal=True):
        st.metric("Rows", len(source_items), border=True)
        st.metric("Duplicate Candidates", duplicate_count, border=True)
        st.metric("Unknown Likes", sum(item.likes is None for item in source_items), border=True)
        st.metric("Mock Rows", sum(item.is_mock for item in source_items), border=True)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ID": item.id,
                    "Platform": SOURCE_LABELS[item.platform],
                    "Created": item.created_at,
                    "Text": item.text[:300],
                    "Likes": item.likes,
                    "Comments": item.comments,
                    "Relevance": item.relevance_score,
                    "Quality": item.quality_score,
                    "Spam": item.spam_score,
                    "Bot": item.bot_score,
                    "Duplicate Group": item.duplicate_group,
                    "Mode": item.data_mode.value,
                }
                for item in source_items
            ]
        ),
        hide_index=True,
        width="stretch",
    )

with export_tab:
    st.download_button(
        "JSONを保存",
        data=report_as_json(report),
        file_name=f"{selected_run_id}.json",
        mime="application/json",
        icon=":material/download:",
    )
    st.download_button(
        "Markdownを保存",
        data=report.markdown,
        file_name=f"{selected_run_id}.md",
        mime="text/markdown",
        icon=":material/download:",
    )
    st.download_button(
        "HTMLを保存（ブラウザ印刷でPDF化可能）",
        data=report_as_html(report),
        file_name=f"{selected_run_id}.html",
        mime="text/html",
        icon=":material/download:",
    )
    st.code(report.markdown, language="markdown", wrap_lines=True, height=500)
