from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import AppSetting, Content, ContentProduct, Experience, Performance, Product
from app.schemas import ExperienceInput

DEFAULT_SCORE_WEIGHTS = {
    "affiliate_rate": 25.0,
    "review_count": 20.0,
    "review_average": 15.0,
    "price_fit": 15.0,
    "free_shipping": 10.0,
    "keyword_match": 15.0,
}

DEFAULT_WEEKLY_PLAN = [
    {"曜日": "月曜日", "内容": "X需要調査"},
    {"曜日": "火曜日", "内容": "note作成・公開"},
    {"曜日": "水曜日", "内容": "楽天ROOM"},
    {"曜日": "木曜日", "内容": "Instagram"},
    {"曜日": "金曜日", "内容": "Pinterest"},
    {"曜日": "土曜日", "内容": "Xで記事再利用"},
    {"曜日": "日曜日", "内容": "成果分析"},
]


def _product_query() -> Select[tuple[Product]]:
    return select(Product).options(selectinload(Product.experience)).order_by(Product.id.desc())


def list_products(session: Session) -> list[Product]:
    return list(session.scalars(_product_query()).all())


def get_product(session: Session, product_id: int) -> Product | None:
    return session.scalar(_product_query().where(Product.id == product_id))


def save_product(session: Session, values: dict[str, Any]) -> Product:
    item_code = str(values.get("item_code", "")).strip()
    if not item_code:
        raise ValueError("商品コードがありません。")
    product = session.scalar(select(Product).where(Product.item_code == item_code))
    if product is None:
        product = Product(item_code=item_code, item_name=str(values.get("item_name", "")))
        session.add(product)
    allowed = {column.name for column in Product.__table__.columns} - {"id", "item_code"}
    for key, value in values.items():
        if key in allowed:
            setattr(product, key, value)
    session.flush()
    return product


def upsert_experience(session: Session, product_id: int, payload: ExperienceInput) -> Experience:
    experience = session.scalar(select(Experience).where(Experience.product_id == product_id))
    if experience is None:
        experience = Experience(product_id=product_id)
        session.add(experience)
    for key, value in payload.model_dump().items():
        setattr(experience, key, value)
    session.flush()
    return experience


def delete_product(session: Session, product_id: int) -> bool:
    """Delete a saved product while preserving historical content and performance rows."""
    product = session.get(Product, product_id)
    if product is None:
        return False
    session.execute(
        update(Performance).where(Performance.product_id == product_id).values(product_id=None)
    )
    session.execute(delete(ContentProduct).where(ContentProduct.product_id == product_id))
    session.delete(product)
    session.flush()
    return True


def create_content(
    session: Session,
    *,
    channel: str,
    theme: str,
    title: str,
    body: str,
    product_ids: list[int],
    affiliate_url: str,
    pr_required: bool,
    compliance_status: str,
    compliance_report: dict[str, Any],
    info_verified_at: date | None,
) -> Content:
    products = list(session.scalars(select(Product).where(Product.id.in_(product_ids))).all())
    content = Content(
        channel=channel,
        theme=theme,
        title=title,
        draft_body=body,
        status="review",
        affiliate_url=affiliate_url,
        pr_required=pr_required,
        compliance_status=compliance_status,
        compliance_report=compliance_report,
        info_verified_at=info_verified_at,
        products=products,
    )
    session.add(content)
    session.flush()
    return content


def list_contents(session: Session) -> list[Content]:
    query = (
        select(Content).options(selectinload(Content.products)).order_by(Content.updated_at.desc())
    )
    return list(session.scalars(query).all())


def get_content(session: Session, content_id: int) -> Content | None:
    query = (
        select(Content)
        .options(selectinload(Content.products).selectinload(Product.experience))
        .where(Content.id == content_id)
    )
    return session.scalar(query)


def save_content_review(
    session: Session,
    content: Content,
    *,
    approved_body: str,
    status: str,
    reviewer: str,
    scheduled_at: datetime | None,
    published_at: datetime | None,
    published_url: str,
    work_minutes: int,
    compliance_status: str,
    compliance_report: dict[str, Any],
) -> Content:
    content.approved_body = approved_body
    content.status = status
    content.reviewer = reviewer
    content.scheduled_at = scheduled_at
    content.published_at = published_at
    content.published_url = published_url
    content.work_minutes = max(0, work_minutes)
    content.compliance_status = compliance_status
    content.compliance_report = compliance_report
    session.flush()
    return content


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(AppSetting, key)
    return default if row is None else row.value


def set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def dashboard_metrics(session: Session) -> dict[str, float | int]:
    today = date.today()
    month_start = today.replace(day=1)
    statuses: dict[str, int] = {
        str(row[0]): int(row[1])
        for row in session.execute(
            select(Content.status, func.count(Content.id)).group_by(Content.status)
        ).all()
    }
    perf = session.execute(
        select(
            func.coalesce(func.sum(Performance.clicks), 0),
            func.coalesce(func.sum(Performance.orders), 0),
            func.coalesce(func.sum(Performance.sales), 0),
            func.coalesce(func.sum(Performance.reward), 0),
        ).where(Performance.date >= month_start)
    ).one()
    return {
        "products": session.scalar(select(func.count(Product.id))) or 0,
        "drafts": statuses.get("drafting", 0) + statuses.get("idea", 0),
        "reviews": statuses.get("review", 0),
        "published": statuses.get("published", 0),
        "clicks": int(perf[0]),
        "orders": int(perf[1]),
        "sales": float(perf[2]),
        "reward": float(perf[3]),
    }


def add_performance_rows(session: Session, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        session.add(Performance(**row))
    session.flush()
    return len(rows)
