from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Performance, Product
from app.research_catalog.outcomes import (
    link_unlinked_performance_rows,
    summarize_plan_performance,
)
from app.research_catalog.repositories import save_research_plan, save_research_product
from app.research_catalog.schemas import ProductCandidateInput, ResearchPlanInput


def test_exact_performance_linking_and_plan_rollup_do_not_guess(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'outcomes.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = Product(
            item_code="LEGACY-A",
            item_name="同名商品",
            shop_name="店舗A",
            item_url="https://shop.example/a?x=1&y=2",
            affiliate_url="https://affiliate.example/a?tracking=kept",
        )
        second = Product(
            item_code="LEGACY-B",
            item_name="同名商品",
            shop_name="店舗B",
            item_url="https://shop.example/b",
            affiliate_url="https://affiliate.example/b?tracking=kept",
        )
        session.add_all([first, second])
        session.flush()
        rows = [
            Performance(
                date=date(2026, 9, 1),
                product_name="同名商品",
                shop_name="店舗A",
                clicks=10,
                orders=1,
                sales=2_000,
                reward=80,
                source_file="report.csv",
            ),
            Performance(
                date=date(2026, 9, 2),
                product_name="別表記",
                url="https://affiliate.example/b?tracking=kept",
                clicks=20,
                orders=2,
                sales=4_000,
                reward=160,
                source_file="report.csv",
            ),
            Performance(
                date=date(2026, 9, 3),
                product_name="同名商品",
                clicks=30,
                orders=3,
                sales=6_000,
                reward=240,
                source_file="report.csv",
            ),
        ]
        session.add_all(rows)
        session.flush()

        linked = link_unlinked_performance_rows(session)
        assert linked.linked == 2
        assert linked.ambiguous == 1
        assert rows[0].product_id == first.id
        assert rows[1].product_id == second.id
        assert rows[2].product_id is None

        research_products = []
        for index, legacy in enumerate((first, second), 1):
            research_products.append(
                save_research_product(
                    session,
                    ProductCandidateInput(
                        product_code=f"RESEARCH-{index}",
                        product_name=legacy.item_name,
                        manufacturer="メーカー",
                        model_number=f"MODEL-{index}",
                        variant=f"{index}型",
                        genre="テスト",
                        audience="比較したい人",
                        pain_point="違いが分からない",
                        use_case="自宅",
                        legacy_product_id=legacy.id,
                    ),
                )
            )
        plan = save_research_plan(
            session,
            ResearchPlanInput(
                title="成果を追跡する企画",
                audience="比較したい人",
                pain_point="違いが分からない",
                comparison_axes=["価格"],
            ),
            {
                product.id: {"価格": f"{index * 1000}円"}
                for index, product in enumerate(research_products, 1)
            },
        )

        summary = summarize_plan_performance(session, plan.id)

    assert len(summary.rows) == 2
    assert summary.clicks == 30
    assert summary.orders == 3
    assert summary.sales == 6_000
    assert summary.reward == 240
    assert summary.conversion_rate == 0.1
    assert {row.attribution for row in summary.rows} == {"商品リンク"}
