from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Content, ContentProduct, Experience, Performance, Product
from app.repositories import delete_product


def test_delete_product_preserves_history_and_removes_private_notes() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        product = Product(item_code="shop:item", item_name="保存商品")
        product.experience = Experience(memo="削除対象の体験メモ")
        content = Content(channel="note", title="過去の投稿", draft_body="投稿本文")
        content.products.append(product)
        session.add_all([product, content])
        session.flush()
        performance = Performance(
            date=date.today(),
            product_id=product.id,
            product_name="保存商品",
            clicks=10,
        )
        session.add(performance)
        session.commit()
        product_id = product.id
        content_id = content.id
        performance_id = performance.id

        assert delete_product(session, product_id) is True
        session.commit()

        assert session.get(Product, product_id) is None
        assert session.scalar(
            select(func.count(Experience.id)).where(Experience.product_id == product_id)
        ) == 0
        assert session.scalar(
            select(func.count(ContentProduct.product_id)).where(
                ContentProduct.product_id == product_id
            )
        ) == 0
        assert session.get(Content, content_id) is not None
        saved_performance = session.get(Performance, performance_id)
        assert saved_performance is not None
        assert saved_performance.product_id is None
        assert saved_performance.product_name == "保存商品"


def test_delete_product_returns_false_for_missing_product() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        assert delete_product(session, 999) is False
