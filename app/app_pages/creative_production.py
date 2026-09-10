from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.config import get_settings
from app.creative_production.engine import CreativeProductionEngine
from app.creative_production.exporter import (
    package_as_json,
    package_as_markdown,
    package_as_zip,
)
from app.creative_production.performance import PerformanceAgent
from app.creative_production.provider_factory import (
    create_image_registry,
    create_text_registry,
    create_video_registry,
)
from app.creative_production.repositories import (
    asset_lineage,
    get_package,
    list_packages,
    list_performance,
    record_approval,
    save_performance,
)
from app.creative_production.schemas import (
    ApprovalDecision,
    BrandVisualProfile,
    BrandVoiceProfile,
    ContentPerformance,
    CreativePlatform,
    CreativeProductionRequest,
    CreativeQualityLevel,
    CreativeStatus,
)
from app.database import session_scope
from app.market_intelligence.repositories import get_report, list_runs

PLATFORM_LABELS = {
    CreativePlatform.ARTICLE: "Article",
    CreativePlatform.NOTE: "note",
    CreativePlatform.BLOG: "Blog",
    CreativePlatform.SEO_ARTICLE: "SEO Article",
    CreativePlatform.X: "X",
    CreativePlatform.INSTAGRAM_FEED: "Instagram Feed",
    CreativePlatform.INSTAGRAM_CAROUSEL: "Instagram Carousel",
    CreativePlatform.INSTAGRAM_REEL: "Instagram Reel",
    CreativePlatform.INSTAGRAM_STORY: "Instagram Story",
    CreativePlatform.TIKTOK: "TikTok",
    CreativePlatform.YOUTUBE_LONG: "YouTube Long",
    CreativePlatform.YOUTUBE_SHORTS: "YouTube Shorts",
    CreativePlatform.PINTEREST: "Pinterest",
    CreativePlatform.IMAGE: "Image only",
    CreativePlatform.VIDEO: "Video storyboard",
}


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


@st.cache_data(ttl=60, show_spinner=False)
def capability_tables() -> dict[str, list[dict[str, Any]]]:
    settings = get_settings()
    text = create_text_registry(settings, allow_external=True).capabilities()
    image = create_image_registry(settings, allow_external=False).capabilities()
    video = create_video_registry(settings, allow_external=False).capabilities()
    return {
        "Text": [
            {
                "Provider": key,
                "Status": value.status.value,
                "Model": value.model,
                "External": value.external,
                "Operations": ", ".join(value.operations),
                "Message": value.message,
            }
            for key, value in text.items()
        ],
        "Image": [
            {
                "Provider": key,
                "Status": value.status.value,
                "Model": value.model,
                "External": value.external,
                "Operations": ", ".join(value.operations),
                "Message": value.message,
            }
            for key, value in image.items()
        ],
        "Video": [
            {
                "Provider": key,
                "Status": value.status.value,
                "Model": value.model,
                "External": value.external,
                "Operations": ", ".join(value.operations),
                "Message": value.message,
            }
            for key, value in video.items()
        ],
    }


settings = get_settings()
st.caption(
    "Market IntelligenceのEvidenceから、Campaign、媒体別独立案、複数審査、修正、"
    "画像レイアウト、Shot-based Storyboard、Human Approvalまで追跡します。"
)
st.warning(
    "外部APIは初期状態では呼びません。APIキー・権限・課金設定は最後にまとめて行えます。",
    icon=":material/key_off:",
)

with st.expander("Creative Provider Capability", expanded=True, icon=":material/hub:"):
    for category, rows in capability_tables().items():
        st.markdown(f"**{category}**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Local Providerは工程確認用です。Local画像とStoryboardはPlaceholderとして保存され、"
        "実Assetへ差し替えるまで公開承認できません。"
    )

with session_scope() as session:
    research_rows = list_runs(session, limit=50)
    research_options = [row for row in research_rows if get_report(session, row.id) is not None]

if not research_options:
    st.info("先に「SNS市場調査」でMarket Intelligence Reportを1件生成してください。")
else:
    with st.form("creative_production_request", border=True):
        st.subheader("Production request", anchor=False)
        research_run_id = st.selectbox(
            "Market Intelligence Run",
            [row.id for row in research_options],
            format_func=lambda run_id: next(
                f"{row.market} · {row.status} · {row.created_at:%Y-%m-%d %H:%M}"
                for row in research_options
                if row.id == run_id
            ),
        )
        platforms = st.multiselect(
            "制作Platform",
            [platform.value for platform in CreativePlatform],
            default=[
                CreativePlatform.NOTE.value,
                CreativePlatform.X.value,
                CreativePlatform.INSTAGRAM_CAROUSEL.value,
                CreativePlatform.TIKTOK.value,
                CreativePlatform.YOUTUBE_SHORTS.value,
                CreativePlatform.PINTEREST.value,
            ],
            format_func=lambda value: PLATFORM_LABELS[CreativePlatform(value)],
        )
        controls = st.columns(3)
        quality_value = controls[0].selectbox(
            "Quality level",
            [level.value for level in CreativeQualityLevel],
            index=1,
            format_func=str.title,
        )
        include_images = controls[1].toggle("画像候補とLayout", value=True)
        include_video = controls[2].toggle("動画Storyboard", value=True)
        target = st.text_input("Target audience override（任意）")
        objective = st.text_input("Objective override（任意）")
        cta = st.text_input("CTA override（任意）")

        st.markdown("**Brand voice**")
        brand_columns = st.columns(3)
        voice_name = brand_columns[0].text_input("Profile name", value="Default voice")
        voice_tone = brand_columns[1].text_input("Tone", value="明快, 誠実, 具体的")
        forbidden_phrases = brand_columns[2].text_input(
            "Forbidden phrases", value="絶対, 必ず成功, 誰でも稼げる"
        )
        st.markdown("**Brand visual**")
        visual_columns = st.columns(3)
        visual_name = visual_columns[0].text_input("Visual profile", value="Default visual")
        colors = visual_columns[1].text_input("Colors (#RRGGBB)", value="#17324D, #F3B61F, #F7F9FB")
        image_style = visual_columns[2].text_input(
            "Image style", value="editorial, clean, credible"
        )

        external_allowed = st.toggle(
            "設定済み外部生成APIの実行を許可",
            value=False,
            disabled=not settings.creative_external_api_enabled,
            help="有効にするとAPI利用料が発生する可能性があります。初期状態では無効です。",
        )
        if not settings.creative_external_api_enabled:
            st.caption(
                "外部APIは環境設定でロック中です。最終確認後に"
                "CREATIVE_EXTERNAL_API_ENABLED=trueへ変更するとToggleを利用できます。"
            )
        budget = st.number_input(
            "Estimated API budget上限（USD、0は未設定）",
            min_value=0.0,
            value=0.0,
            step=1.0,
        )
        if external_allowed:
            st.error(
                "外部API実行が有効です。設定済みProviderで呼び出しと課金が発生する可能性があります。",
                icon=":material/paid:",
            )
        submitted = st.form_submit_button(
            "制作を開始",
            type="primary",
            icon=":material/movie_edit:",
            width="stretch",
        )

    if submitted:
        if not platforms:
            st.error("制作Platformを1つ以上選択してください。")
        else:
            progress = st.progress(0.0, text="Productionを準備中")
            run_status = st.status("Creative Productionを実行中", expanded=True)

            def update_progress(status: CreativeStatus, message: str, fraction: float) -> None:
                progress.progress(fraction, text=message)
                run_status.write(f"{status.value}: {message}")

            try:
                voice = BrandVoiceProfile(
                    name=voice_name,
                    tone=_split(voice_tone),
                    forbidden_phrases=_split(forbidden_phrases),
                )
                visual = BrandVisualProfile(
                    name=visual_name,
                    colors=_split(colors),
                    image_style=image_style,
                )
                request = CreativeProductionRequest(
                    research_run_id=research_run_id,
                    platforms=[CreativePlatform(value) for value in platforms],
                    quality_level=CreativeQualityLevel(quality_value),
                    include_images=include_images,
                    include_video_storyboards=include_video,
                    budget_limit=budget or None,
                    allow_external_api=external_allowed,
                    target_audience_override=target,
                    objective_override=objective,
                    cta_override=cta,
                )
                with session_scope() as session:
                    generated_package = CreativeProductionEngine(settings).run(
                        session,
                        request,
                        brand_voice=voice,
                        brand_visual=visual,
                        on_progress=update_progress,
                    )
                st.session_state.creative_package_id = generated_package.package_id
                run_status.update(
                    label="Content Package生成完了",
                    state="complete",
                    expanded=False,
                )
                st.success(
                    f"{generated_package.package_id} を保存しました。Human Approval待ちです。"
                )
            except Exception as exc:
                run_status.update(label="制作失敗", state="error", expanded=True)
                st.error(f"制作を完了できませんでした: {type(exc).__name__}: {exc}")

with session_scope() as session:
    package_rows = list_packages(session)

if not package_rows:
    st.info("保存済みContent Packageはありません。")
    st.stop()

package_ids = [row.id for row in package_rows]
current_id = st.session_state.get("creative_package_id", package_ids[0])
package_index = package_ids.index(current_id) if current_id in package_ids else 0
selected_package_id = st.selectbox(
    "Past Production",
    package_ids,
    index=package_index,
    format_func=lambda package_id: next(
        f"{row.status} · v{row.version} · {row.created_at:%Y-%m-%d %H:%M} · {package_id[:14]}"
        for row in package_rows
        if row.id == package_id
    ),
)
st.session_state.creative_package_id = selected_package_id
with session_scope() as session:
    package = get_package(session, selected_package_id)
    performance_rows = list_performance(session, selected_package_id)

if package is None:
    st.error("Content Packageを読み込めませんでした。")
    st.stop()

if package.degraded_quality_mode:
    st.error(
        "DEGRADED QUALITY MODE — 外部Provider数または実Assetの品質条件を満たしていません。",
        icon=":material/warning:",
    )
    with st.expander("Degradation reasons", expanded=True):
        for reason in package.degradation_reasons:
            st.write(f"- {reason}")

known_cost = sum(cost.estimated_cost or 0 for cost in package.costs)
with st.container(horizontal=True):
    st.metric("Final contents", len(package.final_content), border=True)
    st.metric("Candidates", len(package.candidates), border=True)
    st.metric("Jury scores", len(package.scores), border=True)
    st.metric("Assets", len(package.assets), border=True)
    st.metric("Known estimated cost", f"${known_cost:.4f}", border=True)

(
    brief_tab,
    content_tab,
    jury_tab,
    asset_tab,
    video_tab,
    lineage_tab,
    approval_tab,
    performance_tab,
) = st.tabs(
    [
        "Brief",
        "Final content",
        "Arena / Jury",
        "Images",
        "Storyboard",
        "Evidence / Lineage",
        "Approval / Export",
        "Performance",
    ]
)

with brief_tab:
    st.subheader(package.campaign.name, anchor=False)
    st.write(package.campaign.objective)
    st.json(package.content_brief.model_dump(mode="json"), expanded=False)

with content_tab:
    if not package.final_content:
        st.info("Text final contentはありません。")
    for platform, candidate in package.final_content.items():
        with st.container(border=True):
            st.subheader(PLATFORM_LABELS[platform], anchor=False)
            st.caption(
                f"{candidate.provider} / {candidate.model} / revision {candidate.revision_round}"
            )
            st.code(candidate.content, language=None, wrap_lines=True)
            st.caption(f"Evidence: {', '.join(candidate.evidence_ids) or 'none'}")

with jury_tab:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Candidate": score.candidate_id,
                    "Judge": score.judge_name,
                    "Overall": score.overall,
                    "Blocking": " / ".join(score.blocking_issues),
                }
                for score in package.scores
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Overall": st.column_config.ProgressColumn(
                "Overall", min_value=0, max_value=100, format="%.1f"
            )
        },
    )
    st.subheader("Critic reports", anchor=False)
    for report in package.critic_reports[-10:]:
        with st.expander(f"{report.candidate_id[:16]} · pass={report.pass_threshold}"):
            st.write("Revision: " + " / ".join(report.revision_instructions))
            if report.weaknesses:
                st.write("Weaknesses: " + " / ".join(report.weaknesses))

with asset_tab:
    composite_images = [
        asset
        for asset in package.assets
        if asset.kind.value == "composite_image" and Path(asset.file_path).is_file()
    ]
    if not composite_images:
        st.info("Composite imageはありません。")
    for asset in composite_images:
        with st.container(border=True):
            if asset.is_placeholder:
                st.warning("DRAFT PLACEHOLDER — 実生成画像ではありません。")
            st.image(asset.file_path, caption=f"{asset.asset_id} · {asset.provider}")

with video_tab:
    if not package.storyboards:
        st.info("Storyboardはありません。")
    for storyboard in package.storyboards:
        st.subheader(
            f"{PLATFORM_LABELS[storyboard.platform]} · {storyboard.total_duration:g}s",
            anchor=False,
        )
        st.warning("STORYBOARD PLACEHOLDER — 実動画ファイルではありません。")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Shot": shot.order,
                        "Duration": shot.duration,
                        "Purpose": shot.purpose,
                        "Narration": shot.narration,
                        "Camera": f"{shot.camera_angle} / {shot.camera_motion} / {shot.lens}",
                        "Continuity": shot.continuity_notes,
                    }
                    for shot in storyboard.shots
                ]
            ),
            hide_index=True,
            width="stretch",
        )

with lineage_tab:
    st.markdown("**Research Evidence**")
    st.write(", ".join(package.evidence_ids) or "Evidenceなし")
    if package.assets:
        selected_asset_id = st.selectbox(
            "Asset lineage",
            [asset.asset_id for asset in package.assets],
            key="creative_lineage_asset",
        )
        with session_scope() as session:
            lineage = asset_lineage(session, selected_asset_id)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Step": index,
                        "Asset": asset.asset_id,
                        "Kind": asset.kind.value,
                        "Provider": asset.provider,
                        "Model": asset.model,
                        "Parent": asset.parent_asset_id,
                        "Revision": asset.revision_number,
                        "Placeholder": asset.is_placeholder,
                    }
                    for index, asset in enumerate(lineage)
                ]
            ),
            hide_index=True,
            width="stretch",
        )

with approval_tab:
    st.write(f"Current status: **{package.status.value}**")
    with st.form("creative_approval", border=True):
        decision_value = st.segmented_control(
            "Decision",
            [decision.value for decision in ApprovalDecision],
            default=ApprovalDecision.REQUEST_REVISION.value,
            format_func=lambda value: {
                "approve": "Approve",
                "request_revision": "Request revision",
                "reject": "Reject",
            }[value],
        )
        reviewer = st.text_input("Reviewer")
        feedback = st.text_area("Feedback")
        approval_submitted = st.form_submit_button("Decisionを保存", icon=":material/fact_check:")
    if approval_submitted:
        if not reviewer.strip():
            st.error("Reviewerを入力してください。")
        else:
            try:
                with session_scope() as session:
                    updated = record_approval(
                        session,
                        package.package_id,
                        ApprovalDecision(str(decision_value)),
                        reviewer.strip(),
                        feedback.strip(),
                    )
                st.success(f"Decisionを保存しました: {updated.status.value}")
            except ValueError as exc:
                st.error(str(exc))
    with st.container(horizontal=True):
        st.download_button(
            "JSON",
            package_as_json(package),
            file_name=f"{package.package_id}.json",
            mime="application/json",
            icon=":material/download:",
        )
        st.download_button(
            "Markdown",
            package_as_markdown(package),
            file_name=f"{package.package_id}.md",
            mime="text/markdown",
            icon=":material/download:",
        )
        st.download_button(
            "Content Package ZIP",
            package_as_zip(package),
            file_name=f"{package.package_id}.zip",
            mime="application/zip",
            icon=":material/folder_zip:",
        )

with performance_tab:
    st.caption("公開後に手動入力した実績を、次回のModel Router判断へ利用します。")
    platform_value = (
        st.selectbox(
            "Performance platform",
            [platform.value for platform in package.final_content],
            format_func=lambda value: PLATFORM_LABELS[CreativePlatform(value)],
            disabled=not package.final_content,
        )
        if package.final_content
        else None
    )
    with st.form("creative_performance", border=True):
        performance_columns = st.columns(4)
        impressions = performance_columns[0].number_input("Impressions", min_value=0, value=0)
        views = performance_columns[1].number_input("Views", min_value=0, value=0)
        clicks = performance_columns[2].number_input("Clicks", min_value=0, value=0)
        conversions = performance_columns[3].number_input("Conversions", min_value=0, value=0)
        engagement_columns = st.columns(4)
        likes = engagement_columns[0].number_input("Likes", min_value=0, value=0)
        comments = engagement_columns[1].number_input("Comments", min_value=0, value=0)
        shares = engagement_columns[2].number_input("Shares", min_value=0, value=0)
        saves = engagement_columns[3].number_input("Saves", min_value=0, value=0)
        save_metrics = st.form_submit_button("Performanceを保存", icon=":material/save:")
    if save_metrics and platform_value:
        platform = CreativePlatform(platform_value)
        candidate = package.final_content[platform]
        with session_scope() as session:
            save_performance(
                session,
                ContentPerformance(
                    package_id=package.package_id,
                    platform=platform,
                    provider=candidate.provider,
                    model=candidate.model,
                    impressions=int(impressions),
                    views=int(views),
                    clicks=int(clicks),
                    likes=int(likes),
                    comments=int(comments),
                    shares=int(shares),
                    saves=int(saves),
                    conversions=int(conversions),
                    ctr=(clicks / impressions if impressions else None),
                ),
            )
        st.success("Performanceを保存しました。")
    summaries = PerformanceAgent().analyze(performance_rows)
    if summaries:
        st.dataframe(pd.DataFrame([summary.__dict__ for summary in summaries]), hide_index=True)
