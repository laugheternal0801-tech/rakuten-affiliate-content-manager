from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Content, Product
from app.research_catalog.models import (
    ResearchArticleBrief,
    ResearchContentLink,
    ResearchEvidence,
    ResearchOffer,
    ResearchPlan,
    ResearchPlanProduct,
    ResearchPlanRevision,
    ResearchProduct,
    ResearchSchemaVersion,
)
from app.research_catalog.schemas import (
    EvidenceInput,
    OfferInput,
    ProductCandidateInput,
    ProductResearchStatus,
    ResearchPlanInput,
)
from app.research_catalog.time import to_utc, utc_now

RESEARCH_SCHEMA_VERSION = 1


def ensure_schema_version(session: Session) -> None:
    if session.get(ResearchSchemaVersion, RESEARCH_SCHEMA_VERSION) is None:
        session.add(ResearchSchemaVersion(version=RESEARCH_SCHEMA_VERSION))
        session.flush()


def _product_query() -> Select[tuple[ResearchProduct]]:
    return select(ResearchProduct).options(
        selectinload(ResearchProduct.offers), selectinload(ResearchProduct.evidence)
    )


def list_research_products(
    session: Session,
    *,
    genre: str = "",
    status: str = "",
    audience_query: str = "",
    include_archived: bool = False,
) -> list[ResearchProduct]:
    query = _product_query().order_by(ResearchProduct.updated_at.desc())
    if genre:
        query = query.where(ResearchProduct.genre == genre)
    if status:
        query = query.where(ResearchProduct.status == status)
    elif not include_archived:
        query = query.where(ResearchProduct.status != ProductResearchStatus.ARCHIVED.value)
    if audience_query:
        token = f"%{audience_query.strip()}%"
        query = query.where(
            ResearchProduct.audience.ilike(token) | ResearchProduct.pain_point.ilike(token)
        )
    return list(session.scalars(query).unique().all())


def list_research_genres(session: Session) -> list[str]:
    values = session.scalars(
        select(ResearchProduct.genre)
        .where(ResearchProduct.genre != "")
        .distinct()
        .order_by(ResearchProduct.genre)
    )
    return [str(value) for value in values]


def get_research_product(session: Session, product_id: int) -> ResearchProduct | None:
    return session.scalar(_product_query().where(ResearchProduct.id == product_id))


def save_research_product(
    session: Session,
    values: ProductCandidateInput,
    *,
    product_id: int | None = None,
) -> ResearchProduct:
    if product_id is None:
        product = ResearchProduct()
        session.add(product)
    else:
        existing_product = get_research_product(session, product_id)
        if existing_product is None:
            raise ValueError("商品候補が見つかりません")
        product = existing_product

    for key, value in values.model_dump(mode="json").items():
        setattr(product, key, value)
    if product.status != ProductResearchStatus.ARCHIVED.value:
        product.archived_at = None
    try:
        session.flush()
    except IntegrityError as exc:
        raise ValueError("同じ商品IDまたは既存商品リンクがすでに登録されています") from exc
    return product


def archive_research_product(session: Session, product_id: int) -> ResearchProduct:
    product = get_research_product(session, product_id)
    if product is None:
        raise ValueError("商品候補が見つかりません")
    product.status = ProductResearchStatus.ARCHIVED.value
    product.archived_at = utc_now()
    session.flush()
    return product


def save_offer(
    session: Session,
    product_id: int,
    values: OfferInput,
    *,
    offer_id: int | None = None,
) -> ResearchOffer:
    if get_research_product(session, product_id) is None:
        raise ValueError("商品候補が見つかりません")
    if offer_id is None:
        offer = ResearchOffer(product_id=product_id)
        session.add(offer)
    else:
        existing_offer = session.get(ResearchOffer, offer_id)
        if existing_offer is None or existing_offer.product_id != product_id:
            raise ValueError("販売先が見つかりません")
        offer = existing_offer

    payload = values.model_dump(mode="python")
    checked_at = payload.pop("terms_checked_at")
    for key, value in payload.items():
        setattr(offer, key, value)
    offer.terms_checked_at = to_utc(checked_at) if checked_at else None
    if offer.is_primary:
        session.execute(
            update(ResearchOffer)
            .where(
                ResearchOffer.product_id == product_id,
                ResearchOffer.id != (offer.id or -1),
            )
            .values(is_primary=False)
        )
    session.flush()
    return offer


def save_evidence(
    session: Session,
    product_id: int,
    values: EvidenceInput,
    *,
    evidence_id: int | None = None,
) -> ResearchEvidence:
    if get_research_product(session, product_id) is None:
        raise ValueError("商品候補が見つかりません")
    if evidence_id is None:
        evidence = ResearchEvidence(product_id=product_id)
        session.add(evidence)
    else:
        existing_evidence = session.get(ResearchEvidence, evidence_id)
        if existing_evidence is None or existing_evidence.product_id != product_id:
            raise ValueError("調査記録が見つかりません")
        evidence = existing_evidence
    payload = values.model_dump(mode="json")
    payload["checked_at"] = to_utc(values.checked_at)
    for key, value in payload.items():
        setattr(evidence, key, value)
    session.flush()
    return evidence


def import_legacy_product(session: Session, legacy_product_id: int) -> ResearchProduct:
    legacy = session.get(Product, legacy_product_id)
    if legacy is None:
        raise ValueError("既存の保存商品が見つかりません")
    existing = session.scalar(
        _product_query().where(ResearchProduct.legacy_product_id == legacy_product_id)
    )
    if existing is not None:
        return existing

    product = save_research_product(
        session,
        ProductCandidateInput(
            product_code=legacy.item_code,
            product_name=legacy.item_name,
            manufacturer="",
            model_number="",
            variant="",
            genre=legacy.genre_id,
            audience="",
            pain_point="",
            use_case="",
            status=ProductResearchStatus.CANDIDATE,
            legacy_product_id=legacy.id,
        ),
    )
    save_offer(
        session,
        product.id,
        OfferInput(
            seller_name=legacy.shop_name or "楽天市場の保存店舗",
            product_url=legacy.item_url,
            affiliate_url=legacy.affiliate_url,
            price=Decimal(legacy.item_price),
            shipping_fee=None,
            currency="JPY",
            commission_rate=float(legacy.affiliate_rate),
            commission_conditions="既存の楽天商品情報から取り込み。公開前に再確認してください。",
            terms_checked_at=legacy.fetched_at,
            is_primary=True,
        ),
    )
    return product


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def build_product_snapshot(product: ResearchProduct) -> dict[str, Any]:
    return {
        "snapshot_at": utc_now().isoformat(),
        "product": {
            "id": product.id,
            "product_code": product.product_code,
            "product_name": product.product_name,
            "manufacturer": product.manufacturer,
            "model_number": product.model_number,
            "variant": product.variant,
            "genre": product.genre,
            "audience": product.audience,
            "pain_point": product.pain_point,
            "use_case": product.use_case,
            "status": product.status,
            "legacy_product_id": product.legacy_product_id,
        },
        "offers": [
            {
                key: _json_value(getattr(offer, key))
                for key in (
                    "id",
                    "seller_name",
                    "product_url",
                    "affiliate_url",
                    "price",
                    "shipping_fee",
                    "currency",
                    "price_tax_status",
                    "commission_rate",
                    "commission_cap",
                    "commission_conditions",
                    "commission_base_notes",
                    "terms_checked_at",
                    "is_primary",
                )
            }
            for offer in product.offers
        ],
        "evidence": [
            {
                key: _json_value(getattr(item, key))
                for key in (
                    "id",
                    "summary",
                    "source_url",
                    "source_type",
                    "verification_status",
                    "checked_at",
                    "actually_used",
                    "use_conditions",
                    "observations",
                )
            }
            for item in product.evidence
        ],
    }


def _plan_query() -> Select[tuple[ResearchPlan]]:
    return select(ResearchPlan).options(
        selectinload(ResearchPlan.product_links).selectinload(ResearchPlanProduct.product),
        selectinload(ResearchPlan.revisions),
        selectinload(ResearchPlan.briefs).selectinload(ResearchArticleBrief.content_links),
    )


def list_research_plans(session: Session) -> list[ResearchPlan]:
    return list(session.scalars(_plan_query().order_by(ResearchPlan.updated_at.desc())).unique())


def get_research_plan(session: Session, plan_id: int) -> ResearchPlan | None:
    return session.scalar(_plan_query().where(ResearchPlan.id == plan_id))


def save_research_plan(
    session: Session,
    values: ResearchPlanInput,
    product_values: dict[int, dict[str, str]],
    *,
    plan_id: int | None = None,
) -> ResearchPlan:
    product_ids = list(dict.fromkeys(int(product_id) for product_id in product_values))
    if len(product_ids) < 2:
        raise ValueError("比較企画には2商品以上を選択してください")
    products = list(
        session.scalars(
            _product_query().where(
                ResearchProduct.id.in_(product_ids),
                ResearchProduct.status != ProductResearchStatus.ARCHIVED.value,
            ).execution_options(populate_existing=True)
        ).unique()
    )
    if len(products) != len(product_ids):
        raise ValueError("選択した商品候補の一部が見つからないか、アーカイブされています")

    if plan_id is None:
        plan = ResearchPlan(plan_code=f"PLAN-{uuid4().hex[:12].upper()}")
        session.add(plan)
    else:
        existing_plan = get_research_plan(session, plan_id)
        if existing_plan is None:
            raise ValueError("比較企画が見つかりません")
        plan = existing_plan

    payload = values.model_dump(mode="json")
    axes = payload.pop("comparison_axes")
    evaluation = payload.pop("evaluation")
    for key, value in payload.items():
        setattr(plan, key, value)
    plan.comparison_axes_json = axes
    plan.evaluation_json = evaluation
    plan.version = int(plan.version or 0) + 1
    session.flush()

    session.execute(delete(ResearchPlanProduct).where(ResearchPlanProduct.plan_id == plan.id))
    snapshots: list[dict[str, Any]] = []
    by_id = {product.id: product for product in products}
    for product_id in product_ids:
        snapshot = build_product_snapshot(by_id[product_id])
        comparison_values = {
            str(key): str(value).strip()
            for key, value in product_values[product_id].items()
            if str(key).strip()
        }
        session.add(
            ResearchPlanProduct(
                plan_id=plan.id,
                product_id=product_id,
                product_snapshot_json=snapshot,
                comparison_values_json=comparison_values,
            )
        )
        snapshots.append(
            {"product_snapshot": snapshot, "comparison_values": comparison_values}
        )

    revision_snapshot = {
        "plan_code": plan.plan_code,
        "version": plan.version,
        "title": plan.title,
        "audience": plan.audience,
        "pain_point": plan.pain_point,
        "article_purpose": plan.article_purpose,
        "comparison_axes": list(plan.comparison_axes_json),
        "evaluation": dict(plan.evaluation_json),
        "adoption_reason": plan.adoption_reason,
        "weaknesses": plan.weaknesses,
        "additional_checks": plan.additional_checks,
        "status": plan.status,
        "products": snapshots,
        "saved_at": utc_now().isoformat(),
    }
    session.add(
        ResearchPlanRevision(
            plan_id=plan.id,
            version=plan.version,
            snapshot_json=revision_snapshot,
        )
    )
    session.flush()
    return plan


def list_article_briefs(
    session: Session, *, plan_id: int | None = None
) -> list[ResearchArticleBrief]:
    query = select(ResearchArticleBrief).options(
        selectinload(ResearchArticleBrief.content_links)
    )
    if plan_id is not None:
        query = query.where(ResearchArticleBrief.plan_id == plan_id)
    query = query.order_by(ResearchArticleBrief.created_at.desc())
    return list(session.scalars(query).unique())


def save_article_brief(
    session: Session,
    *,
    plan_id: int,
    title: str,
    markdown: str,
    brief_json: dict[str, Any],
    disclosure_policy: str,
) -> ResearchArticleBrief:
    if get_research_plan(session, plan_id) is None:
        raise ValueError("比較企画が見つかりません")
    latest = session.scalar(
        select(func.max(ResearchArticleBrief.version)).where(
            ResearchArticleBrief.plan_id == plan_id
        )
    )
    brief = ResearchArticleBrief(
        brief_code=f"BRIEF-{uuid4().hex[:12].upper()}",
        plan_id=plan_id,
        version=int(latest or 0) + 1,
        title=title.strip(),
        markdown=markdown,
        brief_json=brief_json,
        disclosure_policy=disclosure_policy.strip(),
    )
    session.add(brief)
    session.flush()
    return brief


def get_article_brief(session: Session, brief_id: int) -> ResearchArticleBrief | None:
    return session.scalar(
        select(ResearchArticleBrief)
        .options(
            selectinload(ResearchArticleBrief.plan).selectinload(
                ResearchPlan.product_links
            ),
            selectinload(ResearchArticleBrief.content_links),
        )
        .where(ResearchArticleBrief.id == brief_id)
    )


def save_published_article_reference(
    session: Session,
    brief_id: int,
    *,
    url: str,
    title: str,
    summary: str,
    match_confirmed: bool,
) -> ResearchArticleBrief:
    from urllib.parse import urlsplit

    brief = get_article_brief(session, brief_id)
    if brief is None:
        raise ValueError("記事作成用ブリーフが見つかりません")
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"note.com", "www.note.com"}:
        raise ValueError("公開済みnote記事のHTTPS URLを入力してください")
    if not title.strip() or not summary.strip():
        raise ValueError("公開記事のタイトルと要約を入力してください")
    if not match_confirmed:
        raise ValueError("企画と公開記事の内容が一致することを確認してください")
    brief.published_article_url = url.strip()
    brief.published_article_title = title.strip()
    brief.published_article_summary = summary.strip()
    brief.published_match_confirmed_at = utc_now()
    session.flush()
    return brief


def link_brief_to_content(
    session: Session,
    *,
    brief_id: int,
    content_id: int,
    channel: str,
) -> ResearchContentLink:
    if get_article_brief(session, brief_id) is None:
        raise ValueError("記事作成用ブリーフが見つかりません")
    if session.get(Content, content_id) is None:
        raise ValueError("制作コンテンツが見つかりません")
    existing = session.scalar(
        select(ResearchContentLink).where(
            ResearchContentLink.brief_id == brief_id,
            ResearchContentLink.content_id == content_id,
        )
    )
    if existing is not None:
        return existing
    link = ResearchContentLink(
        brief_id=brief_id,
        content_id=content_id,
        channel=channel,
        link_metadata_json={"linked_at": utc_now().isoformat()},
    )
    session.add(link)
    session.flush()
    return link


def list_content_research_links(
    session: Session, content_id: int
) -> list[ResearchContentLink]:
    query = (
        select(ResearchContentLink)
        .options(
            selectinload(ResearchContentLink.brief).selectinload(
                ResearchArticleBrief.plan
            )
        )
        .where(ResearchContentLink.content_id == content_id)
        .order_by(ResearchContentLink.created_at)
    )
    return list(session.scalars(query).unique())
