from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.database import session_scope
from app.models import Content, Performance
from app.repositories import add_performance_rows, get_setting, set_setting
from app.services.analytics import (
    CANONICAL_FIELDS,
    CSVImportError,
    apply_mapping,
    performance_summary,
    read_report_csv,
    rows_for_database,
    suggest_mapping,
)

st.caption("楽天アフィリエイトから手動でダウンロードしたCSVを読み込みます。自動取得は行いません。")
uploaded = st.file_uploader("楽天成果レポートCSV", type=["csv"], max_upload_size=10)
if uploaded is not None:
    try:
        data = uploaded.getvalue()
        file_key = f"{uploaded.name}:{len(data)}"
        if st.session_state.get("csv_file_key") != file_key:
            frame = read_report_csv(data, uploaded.name)
            with session_scope() as session:
                saved_mapping = get_setting(session, "csv_mapping", {})
            suggestions = suggest_mapping(list(frame.columns))
            st.session_state.csv_frame = frame
            st.session_state.csv_mapping = {
                field: saved_mapping.get(field, suggestions.get(field, ""))
                for field in CANONICAL_FIELDS
            }
            st.session_state.csv_file_key = file_key
            st.session_state.csv_filename = uploaded.name
    except CSVImportError as exc:
        st.error(str(exc))

frame = st.session_state.get("csv_frame")
if isinstance(frame, pd.DataFrame):
    st.subheader("列マッピング")
    st.caption("列名が変更されても、保存した対応関係を再利用できます。")
    mapping: dict[str, str] = {}
    options = ["", *list(frame.columns)]
    for row_start in range(0, len(CANONICAL_FIELDS), 2):
        columns = st.columns(2)
        for offset, field in enumerate(CANONICAL_FIELDS[row_start : row_start + 2]):
            current = st.session_state.csv_mapping.get(field, "")
            index = options.index(current) if current in options else 0
            mapping[field] = columns[offset].selectbox(
                f"{field}に対応するCSV列",
                options,
                index=index,
                key=f"mapping_{field}",
            )
    with st.container(horizontal=True):
        if st.button("マッピングを保存", icon=":material/save:"):
            with session_scope() as session:
                set_setting(session, "csv_mapping", mapping)
            st.session_state.csv_mapping = mapping
            st.success("列マッピングを保存しました。")
        if st.button("CSVを取り込む", icon=":material/upload:", type="primary"):
            try:
                mapped = apply_mapping(frame, mapping)
                rows = rows_for_database(mapped, st.session_state.get("csv_filename", "report.csv"))
                with session_scope() as session:
                    count = add_performance_rows(session, rows)
                    set_setting(session, "csv_mapping", mapping)
                st.success(f"{count}行を取り込みました。")
                st.session_state.csv_mapping = mapping
            except CSVImportError as exc:
                st.error(str(exc))
    st.dataframe(frame.head(20), hide_index=True)

with session_scope() as session:
    performances = list(session.scalars(select(Performance).order_by(Performance.date)))
    content_work_minutes = sum(session.scalars(select(Content.work_minutes)).all())

if not performances:
    st.info("取り込み済みの成果データはありません。")
    st.stop()

analysis = pd.DataFrame(
    [
        {
            "日付": row.date,
            "クリック数": row.clicks,
            "注文件数": row.orders,
            "売上金額": row.sales,
            "成果報酬": row.reward,
            "媒体": row.channel or "未設定",
            "商品名": row.product_name or "未設定",
            "テーマ": row.theme or "未設定",
        }
        for row in performances
    ]
)
summary = performance_summary(analysis)
work_minutes = sum(row.work_minutes for row in performances) + content_work_minutes
with st.container(horizontal=True):
    st.metric("クリック数", f"{summary['clicks']:,.0f}", border=True)
    st.metric("売上", f"¥{summary['sales']:,.0f}", border=True)
    st.metric("報酬", f"¥{summary['reward']:,.0f}", border=True)
    st.metric("購入転換率", f"{summary['purchase_conversion_rate']:.2%}", border=True)
with st.container(horizontal=True):
    st.metric(
        "クリック率", "—", border=True, help="インプレッション数がCSVにないため算出できません。"
    )
    st.metric("1件当たり報酬", f"¥{summary['reward_per_order']:,.0f}", border=True)
    hourly = summary["reward"] / (work_minutes / 60) if work_minutes else 0
    st.metric("作業時間当たり報酬", f"¥{hourly:,.0f}/時", border=True)

st.caption("クリック率はインプレッション数が取得できる場合にのみ算出できます。")
left, right = st.columns(2)
with left.container(border=True):
    st.subheader("期間別クリック数")
    daily = analysis.groupby("日付", as_index=False)[["クリック数", "売上金額", "成果報酬"]].sum()
    st.line_chart(daily, x="日付", y="クリック数")
    st.subheader("期間別売上・報酬")
    st.line_chart(daily, x="日付", y=["売上金額", "成果報酬"])
with right.container(border=True):
    st.subheader("媒体別報酬")
    by_channel = analysis.groupby("媒体", as_index=False)["成果報酬"].sum()
    st.bar_chart(by_channel, x="媒体", y="成果報酬")
    st.subheader("商品別報酬")
    by_product = (
        analysis.groupby("商品名", as_index=False)["成果報酬"].sum().nlargest(15, "成果報酬")
    )
    st.bar_chart(by_product, x="商品名", y="成果報酬", horizontal=True)

with st.container(border=True):
    st.subheader("テーマ別報酬")
    by_theme = analysis.groupby("テーマ", as_index=False)["成果報酬"].sum()
    st.bar_chart(by_theme, x="テーマ", y="成果報酬")
