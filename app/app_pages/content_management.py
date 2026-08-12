from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

from app.database import session_scope
from app.repositories import (
    DEFAULT_WEEKLY_PLAN,
    get_content,
    get_setting,
    list_contents,
    save_content_review,
    set_setting,
)
from app.services.compliance import check_content
from app.streamlit_support import show_compliance_report

STATUSES = [
    "idea",
    "drafting",
    "review",
    "approved",
    "scheduled",
    "published",
    "update_required",
    "archived",
]

STATUS_LABELS = {
    "idea": "アイデア",
    "drafting": "下書き中",
    "review": "確認待ち",
    "approved": "承認済み",
    "scheduled": "投稿予定",
    "published": "投稿済み",
    "update_required": "更新が必要",
    "archived": "保管済み",
}

with session_scope() as session:
    contents = list_contents(session)
    weekly_plan = get_setting(session, "weekly_plan", DEFAULT_WEEKLY_PLAN)

if not contents:
    st.info("投稿はまだありません。コンテンツ作成画面で下書きを保存してください。")
else:
    summary = pd.DataFrame(
        [
            {
                "ID": content.id,
                "媒体": content.channel,
                "テーマ": content.theme,
                "タイトル": content.title,
                "ステータス": STATUS_LABELS.get(content.status, content.status),
                "チェック": content.compliance_status,
                "更新日": content.updated_at,
                "投稿URL": content.published_url,
            }
            for content in contents
        ]
    )
    st.dataframe(
        summary,
        hide_index=True,
        column_config={
            "投稿URL": st.column_config.LinkColumn("投稿URL"),
            "更新日": st.column_config.DatetimeColumn("更新日", format="YYYY/MM/DD HH:mm"),
        },
    )
    selected_id = st.selectbox(
        "編集する投稿",
        [content.id for content in contents],
        format_func=lambda content_id: next(
            f"#{content.id} {content.channel}｜{content.title}"
            for content in contents
            if content.id == content_id
        ),
    )
    with session_scope() as session:
        content = get_content(session, int(selected_id))
        assert content is not None
        initial_body = content.approved_body or content.draft_body
        seo_details = get_setting(
            session,
            f"content_seo_{content.id}",
            {"seo_title": content.title, "summary": "", "hashtags": ""},
        )
        with st.container(border=True):
            st.markdown("**別の商品向けに作り直す**")
            st.caption(
                "媒体・テーマ・商品選択を投稿文作成画面へ引き継ぎます。"
                "元の本文は上書きせず、新しい投稿案として生成します。"
            )
            if st.button(
                "この投稿の設定を複製",
                icon=":material/content_copy:",
                key=f"clone_content_{content.id}",
            ):
                st.session_state["clone_content_request"] = {
                    "source_content_id": content.id,
                    "source_title": content.title,
                    "channel": content.channel,
                    "theme": content.theme,
                    "main_keyword": content.theme,
                    "product_ids": [product.id for product in content.products],
                }
                st.switch_page(Path(__file__).resolve().with_name("content_creation.py"))

        with st.form("content_review_form"):
            approved_body = st.text_area("確認・修正済み本文", value=initial_body, height=500)
            if content.channel == "note":
                st.markdown("**SEO・投稿情報**")
                seo_title = st.text_input(
                    "SEOタイトル", value=str(seo_details.get("seo_title", content.title))
                )
                seo_summary = st.text_area(
                    "記事要約", value=str(seo_details.get("summary", "")), height=100
                )
                seo_hashtags = st.text_input(
                    "推奨ハッシュタグ", value=str(seo_details.get("hashtags", ""))
                )
            else:
                seo_title = ""
                seo_summary = ""
                seo_hashtags = ""
            status = st.selectbox(
                "ステータス",
                STATUSES,
                index=STATUSES.index(content.status),
                format_func=lambda value: STATUS_LABELS.get(value, value),
            )
            reviewer = st.text_input("確認者", value=content.reviewer)
            work_minutes = st.number_input(
                "作業時間（分）", min_value=0, value=content.work_minutes
            )
            scheduled_date = st.date_input(
                "投稿予定日",
                value=content.scheduled_at.date() if content.scheduled_at else None,
            )
            scheduled_time = st.time_input(
                "投稿予定時刻",
                value=content.scheduled_at.time() if content.scheduled_at else time(9, 0),
            )
            published_url = st.text_input("投稿URL", value=content.published_url)
            confirmed = st.checkbox(
                "本文・リンク・PR表記・商品情報を投稿者本人が最終確認しました",
            )
            save = st.form_submit_button(
                "審査結果を保存", icon=":material/fact_check:", type="primary"
            )

        report = check_content(
            approved_body,
            content.products,
            affiliate_disclosure_required=True,
            info_verified_at=content.info_verified_at,
            comparison_basis_saved=all(
                bool(product.experience and product.experience.compared_products)
                for product in content.products
            ),
        )
        show_compliance_report(report.to_dict())

        if save:
            approval_statuses = {"approved", "scheduled", "published"}
            if status in approval_statuses and not confirmed:
                st.error("承認・投稿済みへの変更には、投稿者本人の最終確認が必要です。")
            elif status in approval_statuses and not reviewer.strip():
                st.error("確認者を入力してください。")
            elif status in approval_statuses and report.status == "投稿不可":
                st.error("投稿不可の問題を解消するまで承認できません。")
            elif status == "published" and not published_url.startswith("https://"):
                st.error("投稿済みへの変更にはHTTPSの投稿URLが必要です。")
            else:
                scheduled_at = (
                    datetime.combine(scheduled_date, scheduled_time).astimezone()
                    if scheduled_date
                    else None
                )
                published_at = (
                    datetime.now().astimezone() if status == "published" else content.published_at
                )
                save_content_review(
                    session,
                    content,
                    approved_body=approved_body,
                    status=status,
                    reviewer=reviewer.strip(),
                    scheduled_at=scheduled_at,
                    published_at=published_at,
                    published_url=published_url.strip(),
                    work_minutes=int(work_minutes),
                    compliance_status=report.status,
                    compliance_report=report.to_dict(),
                )
                if content.channel == "note":
                    set_setting(
                        session,
                        f"content_seo_{content.id}",
                        {
                            "seo_title": seo_title.strip(),
                            "summary": seo_summary.strip(),
                            "hashtags": seo_hashtags.strip(),
                        },
                    )
                st.success("投稿管理情報を保存しました。外部SNSへの投稿処理は行っていません。")
                st.rerun()

st.subheader("週単位の投稿カレンダー")
st.caption("予定を編集できます。外部SNSへの自動投稿は行いません。")
plan_frame = pd.DataFrame(weekly_plan)
edited_plan = st.data_editor(
    plan_frame,
    hide_index=True,
    num_rows="fixed",
    disabled=["曜日"],
    key="weekly_plan_editor",
)
if st.button("投稿計画を保存", icon=":material/calendar_month:"):
    records = edited_plan.fillna("").to_dict(orient="records")
    with session_scope() as session:
        set_setting(session, "weekly_plan", records)
    st.success("投稿計画を保存しました。")
