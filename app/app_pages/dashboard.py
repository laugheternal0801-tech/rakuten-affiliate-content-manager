from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.database import session_scope
from app.models import Content, Performance, Product
from app.repositories import dashboard_metrics

with session_scope() as session:
    metrics = dashboard_metrics(session)
    recent_products = list(
        session.scalars(select(Product).order_by(Product.fetched_at.desc()).limit(8))
    )
    updates = list(
        session.scalars(
            select(Content)
            .where(Content.status == "update_required")
            .order_by(Content.updated_at.desc())
            .limit(8)
        )
    )
    performances = list(session.scalars(select(Performance).order_by(Performance.date)))

with st.container(horizontal=True):
    st.metric("保存商品", f"{metrics['products']:,}件", border=True)
    st.metric("下書き", f"{metrics['drafts']:,}件", border=True)
    st.metric("確認待ち", f"{metrics['reviews']:,}件", border=True)
    st.metric("投稿済み", f"{metrics['published']:,}件", border=True)

with st.container(horizontal=True):
    st.metric("今月のクリック", f"{metrics['clicks']:,}", border=True)
    st.metric("今月の売上件数", f"{metrics['orders']:,}件", border=True)
    st.metric("今月の売上金額", f"¥{metrics['sales']:,.0f}", border=True)
    st.metric("今月の成果報酬", f"¥{metrics['reward']:,.0f}", border=True)

if performances:
    perf_frame = pd.DataFrame(
        [
            {
                "日付": row.date,
                "媒体": row.channel or "未設定",
                "商品": row.product_name or "未設定",
                "成果報酬": row.reward,
            }
            for row in performances
        ]
    )
    left, right = st.columns(2)
    with left.container(border=True):
        st.subheader("媒体別成果")
        channel = perf_frame.groupby("媒体", as_index=False)["成果報酬"].sum()
        st.bar_chart(channel, x="媒体", y="成果報酬")
    with right.container(border=True):
        st.subheader("商品別成果")
        product = (
            perf_frame.groupby("商品", as_index=False)["成果報酬"].sum().nlargest(10, "成果報酬")
        )
        st.bar_chart(product, x="商品", y="成果報酬", horizontal=True)
else:
    st.info(
        "成果データはまだありません。成果レポート画面からCSVを読み込めます。",
        icon=":material/upload_file:",
    )

left, right = st.columns(2)
with left.container(border=True):
    st.subheader("最近更新された商品")
    if recent_products:
        st.dataframe(
            pd.DataFrame(
                [
                    {"商品名": p.item_name, "取得日時": p.fetched_at, "評価点": p.score}
                    for p in recent_products
                ]
            ),
            hide_index=True,
            column_config={"評価点": st.column_config.ProgressColumn(min_value=0, max_value=100)},
        )
    else:
        st.caption("商品はまだありません。")
with right.container(border=True):
    st.subheader("更新確認が必要な投稿")
    if updates:
        st.dataframe(
            pd.DataFrame(
                [{"タイトル": c.title, "媒体": c.channel, "更新日": c.updated_at} for c in updates]
            ),
            hide_index=True,
        )
    else:
        st.caption("更新確認が必要な投稿はありません。")

st.caption(
    "架空のサンプル商品は公開に使えません。実在商品の最新情報は楽天市場で再確認してください。"
)
