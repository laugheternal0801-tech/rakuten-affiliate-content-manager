from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now().astimezone()


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_code: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    item_name: Mapped[str] = mapped_column(String(1000))
    catchcopy: Mapped[str] = mapped_column(Text, default="")
    item_price: Mapped[int] = mapped_column(Integer, default=0)
    item_caption: Mapped[str] = mapped_column(Text, default="")
    affiliate_url: Mapped[str] = mapped_column(Text, default="")
    item_url: Mapped[str] = mapped_column(Text, default="")
    affiliate_rate: Mapped[float] = mapped_column(Float, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    review_average: Mapped[float] = mapped_column(Float, default=0)
    postage_flag: Mapped[int] = mapped_column(Integer, default=1)
    availability: Mapped[int] = mapped_column(Integer, default=1)
    point_rate: Mapped[float] = mapped_column(Float, default=1)
    sale_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sale_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_url: Mapped[str] = mapped_column(Text, default="")
    image_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    shop_name: Mapped[str] = mapped_column(String(500), default="")
    shop_code: Mapped[str] = mapped_column(String(255), default="")
    shop_url: Mapped[str] = mapped_column(Text, default="")
    shop_affiliate_url: Mapped[str] = mapped_column(Text, default="")
    genre_id: Mapped[str] = mapped_column(String(64), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    score: Mapped[float] = mapped_column(Float, default=0)
    score_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    experience: Mapped[Experience | None] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )
    contents: Mapped[list[Content]] = relationship(
        secondary="content_products", back_populates="products"
    )


class Experience(Base):
    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True)
    owns_product: Mapped[bool | None] = mapped_column(Boolean)
    has_used: Mapped[bool | None] = mapped_column(Boolean)
    usage_period: Mapped[str] = mapped_column(String(255), default="")
    usage_scene: Mapped[str] = mapped_column(Text, default="")
    positive_points: Mapped[str] = mapped_column(Text, default="")
    negative_points: Mapped[str] = mapped_column(Text, default="")
    suitable_for: Mapped[str] = mapped_column(Text, default="")
    unsuitable_for: Mapped[str] = mapped_column(Text, default="")
    compared_products: Mapped[str] = mapped_column(Text, default="")
    memo: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[date | None] = mapped_column(Date)

    product: Mapped[Product] = relationship(back_populates="experience")


class ContentProduct(Base):
    __tablename__ = "content_products"
    __table_args__ = (UniqueConstraint("content_id", "product_id"),)

    content_id: Mapped[int] = mapped_column(ForeignKey("contents.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)


class Content(Base):
    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(50), index=True)
    theme: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(1000), default="")
    draft_body: Mapped[str] = mapped_column(Text, default="")
    approved_body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="drafting", index=True)
    affiliate_url: Mapped[str] = mapped_column(Text, default="")
    pr_required: Mapped[bool] = mapped_column(Boolean, default=False)
    compliance_status: Mapped[str] = mapped_column(String(50), default="要確認")
    compliance_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewer: Mapped[str] = mapped_column(String(255), default="")
    info_verified_at: Mapped[date | None] = mapped_column(Date)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_url: Mapped[str] = mapped_column(Text, default="")
    work_minutes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    products: Mapped[list[Product]] = relationship(
        secondary="content_products", back_populates="contents"
    )


class Performance(Base):
    __tablename__ = "performances"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    channel: Mapped[str] = mapped_column(String(100), default="")
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    content_id: Mapped[int | None] = mapped_column(ForeignKey("contents.id"))
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    sales: Mapped[float] = mapped_column(Float, default=0)
    reward: Mapped[float] = mapped_column(Float, default=0)
    work_minutes: Mapped[int] = mapped_column(Integer, default=0)
    product_name: Mapped[str] = mapped_column(String(1000), default="")
    shop_name: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(100), default="")
    theme: Mapped[str] = mapped_column(String(255), default="")
    source_file: Mapped[str] = mapped_column(String(255), default="")


class NoteImageAsset(Base):
    __tablename__ = "note_image_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(64), index=True)
    article_title: Mapped[str] = mapped_column(String(1000), default="")
    theme: Mapped[str] = mapped_column(String(1000), default="")
    motifs: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(100), default="gpt-image-2")
    image_data: Mapped[bytes] = mapped_column(LargeBinary)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
