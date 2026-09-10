from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.research_catalog.models import ResearchOffer, ResearchProduct

QualitySeverity = Literal["blocking", "warning"]


@dataclass(frozen=True, slots=True)
class ResearchQualityIssue:
    code: str
    message: str
    severity: QualitySeverity


@dataclass(frozen=True, slots=True)
class ProductQualityReport:
    product_id: int
    latest_terms_checked_at: datetime | None
    issues: tuple[ResearchQualityIssue, ...]

    @property
    def blocking_issues(self) -> tuple[ResearchQualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "blocking")

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)

    @property
    def label(self) -> str:
        if self.blocking_issues:
            return "要確認"
        if self.issues:
            return "注意あり"
        return "確認済み"


def primary_offer(product: ResearchProduct) -> ResearchOffer | None:
    return next((offer for offer in product.offers if offer.is_primary), None) or (
        product.offers[0] if product.offers else None
    )


def assess_product_quality(
    product: ResearchProduct,
    *,
    freshness_days: int = 30,
    now: datetime | None = None,
) -> ProductQualityReport:
    current = _as_utc(now or datetime.now(UTC))
    freshness_cutoff = current - timedelta(days=max(1, freshness_days))
    issues: list[ResearchQualityIssue] = []

    if not product.audience.strip():
        issues.append(_warning("audience_missing", "想定読者が未設定"))
    if not product.pain_point.strip():
        issues.append(_warning("pain_point_missing", "読者の悩みが未設定"))
    if not product.use_case.strip():
        issues.append(_warning("use_case_missing", "利用場面が未設定"))
    if not product.model_number.strip() and not product.variant.strip():
        issues.append(_warning("variant_identity_missing", "型番・サイズが未確認"))

    offer = primary_offer(product)
    checked_values = [
        _as_utc(item.terms_checked_at)
        for item in product.offers
        if item.terms_checked_at is not None
    ]
    latest_checked_at = max(checked_values) if checked_values else None
    if offer is None:
        issues.append(_blocking("offer_missing", "販売先が未登録"))
    else:
        if not offer.affiliate_url.strip():
            issues.append(_blocking("affiliate_url_missing", "アフィリエイトURLが未登録"))
        if offer.terms_checked_at is None:
            issues.append(_blocking("terms_unchecked", "価格・報酬条件の確認日時が未登録"))
        elif _as_utc(offer.terms_checked_at) < freshness_cutoff:
            issues.append(
                _blocking(
                    "terms_stale",
                    f"価格・報酬条件が{max(1, freshness_days)}日より前の情報",
                )
            )
        if offer.price is None:
            issues.append(_warning("price_unknown", "価格が未確認"))
        if offer.shipping_fee is None:
            issues.append(_warning("shipping_unknown", "送料が未確認"))
        if offer.commission_rate is None:
            issues.append(_warning("commission_unknown", "報酬率が未確認"))
        if offer.price_tax_status == "unknown":
            issues.append(_warning("tax_status_unknown", "税込・税抜が不明"))
        if not offer.commission_base_notes.strip():
            issues.append(_warning("commission_base_unknown", "報酬対象額が不明"))

    confirmed = [
        item for item in product.evidence if item.verification_status == "confirmed_fact"
    ]
    if not product.evidence:
        issues.append(_blocking("evidence_missing", "調査根拠が未登録"))
    elif not confirmed:
        issues.append(_blocking("confirmed_evidence_missing", "確認済みの事実が未登録"))

    return ProductQualityReport(
        product_id=product.id,
        latest_terms_checked_at=latest_checked_at,
        issues=tuple(issues),
    )


def plan_ready_blockers(
    products: list[ResearchProduct],
    comparison_axes: list[str],
    product_values: dict[int, dict[str, str]],
    evaluation: dict[str, dict[str, str]],
    *,
    freshness_days: int = 30,
    now: datetime | None = None,
) -> list[str]:
    blockers: list[str] = []
    if len(products) < 2:
        blockers.append("比較商品を2点以上選択")
    if not comparison_axes:
        blockers.append("比較軸を1項目以上入力")

    for product in products:
        report = assess_product_quality(
            product,
            freshness_days=freshness_days,
            now=now,
        )
        blockers.extend(
            f"{product.product_name}: {issue.message}" for issue in report.blocking_issues
        )
        values = product_values.get(product.id, {})
        missing_axes = [axis for axis in comparison_axes if not str(values.get(axis, "")).strip()]
        if missing_axes:
            blockers.append(
                f"{product.product_name}: 比較値が未入力（{', '.join(missing_axes)}）"
            )

    for criterion, values in evaluation.items():
        if not str(values.get("memo", "")).strip() or not str(
            values.get("basis", "")
        ).strip():
            blockers.append(f"{criterion}: 評価メモと判断根拠が未入力")
    return blockers


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _blocking(code: str, message: str) -> ResearchQualityIssue:
    return ResearchQualityIssue(code=code, message=message, severity="blocking")


def _warning(code: str, message: str) -> ResearchQualityIssue:
    return ResearchQualityIssue(code=code, message=message, severity="warning")
