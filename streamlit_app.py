"""Canonical Streamlit entry point for local and Community Cloud runs."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.services.sample_data import seed_sample_data  # noqa: E402

st.set_page_config(
    page_title="楽天アフィ｜投稿文作成・運用管理",
    page_icon=":material/edit_note:",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "楽天アフィリエイト投稿の下書き・確認・分析を支援するアプリです。",
    },
)

init_db()
with session_scope() as session:
    seeded = seed_sample_data(session)

st.session_state.setdefault("search_results", [])
st.session_state.setdefault("generated_content", None)
st.session_state.setdefault("generated_variations", [])
st.session_state.setdefault("csv_frame", None)
st.session_state.setdefault("csv_mapping", {})

settings = get_settings()
page_dir = PROJECT_ROOT / "app" / "app_pages"
with st.sidebar:
    st.markdown("**楽天アフィ**")
    if settings.rakuten_configured:
        st.badge("楽天APIモード", icon=":material/cloud_done:", color="green")
    else:
        st.badge("サンプルモード", icon=":material/science:", color="orange")
    st.caption("下書き・確認・分析を支援するローカルアプリ")
    st.warning(
        "自動投稿・自動DM・自動コメント・自動リプライは行いません。",
        icon=":material/person_check:",
    )
    st.caption("v0.1.0 · 最終確認は投稿者本人が行ってください")

pages = {
    "運用": [
        st.Page(page_dir / "dashboard.py", title="ダッシュボード", icon=":material/dashboard:"),
        st.Page(page_dir / "product_search.py", title="商品検索", icon=":material/search:"),
        st.Page(page_dir / "products.py", title="商品・体験情報", icon=":material/inventory_2:"),
        st.Page(
            page_dir / "content_creation.py", title="投稿文作成", icon=":material/edit_note:"
        ),
        st.Page(page_dir / "content_management.py", title="投稿管理", icon=":material/fact_check:"),
    ],
    "分析・設定": [
        st.Page(page_dir / "analytics.py", title="成果レポート", icon=":material/analytics:"),
        st.Page(page_dir / "export.py", title="エクスポート", icon=":material/download:"),
        st.Page(page_dir / "settings.py", title="設定", icon=":material/settings:"),
    ],
}

page = st.navigation(pages, position="sidebar")
st.title(f"{page.icon} {page.title}")
if seeded:
    st.toast("初回確認用の架空サンプルデータを作成しました。")
page.run()
