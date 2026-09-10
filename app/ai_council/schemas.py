from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.services.ai_council import REQUIRED_COUNCIL_PROVIDERS, STAGE_ORDER


class CouncilJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CouncilBriefData(BaseModel):
    objective: str = Field(min_length=1, max_length=20_000)
    background: str = Field(default="", max_length=40_000)
    constraints: str = Field(default="", max_length=20_000)
    expected_output: str = Field(default="意思決定と次のアクションが分かる結論", max_length=5_000)


class CouncilJobSpec(BaseModel):
    run_id: str = Field(pattern=r"^council-[A-Za-z0-9T-]+$")
    brief: CouncilBriefData
    workflow_version: str = "independent_v2"
    participant_providers: tuple[str, ...] = ()
    stage_providers: dict[str, str] = Field(default_factory=dict)
    reviewer_providers: tuple[str, ...] = ()
    integrator_provider: str = Field(min_length=1, max_length=50)
    provider_models: dict[str, str]
    debate_rounds: int = Field(default=2, ge=1, le=3)
    max_output_tokens: int = Field(default=16_000, ge=256, le=16_000)
    enable_openai_web_search: bool = True

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_workflow(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "workflow_version" not in value
            and value.get("stage_providers")
        ):
            return {**value, "workflow_version": "legacy_v1"}
        return value

    @model_validator(mode="after")
    def validate_assignments(self) -> CouncilJobSpec:
        all_configured = {
            *self.participant_providers,
            *self.stage_providers.values(),
            *self.reviewer_providers,
            self.integrator_provider,
            *self.provider_models,
        }
        if "demo" in all_configured:
            raise ValueError("AI会議ではデモプロバイダーを使用できません。")
        if self.workflow_version == "legacy_v1":
            required_stages = {stage.value for stage in STAGE_ORDER}
            if set(self.stage_providers) != required_stages:
                raise ValueError("AI会議の5工程すべてに担当を割り当ててください。")
            if not 1 <= len(self.reviewer_providers) <= 3:
                raise ValueError("旧AI会議には1〜3人のレビュアーが必要です。")
            selected = {
                *self.stage_providers.values(),
                *self.reviewer_providers,
                self.integrator_provider,
            }
        elif self.workflow_version == "independent_v2":
            if len(self.participant_providers) != len(REQUIRED_COUNCIL_PROVIDERS) or set(
                self.participant_providers
            ) != set(REQUIRED_COUNCIL_PROVIDERS):
                raise ValueError("OpenAI・Anthropic・Geminiを各1社ずつ指定してください。")
            if self.integrator_provider not in self.participant_providers:
                raise ValueError("統合担当は参加する3社から選んでください。")
            selected = {*self.participant_providers, self.integrator_provider}
        else:
            raise ValueError("AI会議のワークフローバージョンが正しくありません。")
        if not selected.issubset(self.provider_models):
            raise ValueError("未設定のAIプロバイダーが割り当てられています。")
        return self


class CouncilJobEvent(BaseModel):
    kind: str
    message: str
    stage: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CouncilJobRecord(BaseModel):
    run_id: str
    status: CouncilJobStatus
    spec: CouncilJobSpec
    events: tuple[CouncilJobEvent, ...] = ()
    result: dict[str, Any] | None = None
    error_message: str = ""
    worker_pid: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
