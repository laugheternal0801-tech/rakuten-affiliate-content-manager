from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utcnow


class CreativeCampaignRow(Base):
    __tablename__ = "creative_campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    quality_level: Mapped[str] = mapped_column(String(32), index=True)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    degradation_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CreativeBriefRow(Base):
    __tablename__ = "creative_content_briefs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    research_run_id: Mapped[str] = mapped_column(String(64), index=True)
    brief_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreativeJobRow(Base):
    __tablename__ = "creative_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(100))
    progress: Mapped[float] = mapped_column(Float, default=0)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CreativeCandidateRow(Base):
    __tablename__ = "creative_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    content_brief_id: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200))
    revision_round: Mapped[int] = mapped_column(Integer, default=0)
    candidate_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreativeEvaluationRow(Base):
    __tablename__ = "creative_evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    evaluator: Mapped[str] = mapped_column(String(100), index=True)
    evaluation_type: Mapped[str] = mapped_column(String(32), index=True)
    overall_score: Mapped[float | None] = mapped_column(Float)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreativeAssetRow(Base):
    __tablename__ = "creative_assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    content_brief_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200))
    file_path: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(100))
    parent_asset_id: Mapped[str | None] = mapped_column(String(64), index=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=0)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    asset_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreativePackageRow(Base):
    __tablename__ = "creative_content_packages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    degraded_quality_mode: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    package_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CreativeApprovalRow(Base):
    __tablename__ = "creative_approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("creative_content_packages.id"), index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))
    feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreativeCostRow(Base):
    __tablename__ = "creative_costs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    content_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(100), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreativePerformanceRow(Base):
    __tablename__ = "creative_content_performance"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("creative_content_packages.id"), index=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
