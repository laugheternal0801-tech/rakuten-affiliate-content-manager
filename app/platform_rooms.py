from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from app.database import session_scope
from app.models import Content
from app.repositories import list_contents, list_products


@dataclass(frozen=True)
class RoomMenuItem:
    title: str
    description: str
    icon: str


@dataclass(frozen=True)
class PlatformRoom:
    channel: str
    slug: str
    title: str
    icon: str
    description: str
    creation_hint: str
    external_label: str
    external_url: str
    menu_items: tuple[RoomMenuItem, ...]


PLATFORM_ROOMS: dict[str, PlatformRoom] = {
    "note": PlatformRoom(
        channel="note",
        slug="note",
        title="noteルーム",
        icon=":material/article:",
        description="比較記事の企画、本文作成、推敲、アイキャッチ、投稿準備をまとめる部屋です。",
        creation_hint="保存した商品から、Claudeによる比較記事とGPT Image 2の画像を作成できます。",
        external_label="noteを開く",
        external_url="https://note.com/",
        menu_items=(
            RoomMenuItem(
                "比較記事", "5〜7商品を比較する約3,000字の記事を作成", ":material/compare_arrows:"
            ),
            RoomMenuItem(
                "記事の推敲", "導入・見出し・比較表・まとめを部分修正", ":material/edit_note:"
            ),
            RoomMenuItem(
                "アイキャッチ", "記事テーマからnote向け画像を生成・保存", ":material/image:"
            ),
            RoomMenuItem(
                "構成最適化", "伸びている公開記事の数値傾向を構成へ反映", ":material/trending_up:"
            ),
        ),
    ),
    "Pinterest": PlatformRoom(
        channel="Pinterest",
        slug="pinterest",
        title="Pinterestルーム",
        icon=":material/push_pin:",
        description="検索されやすいピンのタイトル、説明文、画像制作メモをまとめる部屋です。",
        creation_hint="検索意図別の投稿案、画像内テキスト、構図案、保存先ボード案を作成できます。",
        external_label="Pinterestを開く",
        external_url="https://www.pinterest.com/",
        menu_items=(
            RoomMenuItem("ピンタイトル", "検索意図が伝わるタイトルを作成", ":material/title:"),
            RoomMenuItem(
                "説明文", "商品選びへ自然につながる説明文を作成", ":material/description:"
            ),
            RoomMenuItem(
                "画像制作メモ", "画像内テキストと構図案を整理", ":material/photo_library:"
            ),
            RoomMenuItem(
                "ボード整理", "投稿に合う保存先ボード名を提案", ":material/dashboard_customize:"
            ),
        ),
    ),
    "X": PlatformRoom(
        channel="X",
        slug="x",
        title="Xルーム",
        icon=":material/alternate_email:",
        description="短い投稿案を複数作り、反応を見ながら改善するための部屋です。",
        creation_hint="悩み提示・商品比較・確認メモなど、切り口の異なる短文を最大3案作成できます。",
        external_label="Xを開く",
        external_url="https://x.com/",
        menu_items=(
            RoomMenuItem("短文投稿", "結論を先に伝える読みやすい投稿案", ":material/short_text:"),
            RoomMenuItem(
                "切り口違い", "同じ商品から最大3種類の案を作成", ":material/account_tree:"
            ),
            RoomMenuItem(
                "文字数確認", "目標文字数との差とハッシュタグ数を確認", ":material/data_info_alert:"
            ),
            RoomMenuItem("記事の再利用", "noteのテーマを短い投稿へ展開", ":material/recycling:"),
        ),
    ),
    "Instagram": PlatformRoom(
        channel="Instagram",
        slug="instagram",
        title="Instagramルーム",
        icon=":material/photo_camera:",
        description="キャプション、カルーセル構成、撮影確認をまとめる部屋です。",
        creation_hint="保存したくなる投稿文と、8枚構成のカルーセル案を作成できます。",
        external_label="Instagramを開く",
        external_url="https://www.instagram.com/",
        menu_items=(
            RoomMenuItem("キャプション", "共感から商品確認へつなぐ投稿文", ":material/chat:"),
            RoomMenuItem("カルーセル", "1枚目から8枚目までの構成案", ":material/view_carousel:"),
            RoomMenuItem("リール冒頭", "最初の数秒で伝える短い導入案", ":material/movie:"),
            RoomMenuItem("撮影チェック", "権利と色味に配慮した確認項目", ":material/fact_check:"),
        ),
    ),
    "楽天ROOM": PlatformRoom(
        channel="楽天ROOM",
        slug="rakuten-room",
        title="楽天ROOMルーム",
        icon=":material/storefront:",
        description="楽天市場の商品情報から紹介文とコレクション案を作る部屋です。",
        creation_hint="価格・レビュー・送料・商品特徴を整理した紹介文を最大3案作成できます。",
        external_label="楽天ROOMを開く",
        external_url="https://room.rakuten.co.jp/",
        menu_items=(
            RoomMenuItem(
                "商品紹介文", "確認済み情報を短く読みやすく整理", ":material/rate_review:"
            ),
            RoomMenuItem("複数案", "候補紹介・比較整理など切り口を変更", ":material/content_copy:"),
            RoomMenuItem(
                "コレクション", "商品テーマに合うまとめ名を提案", ":material/collections_bookmark:"
            ),
            RoomMenuItem("リンク確認", "楽天アフィリエイトURLを投稿前に確認", ":material/link:"),
        ),
    ),
}


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


def get_platform_room(channel: str) -> PlatformRoom:
    try:
        return PLATFORM_ROOMS[channel]
    except KeyError as exc:
        raise ValueError(f"未対応のプラットフォームです: {channel}") from exc


def summarize_platform_contents(contents: Iterable[Content], channel: str) -> dict[str, int]:
    relevant = [content for content in contents if content.channel == channel]
    return {
        "total": len(relevant),
        "review": sum(content.status == "review" for content in relevant),
        "scheduled": sum(content.status == "scheduled" for content in relevant),
        "published": sum(content.status == "published" for content in relevant),
    }


def render_platform_room(channel: str) -> None:
    room = get_platform_room(channel)
    page_dir = Path(__file__).resolve().parent / "app_pages"

    st.caption(room.description)
    st.info(room.creation_hint, icon=room.icon)

    with session_scope() as session:
        contents = list_contents(session)
        product_count = len(list_products(session))

    relevant_contents = [content for content in contents if content.channel == channel]
    metrics = summarize_platform_contents(relevant_contents, channel)

    metric_columns = st.columns(4)
    metric_columns[0].metric("保存済み", metrics["total"])
    metric_columns[1].metric("確認待ち", metrics["review"])
    metric_columns[2].metric("投稿予定", metrics["scheduled"])
    metric_columns[3].metric("投稿済み", metrics["published"])

    st.subheader("このルームから始める", anchor=False)
    with st.container(horizontal=True):
        if st.button(
            f"{room.channel}向け投稿を作成",
            key=f"room_create_{room.slug}",
            icon=":material/edit_square:",
            type="primary",
        ):
            st.session_state["creation_channel"] = room.channel
            st.session_state.pop("active_clone_request", None)
            st.switch_page(page_dir / "content_creation.py")
        if st.button(
            "この媒体の投稿を管理",
            key=f"room_manage_{room.slug}",
            icon=":material/fact_check:",
        ):
            st.session_state["management_channel_request"] = room.channel
            st.switch_page(page_dir / "content_management.py")
        if st.button(
            f"商品を検索・保存（保存済み {product_count}件）",
            key=f"room_products_{room.slug}",
            icon=":material/search:",
        ):
            st.switch_page(page_dir / "product_search.py")

    st.subheader("制作メニュー", anchor=False)
    menu_columns = st.columns(2)
    for index, item in enumerate(room.menu_items):
        with menu_columns[index % 2].container(border=True, height="stretch"):
            st.markdown(f"{item.icon} **{item.title}**")
            st.caption(item.description)

    st.subheader("最近の投稿", anchor=False)
    if not relevant_contents:
        st.info(
            f"{room.channel}の投稿はまだありません。上の作成ボタンから最初の案を作れます。",
            icon=":material/inbox:",
        )
    else:
        for content in relevant_contents[:5]:
            with st.container(border=True):
                st.write(content.title or "無題の投稿")
                status_label = STATUS_LABELS.get(content.status, content.status)
                st.caption(
                    f"{status_label} · 更新 {content.updated_at:%Y/%m/%d %H:%M} · "
                    f"テーマ: {content.theme or '未設定'}"
                )

    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption("外部サービスへの自動投稿は行いません。内容を確認してから手動で投稿します。")
        st.link_button(
            room.external_label,
            room.external_url,
            icon=":material/open_in_new:",
        )
