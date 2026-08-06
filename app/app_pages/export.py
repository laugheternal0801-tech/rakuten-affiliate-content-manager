from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.config import PROJECT_ROOT
from app.database import session_scope
from app.repositories import get_content, list_contents
from app.services.exporter import export_content_bundle

with session_scope() as session:
    contents = list_contents(session)

if not contents:
    st.info("エクスポートできる投稿がありません。")
    st.stop()

selected_ids = st.multiselect(
    "対象投稿",
    [content.id for content in contents],
    format_func=lambda content_id: next(
        f"#{content.id} {content.channel}｜{content.title}（{content.status}）"
        for content in contents
        if content.id == content_id
    ),
)
theme = st.text_input("出力テーマ", placeholder="例: coffee-comparison")
bom = st.toggle("CSVをUTF-8 BOM付きにする", value=True)
st.caption("CSV数式インジェクション対策を適用し、exports配下だけへ出力します。")

if st.button(
    "ファイル一式を出力",
    icon=":material/folder_zip:",
    type="primary",
    disabled=not selected_ids or not theme.strip(),
):
    with session_scope() as session:
        selected = [get_content(session, content_id) for content_id in selected_ids]
        selected = [content for content in selected if content is not None]
        products_by_id = {
            product.id: product for content in selected for product in content.products
        }
        target = export_content_bundle(
            contents=selected,
            products=list(products_by_id.values()),
            export_root=Path(PROJECT_ROOT / "exports"),
            theme=theme.strip(),
            bom=bom,
        )
    st.session_state.last_export = str(target)
    st.success(f"出力しました: {target}")

last_export = st.session_state.get("last_export")
if last_export:
    target = Path(last_export)
    if target.is_dir() and target.resolve().is_relative_to((PROJECT_ROOT / "exports").resolve()):
        st.subheader("出力ファイル")
        for path in sorted(target.iterdir()):
            if path.is_file():
                st.download_button(
                    f"{path.name} をダウンロード",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="text/csv" if path.suffix == ".csv" else "text/plain",
                    key=f"download_{path.name}",
                    icon=":material/download:",
                )

st.warning(
    "出力は投稿用ファイルの準備までです。内容を本人が確認し、各媒体へ手動で投稿してください。",
    icon=":material/person_check:",
)
