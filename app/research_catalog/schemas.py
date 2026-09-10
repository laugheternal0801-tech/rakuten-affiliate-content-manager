from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductResearchStatus(StrEnum):
    CANDIDATE = "candidate"
    RESEARCHING = "researching"
    COMPARABLE = "comparable"
    ON_HOLD = "on_hold"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    MANUFACTURER = "manufacturer"
    RETAILER = "retailer"
    THIRD_PARTY = "third_party"
    OWN_MEASUREMENT = "own_measurement"


class VerificationStatus(StrEnum):
    CONFIRMED_FACT = "confirmed_fact"
    HYPOTHESIS = "hypothesis"
    UNVERIFIED = "unverified"


class PriceTaxStatus(StrEnum):
    UNKNOWN = "unknown"
    TAX_INCLUDED = "tax_included"
    TAX_EXCLUDED = "tax_excluded"


class ResearchPlanStatus(StrEnum):
    DRAFT = "draft"
    RESEARCHING = "researching"
    READY = "ready"
    ON_HOLD = "on_hold"
    ARCHIVED = "archived"


def _validate_optional_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URLはhttp://またはhttps://から入力してください")
    return cleaned


class ProductCandidateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_code: str = Field(min_length=1, max_length=255)
    product_name: str = Field(min_length=1, max_length=1000)
    manufacturer: str = Field(default="", max_length=500)
    model_number: str = Field(default="", max_length=500)
    variant: str = Field(default="", max_length=500)
    genre: str = Field(default="", max_length=500)
    audience: str = Field(default="", max_length=5000)
    pain_point: str = Field(default="", max_length=5000)
    use_case: str = Field(default="", max_length=5000)
    status: ProductResearchStatus = ProductResearchStatus.CANDIDATE
    legacy_product_id: int | None = None


class OfferInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    seller_name: str = Field(min_length=1, max_length=500)
    product_url: str = ""
    affiliate_url: str = ""
    price: Decimal | None = Field(default=None, ge=0)
    shipping_fee: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="JPY", min_length=3, max_length=3)
    price_tax_status: PriceTaxStatus = PriceTaxStatus.UNKNOWN
    commission_rate: float | None = Field(default=None, ge=0, le=100)
    commission_cap: Decimal | None = Field(default=None, ge=0)
    commission_conditions: str = Field(default="", max_length=5000)
    commission_base_notes: str = Field(default="", max_length=5000)
    terms_checked_at: datetime | None = None
    is_primary: bool = False

    @field_validator("product_url", "affiliate_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_optional_url(value)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class EvidenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=10_000)
    source_url: str = ""
    source_type: SourceType
    verification_status: VerificationStatus
    checked_at: datetime
    actually_used: bool = False
    use_conditions: str = Field(default="", max_length=5000)
    observations: str = Field(default="", max_length=10_000)

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_optional_url(value)

    @model_validator(mode="after")
    def validate_source_and_usage(self) -> EvidenceInput:
        if self.source_type is not SourceType.OWN_MEASUREMENT and not self.source_url:
            raise ValueError("メーカー・販売店・第三者情報には出典URLが必要です")
        if self.actually_used and not self.observations:
            raise ValueError("実際に使用した場合は観察内容を入力してください")
        return self


class ResearchPlanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=1000)
    audience: str = Field(min_length=1, max_length=5000)
    pain_point: str = Field(min_length=1, max_length=5000)
    article_purpose: str = Field(default="", max_length=5000)
    comparison_axes: list[str] = Field(default_factory=list, max_length=30)
    evaluation: dict[str, dict[str, str]] = Field(default_factory=dict)
    adoption_reason: str = Field(default="", max_length=10_000)
    weaknesses: str = Field(default="", max_length=10_000)
    additional_checks: str = Field(default="", max_length=10_000)
    status: ResearchPlanStatus = ResearchPlanStatus.DRAFT

    @field_validator("comparison_axes")
    @classmethod
    def normalize_axes(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized
