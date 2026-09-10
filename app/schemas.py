from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator


class SearchCriteria(BaseModel):
    keyword: str = Field(default="", max_length=128)
    free_shipping_only: bool = False
    available_only: bool = True
    image_only: bool = True
    sort: str = "standard"
    hits: int = Field(default=20, ge=1, le=30)

    @model_validator(mode="after")
    def validate_search(self) -> SearchCriteria:
        if not self.keyword.strip():
            raise ValueError("検索キーワードを入力してください。")
        return self


class PublishInput(BaseModel):
    reviewer: str = Field(min_length=1, max_length=255)
    published_url: HttpUrl | None = None
    confirmed: bool


class ScoreWeights(BaseModel):
    affiliate_rate: float = Field(default=25, ge=0, le=100)
    review_count: float = Field(default=20, ge=0, le=100)
    review_average: float = Field(default=15, ge=0, le=100)
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
                self.free_shipping,
                self.keyword_match,
            ]
        )


class GeneratedContent(BaseModel):
    channel: str
    title: str
    body: str
    metadata: dict[str, Any] = Field(default_factory=dict)
