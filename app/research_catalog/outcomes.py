from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Performance, Product
from app.research_catalog.models import ResearchPlan
from app.research_catalog.repositories import get_research_plan


@dataclass(frozen=True, slots=True)
class PerformanceLinkResult:
    linked: int = 0
    ambiguous: int = 0
    unmatched: int = 0


@dataclass(frozen=True, slots=True)
class PlanPerformanceRow:
    date: date
    channel: str
    product_name: str
    clicks: int
    orders: int
    sales: float
    reward: float
    attribution: str
    source_file: str


@dataclass(frozen=True, slots=True)
class PlanPerformanceSummary:
    rows: tuple[PlanPerformanceRow, ...] = ()

    @property
    def clicks(self) -> int:
        return sum(row.clicks for row in self.rows)

    @property
    def orders(self) -> int:
        return sum(row.orders for row in self.rows)

    @property
    def sales(self) -> float:
        return sum(row.sales for row in self.rows)

    @property
    def reward(self) -> float:
        return sum(row.reward for row in self.rows)

    @property
    def conversion_rate(self) -> float | None:
        return self.orders / self.clicks if self.clicks else None


def link_unlinked_performance_rows(session: Session) -> PerformanceLinkResult:
    """Link imported rows only when local product identity has one exact match."""

    products = list(session.scalars(select(Product)))
    linked = 0
    ambiguous = 0
    unmatched = 0
    for performance in session.scalars(
        select(Performance).where(Performance.product_id.is_(None))
    ):
        matches = _exact_product_matches(performance, products)
        if len(matches) == 1:
            performance.product_id = matches[0].id
            linked += 1
        elif len(matches) > 1:
            ambiguous += 1
        else:
            unmatched += 1
    session.flush()
    return PerformanceLinkResult(
        linked=linked,
        ambiguous=ambiguous,
        unmatched=unmatched,
    )


def summarize_plan_performance(
    session: Session,
    plan_id: int,
) -> PlanPerformanceSummary:
    plan = get_research_plan(session, plan_id)
    if plan is None:
        raise ValueError("比較企画が見つかりません")
    product_ids = _linked_legacy_product_ids(plan)
    content_ids = {
        link.content_id for brief in plan.briefs for link in brief.content_links
    }
    predicates = []
    if product_ids:
        predicates.append(Performance.product_id.in_(product_ids))
    if content_ids:
        predicates.append(Performance.content_id.in_(content_ids))
    if not predicates:
        return PlanPerformanceSummary()

    statement = select(Performance).where(or_(*predicates)).order_by(Performance.date.desc())
    rows: list[PlanPerformanceRow] = []
    for item in session.scalars(statement):
        reasons: list[str] = []
        if item.product_id in product_ids:
            reasons.append("商品リンク")
        if item.content_id in content_ids:
            reasons.append("制作コンテンツリンク")
        rows.append(
            PlanPerformanceRow(
                date=item.date,
                channel=item.channel,
                product_name=item.product_name,
                clicks=item.clicks,
                orders=item.orders,
                sales=item.sales,
                reward=item.reward,
                attribution="＋".join(reasons),
                source_file=item.source_file,
            )
        )
    return PlanPerformanceSummary(rows=tuple(rows))


def _linked_legacy_product_ids(plan: ResearchPlan) -> set[int]:
    product_ids: set[int] = set()
    for link in plan.product_links:
        current_id = link.product.legacy_product_id
        snapshot_id = link.product_snapshot_json.get("product", {}).get(
            "legacy_product_id"
        )
        for candidate in (current_id, snapshot_id):
            if isinstance(candidate, int):
                product_ids.add(candidate)
            elif isinstance(candidate, str) and candidate.isdigit():
                product_ids.add(int(candidate))
    return product_ids


def _exact_product_matches(
    performance: Performance,
    products: list[Product],
) -> list[Product]:
    report_url = performance.url.strip()
    if report_url:
        url_matches = [
            product
            for product in products
            if report_url
            in {
                product.item_url.strip(),
                product.affiliate_url.strip(),
            }
        ]
        if url_matches:
            return url_matches

    report_name = performance.product_name.strip()
    if not report_name:
        return []
    name_matches = [
        product for product in products if product.item_name.strip() == report_name
    ]
    report_shop = performance.shop_name.strip()
    if report_shop:
        shop_matches = [
            product for product in name_matches if product.shop_name.strip() == report_shop
        ]
        if shop_matches:
            return shop_matches
    return name_matches
