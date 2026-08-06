from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator


class SearchCriteria(BaseModel):
    keyword: str = Field(default="", max_length=128)
    genre_id: str = Field(default="", pattern=r"^\d*$")
    min_price: int | None = Field(default=None, gt=0, lt=999_999_999)
    max_price: int | None = Field(default=None, gt=0, lt=999_999_999)
    min_review_count: int = Field(default=0, ge=0)
    min_review_average: float = Field(default=0, ge=0, le=5)
    min_affiliate_rate: float = Field(default=0, ge=0, le=100)
    free_shipping_only: bool = False
    available_only: bool = True
    image_only: bool = True
    excluded_keywords: list[str] = Field(default_factory=list)
    sort: str = "standard"
    hits: int = Field(default=20, ge=1, le=30)

    @model_validator(mode="after")
    def validate_search(self) -> SearchCriteria:
        if not self.keyword.strip() and not self.genre_id:
            raise ValueError("検索キーワードまたはジャンルIDを入力してください。")
        if self.min_price and self.max_price and self.max_price < self.min_price:
            raise ValueError("最高価格は最低価格以上にしてください。")
        return self


class ExperienceInput(BaseModel):
    owns_product: bool | None = None
    has_used: bool | None = None
    usage_period: str = Field(default="", max_length=255)
    usage_scene: str = Field(default="", max_length=5000)
    positive_points: str = Field(default="", max_length=5000)
    negative_points: str = Field(default="", max_length=5000)
    suitable_for: str = Field(default="", max_length=5000)
    unsuitable_for: str = Field(default="", max_length=5000)
    compared_products: str = Field(default="", max_length=5000)
    memo: str = Field(default="", max_length=10000)
    verified_at: date | None = None


class PublishInput(BaseModel):
    reviewer: str = Field(min_length=1, max_length=255)
    published_url: HttpUrl | None = None
    confirmed: bool


class ScoreWeights(BaseModel):
    affiliate_rate: float = Field(default=25, ge=0, le=100)
    review_count: float = Field(default=20, ge=0, le=100)
    review_average: float = Field(default=15, ge=0, le=100)
    price_fit: float = Field(default=15, ge=0, le=100)
    free_shipping: float = Field(default=10, ge=0, le=100)
    keyword_match: float = Field(default=15, ge=0, le=100)

    @model_validator(mode="after")
    def total_is_positive(self) -> ScoreWeights:
        if self.total <= 0:
            raise ValueError("配点合計は1以上にしてください。")
        return self

    @property
    def total(self) -> float:
        return sum(
            [
                self.affiliate_rate,
                self.review_count,
                self.review_average,
                self.price_fit,
                self.free_shipping,
                self.keyword_match,
            ]
        )


class GeneratedContent(BaseModel):
    channel: str
    title: str
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)
