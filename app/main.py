from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.services.sample_data import seed_sample_data  # noqa: E402

st.set_page_config(
    page_title="楽天アフィ｜市場調査・コンテンツ運用",
    page_icon=":material/account_tree:",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "市場調査、AI意思決定、制作、承認、配信、学習をつなぐローカルアプリです。",
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
page_dir = Path(__file__).resolve().parent / "app_pages"
with st.sidebar:
    st.markdown("**楽天アフィ運用システム**")
    live_ai_count = len(settings.ai_council_provider_models)
    if live_ai_count:
        st.badge(
            f"AI API {live_ai_count}社 設定あり",
            icon=":material/cloud_done:",
            color="green",
        )
    else:
        st.badge("ローカルモード", icon=":material/science:", color="orange")
    st.caption("調査・意思決定・制作・配信・学習を支援するローカルアプリ")
    st.warning(
        "自動投稿・自動DM・自動コメント・自動リプライは行いません。",
        icon=":material/person_check:",
    )
    st.caption("v0.1.0 · 最終確認は投稿者本人が行ってください")

pages = {
    "ホーム・商品": [
        st.Page(page_dir / "dashboard.py", title="ダッシュボード", icon=":material/dashboard:"),
        st.Page(page_dir / "product_search.py", title="商品検索", icon=":material/search:"),
        st.Page(page_dir / "products.py", title="商品・体験情報", icon=":material/inventory_2:"),
    ],
    "商品調査・企画": [
        st.Page(
            page_dir / "product_research.py",
            title="商品・調査管理",
            icon=":material/manage_search:",
        ),
        st.Page(
            page_dir / "research_plans.py",
            title="比較・企画",
            icon=":material/compare_arrows:",
        ),
    ],
    "プラットフォームルーム": [
        st.Page(page_dir / "note_room.py", title="noteルーム", icon=":material/article:"),
        st.Page(
            page_dir / "pinterest_room.py",
            title="Pinterestルーム",
            icon=":material/push_pin:",
        ),
        st.Page(page_dir / "x_room.py", title="Xルーム", icon=":material/alternate_email:"),
        st.Page(
            page_dir / "instagram_room.py",
            title="Instagramルーム",
            icon=":material/photo_camera:",
        ),
        st.Page(
            page_dir / "rakuten_room.py",
            title="楽天ROOMルーム",
            icon=":material/storefront:",
        ),
    ],
    "共通管理": [
        st.Page(
            page_dir / "operating_system.py",
            title="AI意思決定",
            icon=":material/account_tree:",
        ),
        st.Page(
            page_dir / "opportunity_discovery.py",
            title="市場機会の発見",
            icon=":material/radar:",
        ),
        st.Page(
            page_dir / "market_intelligence.py",
            title="SNS市場調査",
            icon=":material/travel_explore:",
        ),
        st.Page(
            page_dir / "creative_production.py",
            title="AI制作スタジオ",
            icon=":material/movie_edit:",
        ),
        st.Page(
            page_dir / "publishing_center.py",
            title="公開・学習センター",
            icon=":material/publish:",
        ),
        st.Page(
            page_dir / "decision_learning.py",
            title="意思決定・学習履歴",
            icon=":material/query_stats:",
        ),
        st.Page(page_dir / "ai_council.py", title="AI会議", icon=":material/groups:"),
        st.Page(page_dir / "content_creation.py", title="投稿文作成", icon=":material/edit_note:"),
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
