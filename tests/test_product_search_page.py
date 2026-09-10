from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from app.models import Base


def test_product_search_page_only_shows_simple_search_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = create_engine(f"sqlite:///{(tmp_path / 'product-search.db').as_posix()}")
    Base.metadata.create_all(database)

    @contextmanager
    def isolated_session_scope() -> Iterator[Session]:
        with Session(database) as session:
            yield session

    monkeypatch.setattr("app.database.session_scope", isolated_session_scope)
    page = Path(__file__).parents[1] / "app" / "app_pages" / "product_search.py"
    app = AppTest.from_file(str(page), default_timeout=20).run()

    assert not app.exception
    assert [widget.label for widget in app.text_input] == ["検索キーワード"]
    assert [widget.label for widget in app.number_input] == ["取得件数"]
    assert [widget.label for widget in app.selectbox][:1] == ["並び順"]
    assert [widget.label for widget in app.checkbox][:3] == [
        "送料無料のみ",
        "在庫ありのみ",
        "画像ありのみ",
    ]

    visible_labels = {
        widget.label
        for widget_type in (
            app.text_input,
            app.number_input,
            app.selectbox,
            app.checkbox,
        )
        for widget in widget_type
    }
    assert visible_labels.isdisjoint(
        {
            "ジャンルID",
            "最低価格",
            "最高価格",
            "最低レビュー件数",
            "最低平均評価",
            "最低アフィリエイト料率（%）",
            "除外キーワード",
        }
    )
