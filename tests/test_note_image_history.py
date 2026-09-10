from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.repositories import (
    delete_note_image_asset,
    list_note_image_assets,
    save_note_image_assets,
    select_note_image_asset,
)


def test_note_image_history_can_save_select_and_delete_candidates() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        assets = save_note_image_assets(
            session,
            batch_id="batch-1",
            article_title="比較記事",
            theme="コーヒー比較",
            motifs="白いカップ",
            prompt="生成プロンプト",
            model="gpt-image-2",
            image_data=[b"image-1", b"image-2", b"image-3"],
        )
        assert len(assets) == 3
        assert not any(asset.is_selected for asset in assets)

        selected = select_note_image_asset(session, assets[1].id)
        assert selected is not None
        assert selected.is_selected
        assert sum(asset.is_selected for asset in list_note_image_assets(session)) == 1

        assert delete_note_image_asset(session, assets[0].id)
        assert len(list_note_image_assets(session)) == 2
