from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.research_catalog.time import utc_now


class ResearchSchemaVersion(Base):
    __tablename__ = "research_schema_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchProduct(Base):
    __tablename__ = "research_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_code: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    product_name: Mapped[str] = mapped_column(String(1000), index=True)
    manufacturer: Mapped[str] = mapped_column(String(500), default="")
    model_number: Mapped[str] = mapped_column(String(500), default="")
    variant: Mapped[str] = mapped_column(String(500), default="")
    genre: Mapped[str] = mapped_column(String(500), default="", index=True)
    audience: Mapped[str] = mapped_column(Text, default="")
    pain_point: Mapped[str] = mapped_column(Text, default="")
    use_case: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="candidate", index=True)
    legacy_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), unique=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    offers: Mapped[list[ResearchOffer]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ResearchOffer.id"
    )
    evidence: Mapped[list[ResearchEvidence]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ResearchEvidence.id"
    )
    plan_links: Mapped[list[ResearchPlanProduct]] = relationship(back_populates="product")


class ResearchOffer(Base):
    __tablename__ = "research_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("research_products.id", ondelete="CASCADE"), index=True
    )
    seller_name: Mapped[str] = mapped_column(String(500))
    product_url: Mapped[str] = mapped_column(Text, default="")
    affiliate_url: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="JPY")
    price_tax_status: Mapped[str] = mapped_column(String(50), default="unknown")
    commission_rate: Mapped[float | None] = mapped_column(Float)
    commission_cap: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    commission_conditions: Mapped[str] = mapped_column(Text, default="")
    commission_base_notes: Mapped[str] = mapped_column(Text, default="")
    terms_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    product: Mapped[ResearchProduct] = relationship(back_populates="offers")


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("research_products.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    verification_status: Mapped[str] = mapped_column(String(50), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actually_used: Mapped[bool] = mapped_column(Boolean, default=False)
    use_conditions: Mapped[str] = mapped_column(Text, default="")
    observations: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    product: Mapped[ResearchProduct] = relationship(back_populates="evidence")


class ResearchPlan(Base):
    __tablename__ = "research_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1000))
    audience: Mapped[str] = mapped_column(Text)
    pain_point: Mapped[str] = mapped_column(Text)
    article_purpose: Mapped[str] = mapped_column(Text, default="")
    comparison_axes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    adoption_reason: Mapped[str] = mapped_column(Text, default="")
    weaknesses: Mapped[str] = mapped_column(Text, default="")
    additional_checks: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    product_links: Mapped[list[ResearchPlanProduct]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="ResearchPlanProduct.id"
    )
    revisions: Mapped[list[ResearchPlanRevision]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="ResearchPlanRevision.version"
    )
    briefs: Mapped[list[ResearchArticleBrief]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="ResearchArticleBrief.version"
    )


class ResearchPlanProduct(Base):
    __tablename__ = "research_plan_products"
    __table_args__ = (UniqueConstraint("plan_id", "product_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("research_plans.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("research_products.id", ondelete="RESTRICT"), index=True
    )
    product_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    comparison_values_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    plan: Mapped[ResearchPlan] = relationship(back_populates="product_links")
    product: Mapped[ResearchProduct] = relationship(back_populates="plan_links")


class ResearchPlanRevision(Base):
    __tablename__ = "research_plan_revisions"
    __table_args__ = (UniqueConstraint("plan_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("research_plans.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plan: Mapped[ResearchPlan] = relationship(back_populates="revisions")


class ResearchArticleBrief(Base):
    __tablename__ = "research_article_briefs"
    __table_args__ = (UniqueConstraint("plan_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("research_plans.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(1000))
    markdown: Mapped[str] = mapped_column(Text)
    brief_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    disclosure_policy: Mapped[str] = mapped_column(Text)
    published_article_url: Mapped[str] = mapped_column(Text, default="")
    published_article_title: Mapped[str] = mapped_column(Text, default="")
    published_article_summary: Mapped[str] = mapped_column(Text, default="")
    published_match_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    plan: Mapped[ResearchPlan] = relationship(back_populates="briefs")
    content_links: Mapped[list[ResearchContentLink]] = relationship(
        back_populates="brief", cascade="all, delete-orphan"
    )


class ResearchContentLink(Base):
    __tablename__ = "research_content_links"
    __table_args__ = (UniqueConstraint("brief_id", "content_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_id: Mapped[int] = mapped_column(
        ForeignKey("research_article_briefs.id", ondelete="CASCADE"), index=True
    )
    content_id: Mapped[int] = mapped_column(ForeignKey("contents.id"), index=True)
    channel: Mapped[str] = mapped_column(String(50))
    link_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    brief: Mapped[ResearchArticleBrief] = relationship(back_populates="content_links")
