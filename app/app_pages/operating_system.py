from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from app.config import get_settings
from app.database import session_scope
from app.market_intelligence.repositories import get_report
from app.market_intelligence.repositories import list_runs as list_research_runs
from app.operating_system.integrations import (
    decision_evidence_from_research,
    opportunity_dimensions_from_research,
)
from app.operating_system.repositories import (
    get_decision,
    list_model_calls,
    list_runs,
    set_decision_approval,
)
from app.operating_system.schemas import (
    ApprovalStatus,
    DecisionEvidence,
    DecisionOutput,
    DecisionRequest,
    DecisionVerdict,
    OpportunityDimensions,
    ResearchMode,
    RunBudget,
)
from app.operating_system.service import OperatingSystemService

MODE_HELP = {
    ResearchMode.QUICK: "候補探索・分類向け。安価なProvider 1件、短い出力、キャッシュ優先。",
    ResearchMode.STANDARD: "意思決定向け。必要性が高い時だけ独立検証を追加。",
    ResearchMode.DEEP: (
        "重要判断向け。必要時だけBull / Bear / Skeptic / Red Team / Judgeを"
        "3〜6役で実行し、Evidence・前提・不一致を比較。"
    ),
}


@st.cache_resource(show_spinner=False)
def _service() -> OperatingSystemService:
    return OperatingSystemService(get_settings())


def _parse_evidence(value: str) -> list[DecisionEvidence]:
    records: list[DecisionEvidence] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            raise ValueError(f"Evidence {line_number}行目は「source | statement」形式です。")
        quality = float(parts[2]) if len(parts) >= 3 and parts[2] else 0.6
        sample_size = int(parts[3]) if len(parts) >= 4 and parts[3] else 1
        records.append(
            DecisionEvidence(
                source=parts[0],
                statement=parts[1],
                source_quality=quality,
                sample_size=sample_size,
            )
        )
    return records


def _show_decision(output: DecisionOutput) -> None:
    with st.container(border=True):
        if output.decision is DecisionVerdict.GO:
            st.success(f"Decision: {output.decision.value}", icon=":material/check_circle:")
        elif output.decision is DecisionVerdict.NO_GO:
            st.error(f"Decision: {output.decision.value}", icon=":material/block:")
        else:
            st.warning(f"Decision: {output.decision.value}", icon=":material/manage_search:")
        st.write(output.summary)
        with st.container(horizontal=True):
            st.metric("Opportunity", f"{output.opportunity_score:.1f}/100", border=True)
            st.metric("Confidence", f"{output.confidence:.0%}", border=True)
            st.metric("Evidence", f"{output.evidence_score:.0%}", border=True)
            st.metric("AI Cost", f"${output.ai_cost:.4f}", border=True)
            st.metric("API Calls", output.api_calls, border=True)
        st.caption(
            f"Research: requested "
            f"{(output.requested_research_mode or output.research_mode).value} → "
            f"executed {output.research_mode.value} · Agents: "
            f"{', '.join(output.agents_used) or 'rule-based only'}"
        )

    with st.container(horizontal=True):
        st.metric(
            "Expected Value",
            f"${output.estimated_value:,.2f}" if output.estimated_value is not None else "N/A",
            border=True,
        )
        st.metric(
            "Expected Profit",
            f"${output.estimated_profit:,.2f}" if output.estimated_profit is not None else "N/A",
            border=True,
        )
        st.metric(
            "Decision ROI",
            f"{output.decision_roi:.1%}" if output.decision_roi is not None else "実績待ち",
            border=True,
        )

    if output.warnings:
        st.warning("\n\n".join(output.warnings))

    with st.expander("判断理由・リスク・次のアクション", expanded=True):
        columns = st.columns(3)
        with columns[0]:
            st.markdown("**Reason**")
            for item in output.reasons:
                st.write(f"- {item}")
        with columns[1]:
            st.markdown("**Risk / Counter argument**")
            for item in [*output.risks, *output.counter_arguments]:
                st.write(f"- {item}")
        with columns[2]:
            st.markdown("**Recommended action**")
            for item in output.recommended_actions:
                st.write(f"- {item}")

    with st.expander("7段階分析・Evidence・使用モデル"):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Level": level.value, "Analysis": text}
                    for level, text in output.analysis_levels.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.write("**Sources:** " + (", ".join(output.sources) or "未登録"))
        st.write("**Models:** " + (", ".join(output.models_used) or "AI助言なし"))
        if output.evidence:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Source": item.source,
                            "Statement": item.statement,
                            "Quality": item.source_quality,
                            "Sample": item.sample_size,
                        }
                        for item in output.evidence
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    with st.expander("Fact / Inference / Assumption / Prediction", expanded=True):
        detail_columns = st.columns(4)
        for column, label, values in zip(
            detail_columns,
            ("Facts", "Inferences", "Assumptions", "Predictions"),
            (
                output.facts,
                output.inferences,
                output.assumptions,
                [
                    f"{item.metric}: {item.predicted_value} ({item.confidence:.0%})"
                    for item in output.predictions
                ],
            ),
            strict=True,
        ):
            with column:
                st.markdown(f"**{label}**")
                if values:
                    for value in values:
                        st.write(f"- {value}")
                else:
                    st.caption("なし / 未抽出")
        st.markdown("**Disagreement analysis**")
        for item in output.disagreement_analysis:
            st.write(f"- {item}")

    with st.expander("Execution Plan"):
        st.dataframe(
            pd.DataFrame([item.model_dump(mode="json") for item in output.execution_plan]),
            hide_index=True,
            width="stretch",
        )

    with st.container(border=True):
        st.markdown("**Human approval**")
        st.caption(
            "AIのDecisionだけでは外部実行しません。承認状態は後続の制作・配信ゲート用に保存します。"
        )
        st.write(f"現在: {output.approval_status.value}")
        approval_note = st.text_input(
            "承認メモ / 修正内容",
            key=f"approval_note_{output.run_id}",
        )
        approval_columns = st.columns(3)
        if approval_columns[0].button(
            "承認",
            type="primary",
            icon=":material/check:",
            key=f"approve_{output.run_id}",
            disabled=output.approval_status is ApprovalStatus.APPROVED,
            width="stretch",
        ):
            with session_scope() as session:
                updated = set_decision_approval(
                    session, output.run_id, ApprovalStatus.APPROVED, approval_note
                )
            st.session_state["os_decision"] = updated
            st.rerun()
        if approval_columns[1].button(
            "却下",
            icon=":material/close:",
            key=f"reject_{output.run_id}",
            disabled=output.approval_status is ApprovalStatus.REJECTED,
            width="stretch",
        ):
            with session_scope() as session:
                updated = set_decision_approval(
                    session, output.run_id, ApprovalStatus.REJECTED, approval_note
                )
            st.session_state["os_decision"] = updated
            st.rerun()
        if approval_columns[2].button(
            "修正依頼",
            icon=":material/edit_note:",
            key=f"modify_{output.run_id}",
            disabled=output.approval_status is ApprovalStatus.MODIFICATION_REQUESTED,
            width="stretch",
        ):
            with session_scope() as session:
                updated = set_decision_approval(
                    session,
                    output.run_id,
                    ApprovalStatus.MODIFICATION_REQUESTED,
                    approval_note,
                )
            st.session_state["os_decision"] = updated
            st.rerun()

    st.download_button(
        "Decision JSONを保存",
        data=json.dumps(output.export_dict(), ensure_ascii=False, indent=2),
        file_name=f"{output.run_id}-decision.json",
        mime="application/json",
        icon=":material/download:",
    )


settings = get_settings()
service = _service()

st.caption(
    "市場の発見 → 分析 → Decision → 制作 → 承認 → 配信 → 学習を、"
    "run_id・Evidence・AI Costでつなぐ統合レイヤーです。"
)

with st.container(border=True):
    st.markdown("**Phase 1 · Cost Efficient Intelligence**")
    st.write("初期値は外部APIを使わず、1円あたりの意思決定品質を優先します。")
    profile_rows = [
        {
            "Provider": profile.provider,
            "Model": profile.model,
            "Health": "available" if profile.healthy else "unavailable",
            "External": profile.external,
            "Planning reserve / call": f"${profile.planning_cost_usd:.4f}",
            "Quality hint": profile.quality,
        }
        for profile in service.profiles
    ]
    st.dataframe(pd.DataFrame(profile_rows), hide_index=True, width="stretch")
    st.caption("Planning reserveは予算ガード用の概算で、各社の請求額ではありません。")
    st.write(
        f"Global protection: Mock {'ON' if settings.ai_os_mock_mode else 'OFF'} · "
        f"Daily ${settings.ai_os_daily_budget_usd:.2f} · "
        f"Run ${settings.ai_os_max_run_cost_usd:.2f} · "
        f"Calls {settings.ai_os_max_model_calls_per_run}"
    )

with session_scope() as session:
    research_rows = [
        row for row in list_research_runs(session, limit=30) if get_report(session, row.id)
    ]
research_options = {f"{row.market} · {row.id[:18]}": row.id for row in research_rows}
candidate = st.session_state.get("os_discovery_candidate")
candidate_run_id = candidate.get("research_run_id", "") if isinstance(candidate, dict) else ""
labels = ["手動入力", *research_options]
default_index = next(
    (
        index
        for index, label in enumerate(labels)
        if research_options.get(label) == candidate_run_id
    ),
    0,
)
research_source = st.selectbox(
    "Evidence source",
    labels,
    index=default_index,
    help="保存済みSNS市場調査を選ぶと、追加API呼び出しなしでEvidenceとScore入力へ変換します。",
)
selected_research_run_id = research_options.get(research_source, "")
imported_evidence: list[DecisionEvidence] = []
imported_dimensions = OpportunityDimensions()
imported_market = ""
if selected_research_run_id:
    with session_scope() as session:
        imported_evidence = decision_evidence_from_research(session, selected_research_run_id)
        imported_dimensions = opportunity_dimensions_from_research(
            session, selected_research_run_id
        )
        report = get_report(session, selected_research_run_id)
    imported_market = report.scope.get("market", "") if report else ""
    if not imported_market:
        imported_market = next(
            (row.market for row in research_rows if row.id == selected_research_run_id), ""
        )
    st.success(
        f"保存済みEvidence {len(imported_evidence)}件を読み込みました。追加AI Costは$0です。"
    )

mode = st.segmented_control(
    "Research Mode",
    [item.value for item in ResearchMode],
    default=ResearchMode.QUICK.value,
    selection_mode="single",
)
research_mode = ResearchMode(mode or ResearchMode.QUICK.value)
st.info(MODE_HELP[research_mode], icon=":material/tune:")
budget_defaults = RunBudget.for_mode(research_mode)

with st.form("operating_system_request", border=True):
    st.subheader("Decision Context", anchor=False)
    objective = st.text_area(
        "判断したいこと",
        placeholder="例: この市場へ小規模参入し、noteとXで検証を始めるべきか",
        height=90,
    )
    market = st.text_input(
        "市場・商品・テーマ",
        value=imported_market,
        placeholder="例: 在宅勤務者向けコーヒー器具",
    )
    background = st.text_area("背景・制約", height=80)

    context_columns = st.columns(4)
    importance = context_columns[0].slider("重要度", 0.0, 1.0, 0.5, 0.05)
    uncertainty = context_columns[1].slider("不確実性", 0.0, 1.0, 0.5, 0.05)
    downside = context_columns[2].slider("失敗時影響", 0.0, 1.0, 0.5, 0.05)
    expected_value = context_columns[3].number_input(
        "期待価値（任意）", min_value=0.0, value=0.0, step=1000.0
    )
    decision_columns = st.columns(4)
    reversibility = decision_columns[0].slider("可逆性", 0.0, 1.0, 0.5, 0.05)
    urgency = decision_columns[1].slider("緊急度", 0.0, 1.0, 0.5, 0.05)
    expected_profit = decision_columns[2].number_input("期待利益（任意）", value=0.0, step=1000.0)
    auto_escalation = decision_columns[3].toggle("自動深掘り", value=True)

    with st.expander("Opportunity Score入力", expanded=True):
        st.caption("0〜100。競争・参入難易度・Riskは高いほど最終Scoreを下げます。")
        score_columns = st.columns(3)
        demand = score_columns[0].slider("Demand", 0, 100, int(imported_dimensions.demand))
        growth = score_columns[1].slider("Growth", 0, 100, int(imported_dimensions.growth))
        competition = score_columns[2].slider(
            "Competition", 0, 100, int(imported_dimensions.competition)
        )
        monetization = score_columns[0].slider(
            "Monetization", 0, 100, int(imported_dimensions.monetization_potential)
        )
        profit_potential = score_columns[1].slider(
            "Profit potential", 0, 100, int(imported_dimensions.profit_potential)
        )
        differentiation = score_columns[2].slider(
            "Differentiation",
            0,
            100,
            int(imported_dimensions.differentiation_potential),
        )
        content_opportunity = score_columns[0].slider(
            "Content opportunity", 0, 100, int(imported_dimensions.content_opportunity)
        )
        sns_opportunity = score_columns[1].slider(
            "SNS opportunity", 0, 100, int(imported_dimensions.sns_opportunity)
        )
        entry_difficulty = score_columns[2].slider(
            "Entry difficulty", 0, 100, int(imported_dimensions.entry_difficulty)
        )
        risk = score_columns[0].slider("Risk", 0, 100, int(imported_dimensions.risk))
        input_confidence = score_columns[1].slider(
            "Input confidence", 0, 100, int(imported_dimensions.confidence)
        )

    evidence_text = st.text_area(
        "Evidence（1行1件: source | statement | quality 0〜1 | sample size）",
        placeholder=(
            "X API | 直近30日で関連投稿が増加 | 0.65 | 120\n"
            "公式統計 | 対象人口が前年比で増加 | 0.90 | 1000"
        ),
        height=100,
    )

    with st.expander("Budget Guardrail"):
        budget_columns = st.columns(4)
        max_cost = budget_columns[0].number_input(
            "Max cost USD", min_value=0.0, value=budget_defaults.max_cost_usd, step=0.01
        )
        max_calls = budget_columns[1].number_input(
            "Max API calls", min_value=0, value=budget_defaults.max_api_calls, step=1
        )
        max_searches = budget_columns[2].number_input(
            "Max search calls", min_value=0, value=budget_defaults.max_search_calls, step=1
        )
        max_seconds = budget_columns[3].number_input(
            "Max seconds",
            min_value=5,
            value=budget_defaults.max_execution_time_seconds,
            step=5,
        )
        allow_external = st.toggle(
            "このRunで外部AI APIを許可",
            value=False,
            disabled=not settings.ai_os_external_api_enabled,
        )
        if not settings.ai_os_external_api_enabled:
            st.caption(
                "全体ロック中です。最後の実運用テスト時だけ "
                "AI_OS_EXTERNAL_API_ENABLED=true にします。"
            )

    submitted = st.form_submit_button(
        "Decision Runを開始",
        type="primary",
        icon=":material/play_arrow:",
        width="stretch",
    )

if submitted:
    try:
        request = DecisionRequest(
            objective=objective,
            market=market,
            background=background,
            research_mode=research_mode,
            decision_importance=importance,
            uncertainty=uncertainty,
            potential_downside=downside,
            reversibility=reversibility,
            urgency=urgency,
            expected_value=expected_value or None,
            expected_profit=expected_profit if expected_profit != 0 else None,
            budget=RunBudget(
                max_cost_usd=max_cost,
                max_api_calls=int(max_calls),
                max_search_calls=int(max_searches),
                max_execution_time_seconds=int(max_seconds),
            ),
            opportunity=OpportunityDimensions(
                demand=demand,
                growth=growth,
                competition=competition,
                monetization_potential=monetization,
                profit_potential=profit_potential,
                content_opportunity=content_opportunity,
                sns_opportunity=sns_opportunity,
                differentiation_potential=differentiation,
                entry_difficulty=entry_difficulty,
                risk=risk,
                confidence=input_confidence,
            ),
            evidence=[*imported_evidence, *_parse_evidence(evidence_text)],
            allow_external_api=allow_external,
            auto_escalation=auto_escalation,
            research_run_id=selected_research_run_id,
        )
        route = service.preview_route(request)
        status = st.status("Decision Runを開始しています。", expanded=True)
        status.write(" / ".join(route.routing_reason))
        status.write(
            f"Estimated: ${route.estimated_cost_usd:.4f} / API {route.estimated_api_calls}回"
        )
        progress = st.progress(0.0, text="準備中")

        def update_progress(stage: str, message: str, fraction: float) -> None:
            progress.progress(fraction, text=message)
            status.write(f"{stage}: {message}")

        with session_scope() as session:
            result = service.run(session, request, on_progress=update_progress)
        st.session_state["os_decision"] = result
        status.update(label="Decision Run完了", state="complete", expanded=False)
    except (ValueError, RuntimeError) as exc:
        if "status" in locals():
            status.update(label="Decision Run失敗", state="error", expanded=True)
        st.error(str(exc))

saved = st.session_state.get("os_decision")
if isinstance(saved, DecisionOutput):
    _show_decision(saved)
else:
    st.info(
        "最初はQUICK・外部API OFFで、Score・Evidence・予算・承認フローを無料確認できます。",
        icon=":material/science:",
    )

with session_scope() as session:
    run_rows = list_runs(session, limit=30)

if run_rows:
    with st.expander("Run / Model Call Observability"):
        selected_run_id = st.selectbox("Run ID", [row.id for row in run_rows])
        with session_scope() as session:
            historical = get_decision(session, selected_run_id)
            calls = list_model_calls(session, selected_run_id)
        if historical and (
            not isinstance(saved, DecisionOutput) or historical.run_id != saved.run_id
        ):
            st.write(historical.summary)
        if calls:
            call_rows: list[dict[str, Any]] = [
                {
                    "Agent": row.agent,
                    "Provider": row.provider,
                    "Model": row.model,
                    "Input tokens": row.input_tokens,
                    "Output tokens": row.output_tokens,
                    "Cost estimate": row.estimated_cost_usd,
                    "Latency ms": row.latency_ms,
                    "Cached": row.cached,
                    "Fallback": row.fallback,
                    "Success": row.success,
                }
                for row in calls
            ]
            st.dataframe(pd.DataFrame(call_rows), hide_index=True, width="stretch")
        else:
            st.caption("このRunのModel Callはありません。")
