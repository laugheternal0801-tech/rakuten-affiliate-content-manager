from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, utcnow


class MIResearchRun(Base):
    __tablename__ = "mi_research_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(300), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    capability_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MISourceQuery(Base):
    __tablename__ = "mi_source_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("mi_research_runs.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    query: Mapped[str] = mapped_column(Text)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MIRawSocialItem(Base):
    __tablename__ = "mi_raw_social_items"

    raw_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("mi_research_runs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(300), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    query: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    data_mode: Mapped[str] = mapped_column(String(32))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class MINormalizedSocialItem(Base):
    __tablename__ = "mi_normalized_social_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_id: Mapped[str] = mapped_column(ForeignKey("mi_raw_social_items.raw_id"), index=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("mi_research_runs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(300), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str | None] = mapped_column(String(300))
    author_name: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(32), index=True)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    query: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(String(300), index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    data_mode: Mapped[str] = mapped_column(String(32))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    spam_score: Mapped[float] = mapped_column(Float, default=0)
    bot_score: Mapped[float] = mapped_column(Float, default=0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0)
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    duplicate_group: Mapped[str | None] = mapped_column(String(64), index=True)
    advertisement_likelihood: Mapped[float] = mapped_column(Float, default=0)


class MIEvidence(Base):
    __tablename__ = "mi_evidence"

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("mi_research_runs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source_item_ids: Mapped[list[str]] = mapped_column(JSON)
    claim: Mapped[str] = mapped_column(Text)
    sample_size: Mapped[int] = mapped_column(Integer)
    query: Mapped[str] = mapped_column(Text)
    date_from: Mapped[str] = mapped_column(String(10))
    date_to: Mapped[str] = mapped_column(String(10))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    support_score: Mapped[float] = mapped_column(Float)
    counter_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MIAgentOutput(Base):
    __tablename__ = "mi_agent_outputs"

    agent_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("mi_research_runs.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(100))
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MIClaim(Base):
    __tablename__ = "mi_claims"

    claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("mi_research_runs.id"), index=True)
    claim: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float] = mapped_column(Float, index=True)
    confidence_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    counter_evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    verification: Mapped[str] = mapped_column(String(32), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))


class MIReport(Base):
    __tablename__ = "mi_reports"

    research_run_id: Mapped[str] = mapped_column(
        ForeignKey("mi_research_runs.id"), primary_key=True
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
