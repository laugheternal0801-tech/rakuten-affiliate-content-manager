from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utcnow


class OSRunRow(Base):
    __tablename__ = "os_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    research_mode: Mapped[str] = mapped_column(String(16), index=True)
    objective: Mapped[str] = mapped_column(Text)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    route_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    actual_estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    search_calls: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    stopped_early: Mapped[bool] = mapped_column(Boolean, default=False)
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OSModelCallRow(Base):
    __tablename__ = "os_model_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("os_runs.id"), index=True)
    task: Mapped[str] = mapped_column(String(100), index=True)
    agent: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, index=True)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str] = mapped_column(String(200), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OSDecisionRow(Base):
    __tablename__ = "os_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("os_runs.id"), unique=True, index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    opportunity_score: Mapped[float] = mapped_column(Float, index=True)
    confidence: Mapped[float] = mapped_column(Float, index=True)
    evidence_score: Mapped[float] = mapped_column(Float)
    estimated_value: Mapped[float | None] = mapped_column(Float)
    ai_cost: Mapped[float] = mapped_column(Float, default=0)
    approval_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    approval_note: Mapped[str] = mapped_column(Text, default="")
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OSCacheRow(Base):
    __tablename__ = "os_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(500), default="")
    response_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OSOutcomeRow(Base):
    """Revenue feedback bridge across market, content, distribution, and decision runs."""

    __tablename__ = "os_outcomes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("os_runs.id"), index=True)
    research_run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    content_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    publication_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    product_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    market: Mapped[str] = mapped_column(String(500), default="", index=True)
    platform: Mapped[str] = mapped_column(String(64), default="", index=True)
    angle: Mapped[str] = mapped_column(String(500), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0)
    commission: Mapped[float] = mapped_column(Float, default=0)
    content_cost: Mapped[float] = mapped_column(Float, default=0)
    ai_cost: Mapped[float] = mapped_column(Float, default=0)
    profit: Mapped[float] = mapped_column(Float, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OSCostLedgerRow(Base):
    """Additive cost ledger so existing installations do not need ALTER TABLE."""

    __tablename__ = "os_cost_ledger"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("os_runs.id"), index=True)
    decision_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task: Mapped[str] = mapped_column(String(100), index=True)
    agent: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str] = mapped_column(String(200), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, index=True)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OSPredictionRow(Base):
    __tablename__ = "os_predictions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("os_decisions.id"), index=True)
    metric: Mapped[str] = mapped_column(String(200), index=True)
    predicted_value: Mapped[float | None] = mapped_column(Float)
    predicted_range_json: Mapped[list[float] | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    actual_value: Mapped[float | None] = mapped_column(Float)
    error: Mapped[float | None] = mapped_column(Float)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OSDecisionOutcomeRow(Base):
    __tablename__ = "os_decision_outcomes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("os_decisions.id"), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("os_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0)
    commission: Mapped[float] = mapped_column(Float, default=0)
    gross_profit: Mapped[float] = mapped_column(Float, default=0)
    ai_cost: Mapped[float] = mapped_column(Float, default=0)
    content_cost: Mapped[float] = mapped_column(Float, default=0)
    ad_cost: Mapped[float] = mapped_column(Float, default=0)
    platform_cost: Mapped[float] = mapped_column(Float, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0)
    net_profit: Mapped[float] = mapped_column(Float, default=0)
    decision_roi: Mapped[float | None] = mapped_column(Float)
    error_causes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    actual_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lessons_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OSMemoryRow(Base):
    """Versioned business memory for incremental research and outcome learning."""

    __tablename__ = "os_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_type: Mapped[str] = mapped_column(String(32), index=True)
    memory_key: Mapped[str] = mapped_column(String(255), index=True)
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
