from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import streamlit as st

from app.ai_council.launcher import launch_council_worker
from app.ai_council.repositories import (
    enqueue_council_job,
    fail_council_job,
    list_council_jobs,
    set_council_worker_pid,
)
from app.ai_council.schemas import (
    CouncilBriefData,
    CouncilJobRecord,
    CouncilJobSpec,
    CouncilJobStatus,
)
from app.config import get_settings
from app.database import session_scope
from app.services.ai_council import (
    REQUIRED_COUNCIL_PROVIDERS,
    IndependentCouncilResult,
    WorkflowResult,
)

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
}
STATUS_LABELS = {
    CouncilJobStatus.QUEUED: "開始待ち",
    CouncilJobStatus.RUNNING: "実行中",
    CouncilJobStatus.COMPLETED: "完了",
    CouncilJobStatus.FAILED: "失敗",
}
STATUS_COLORS: dict[
    CouncilJobStatus,
    Literal["red", "orange", "yellow", "blue", "green", "violet", "gray", "grey", "primary"],
] = {
    CouncilJobStatus.QUEUED: "blue",
    CouncilJobStatus.RUNNING: "orange",
    CouncilJobStatus.COMPLETED: "green",
    CouncilJobStatus.FAILED: "red",
}


def _provider_name(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"council-{timestamp}-{uuid4().hex[:10]}"


def _show_independent_result(result: IndependentCouncilResult) -> None:
    st.subheader("最終結果")
    with st.container(border=True):
        if result.final_contribution is not None:
            st.caption(
                f"{result.final_contribution.agent_name} · "
                f"{_provider_name(result.final_contribution.provider)} / "
                f"{result.final_contribution.model}"
            )
        st.markdown(result.final_answer)

    if result.warnings:
        st.warning("注意事項\n\n" + "\n".join(result.warnings))

    st.subheader("3社の独立調査・企画・提案")
    proposal_tabs = st.tabs(
        [_provider_name(item.participant.provider) for item in result.independent_proposals]
    )
    for tab, item in zip(proposal_tabs, result.independent_proposals, strict=True):
        with tab:
            st.caption(f"{item.participant.name} · {item.participant.model}")
            for label, contribution in (
                ("市場調査", item.research),
                ("企画", item.planning),
                ("提案", item.proposal),
            ):
                with st.expander(label, expanded=label == "提案"):
                    st.markdown(contribution.content)

    st.subheader("3社による議論")
    for debate_round in result.debate:
        with st.expander(f"議論ラウンド {debate_round.round_number}"):
            for contribution in debate_round.contributions:
                st.markdown(f"**{contribution.agent_name}**")
                st.caption(f"{_provider_name(contribution.provider)} / {contribution.model}")
                if contribution.error:
                    st.error(contribution.error)
                else:
                    st.markdown(contribution.content)
                st.divider()

    st.caption(
        f"実行ID: {result.run_id} · "
        f"所要時間: {(result.completed_at - result.started_at).total_seconds():.1f}秒"
    )
    st.download_button(
        "全結果をJSONで保存",
        data=result.to_json(),
        file_name=f"{result.run_id}.json",
        mime="application/json",
        icon=":material/download:",
    )


def _show_legacy_result(result: WorkflowResult) -> None:
    st.subheader("統合結論")
    with st.container(border=True):
        st.markdown(result.final_answer)

    if result.warnings:
        st.warning("一部のAI応答に失敗しました。\n\n" + "\n".join(result.warnings))

    stage_tabs = st.tabs([stage.stage.label for stage in result.stages])
    for tab, stage_result in zip(stage_tabs, result.stages, strict=True):
        contribution = stage_result.contribution
        with tab:
            st.caption(
                f"{contribution.agent_name} · "
                f"{_provider_name(contribution.provider)} / {contribution.model}"
            )
            st.markdown(contribution.content)

    st.subheader("AI同士の議論")
    for debate_round in result.debate:
        with st.expander(f"議論ラウンド {debate_round.round_number}"):
            for contribution in debate_round.contributions:
                st.markdown(f"**{contribution.agent_name}**")
                st.caption(f"{_provider_name(contribution.provider)} / {contribution.model}")
                if contribution.error:
                    st.error(contribution.error)
                else:
                    st.markdown(contribution.content)
                st.divider()

    with st.container(border=True):
        st.markdown("**実行状態**")
        st.write(result.execution_receipt)
        st.caption(
            f"実行ID: {result.run_id} · "
            f"所要時間: {(result.completed_at - result.started_at).total_seconds():.1f}秒"
        )

    st.download_button(
        "全結果をJSONで保存",
        data=result.to_json(),
        file_name=f"{result.run_id}.json",
        mime="application/json",
        icon=":material/download:",
    )


def _job_label(job: CouncilJobRecord) -> str:
    objective = job.spec.brief.objective.strip().replace("\n", " ")
    if len(objective) > 45:
        objective = objective[:45] + "…"
    created = job.created_at.astimezone().strftime("%m/%d %H:%M")
    return f"{created} · {STATUS_LABELS[job.status]} · {objective}"


def _show_saved_contribution(contribution: object) -> None:
    if not isinstance(contribution, dict):
        return
    provider = str(contribution.get("provider", ""))
    model = str(contribution.get("model", ""))
    st.caption(f"{_provider_name(provider)} / {model}")
    error = str(contribution.get("error", ""))
    if error:
        st.error(error)
    content = str(contribution.get("content", ""))
    if content:
        st.markdown(content)


def _show_saved_outputs(job: CouncilJobRecord) -> None:
    saved_outputs = [event.output for event in job.events if event.output]
    if not saved_outputs:
        return

    st.subheader("保存済みの途中成果")
    for output in saved_outputs:
        output_type = output.get("output_type")
        if output_type == "independent_stage":
            participant = output.get("participant", {})
            provider = str(participant.get("provider", "")) if isinstance(participant, dict) else ""
            label = str(output.get("stage_label", output.get("stage", "工程")))
            status = str(output.get("status", "completed"))
            title = f"{_provider_name(provider)} · {label}"
            with st.expander(title, expanded=status == "completed"):
                _show_saved_contribution(output.get("contribution"))
        elif output_type == "stage":
            label = str(output.get("stage_label", output.get("stage", "工程")))
            with st.expander(f"{label}成果物", expanded=True):
                _show_saved_contribution(output.get("contribution"))
        elif output_type == "debate":
            round_number = output.get("round", "")
            with st.expander(f"議論ラウンド {round_number}"):
                contributions = output.get("contributions", [])
                if isinstance(contributions, list):
                    for contribution in contributions:
                        _show_saved_contribution(contribution)
        elif output_type == "final":
            with st.expander("最終統合結果", expanded=True):
                _show_saved_contribution(output.get("contribution"))


def _show_job(job: CouncilJobRecord) -> None:
    st.badge(
        STATUS_LABELS[job.status],
        color=STATUS_COLORS[job.status],
        icon=(
            ":material/check_circle:"
            if job.status is CouncilJobStatus.COMPLETED
            else ":material/progress_activity:"
        ),
    )
    st.caption(f"実行ID: {job.run_id}")

    if job.status in {CouncilJobStatus.QUEUED, CouncilJobStatus.RUNNING}:
        st.info(
            "会議はバックグラウンドで稼働しています。"
            "この画面やブラウザを閉じても処理は継続します。",
            icon=":material/cloud_sync:",
        )
    if job.events:
        with st.expander("進捗", expanded=job.status is CouncilJobStatus.RUNNING):
            for event in job.events:
                timestamp = event.created_at.astimezone().strftime("%H:%M:%S")
                st.write(f"{timestamp}　{event.message}")

    if job.status is CouncilJobStatus.FAILED:
        st.error(job.error_message or "AI会議を完了できませんでした。")
        _show_saved_outputs(job)
    elif job.status is CouncilJobStatus.COMPLETED and job.result is not None:
        try:
            if job.result.get("workflow_version") == "independent_v2" or (
                "independent_proposals" in job.result
            ):
                _show_independent_result(IndependentCouncilResult.from_dict(job.result))
            else:
                _show_legacy_result(WorkflowResult.from_dict(job.result))
        except (KeyError, TypeError, ValueError) as exc:
            st.error(f"保存済み結果を表示できませんでした: {exc}")
            _show_saved_outputs(job)
    elif job.status is CouncilJobStatus.RUNNING:
        _show_saved_outputs(job)


@st.fragment(run_every="2s")
def _run_history() -> None:
    with session_scope() as session:
        jobs = list_council_jobs(session, limit=20)
    if not jobs:
        st.info("AI会議の実行履歴はまだありません。", icon=":material/history:")
        return

    job_by_id = {job.run_id: job for job in jobs}
    selected = st.session_state.get("ai_council_selected_run_id")
    if selected not in job_by_id:
        st.session_state["ai_council_selected_run_id"] = jobs[0].run_id
    selected_run_id = st.selectbox(
        "会議履歴",
        list(job_by_id),
        key="ai_council_selected_run_id",
        format_func=lambda run_id: _job_label(job_by_id[run_id]),
    )
    _show_job(job_by_id[selected_run_id])


settings = get_settings()
provider_models = settings.ai_council_provider_models
required_providers = list(REQUIRED_COUNCIL_PROVIDERS)
missing_providers = [provider for provider in required_providers if provider not in provider_models]

st.caption(
    "OpenAI・Anthropic・Geminiが互いの回答を見ずに、それぞれ市場調査・企画・提案まで行います。"
    "3案が揃ってから3社全員で議論し、一つの最終結果へ統合します。"
)

with st.container(border=True):
    st.markdown("**接続状態とモデル**")
    badges = st.container(horizontal=True)
    for provider in ("openai", "anthropic", "gemini"):
        if provider in provider_models:
            badges.badge(
                f"{_provider_name(provider)} · {provider_models[provider]}",
                icon=":material/cloud_done:",
                color="green",
            )
        else:
            badges.badge(
                f"{_provider_name(provider)} 未設定",
                icon=":material/key_off:",
                color="gray",
            )
    st.caption(
        "OpenAIはmax＋pro、Anthropicはmax effort、GeminiはHIGH thinkingで実行します。"
        "市場調査では3社すべてのライブWeb検索を必須にします。"
        "APIキーは画面・DB・ログへ保存しません。"
    )

if not missing_providers:
    with st.form("ai_council_brief"):
        objective = st.text_area(
            "達成したい目的",
            placeholder="例: 楽天アフィリエイト記事制作を半自動化し、品質確認まで一貫して行いたい",
            height=100,
        )
        background = st.text_area(
            "背景・手元の情報",
            placeholder="対象者、現在の課題、参考データ、既に決まっていること",
            height=100,
        )
        constraints = st.text_area(
            "制約・守ること",
            placeholder="予算、期限、法令、禁止事項、人間の承認が必要な操作など",
            height=90,
        )
        expected_output = st.text_input(
            "期待する最終出力",
            value="推奨案、採用理由、リスク、具体的な次のアクション",
        )

        with st.expander("3社AI会議の設定"):
            st.markdown(
                "**固定フロー:** 3社が各自で 市場調査 → 企画 → 提案 / 3社全員で議論 → 最終統合"
            )
            integrator_provider = st.selectbox(
                "統合責任者",
                required_providers,
                index=0,
                format_func=_provider_name,
                key="ai_council_integrator",
            )
            debate_rounds = st.slider("議論ラウンド数", min_value=1, max_value=3, value=2)
            estimated_calls = 9 + (3 * debate_rounds) + 1
            st.caption(
                f"AI API呼び出しは {estimated_calls} 回です。"
                "ライブ検索は各社API側で別途課金される場合があります。"
            )

        st.warning(
            "最高品質のモデルと最大推論設定を使用するため、通常より時間とAPI利用料がかかります。",
            icon=":material/payments:",
        )
        submitted = st.form_submit_button(
            "バックグラウンドでAI会議を開始",
            icon=":material/groups:",
            type="primary",
        )

    if submitted:
        try:
            if not objective.strip():
                raise ValueError("目的を入力してください。")
            run_id = _new_run_id()
            spec = CouncilJobSpec(
                run_id=run_id,
                workflow_version="independent_v2",
                brief=CouncilBriefData(
                    objective=objective.strip(),
                    background=background.strip(),
                    constraints=constraints.strip(),
                    expected_output=expected_output.strip(),
                ),
                participant_providers=tuple(required_providers),
                integrator_provider=integrator_provider,
                provider_models=provider_models,
                debate_rounds=debate_rounds,
                max_output_tokens=settings.ai_council_max_output_tokens,
                enable_openai_web_search=True,
            )
            with session_scope() as session:
                enqueue_council_job(session, spec)
            try:
                launched = launch_council_worker(run_id)
                with session_scope() as session:
                    set_council_worker_pid(session, run_id, launched.pid)
            except OSError as exc:
                with session_scope() as session:
                    fail_council_job(
                        session,
                        run_id,
                        f"バックグラウンド処理を開始できません: {exc}",
                    )
                raise
            st.session_state["ai_council_selected_run_id"] = run_id
            st.toast("AI会議をバックグラウンドで開始しました。", icon=":material/cloud_sync:")
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(str(exc))
else:
    missing_names = "、".join(_provider_name(provider) for provider in missing_providers)
    st.error(
        f"3社AI会議にはOpenAI・Anthropic・GeminiのAPIキーがすべて必要です。"
        f"未設定: {missing_names}。設定ページで接続してください。",
        icon=":material/key_off:",
    )

st.subheader("実行履歴")
_run_history()
