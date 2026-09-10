from __future__ import annotations

import subprocess
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai_council import launcher
from app.ai_council.repositories import enqueue_council_job, get_council_job
from app.ai_council.schemas import CouncilBriefData, CouncilJobSpec, CouncilJobStatus
from app.ai_council.worker import AICouncilJobWorker
from app.config import Settings
from app.models import Base
from app.operating_system.providers import build_provider_registry
from app.services.ai_council import (
    STAGE_ORDER,
    LLMProvider,
    LLMRequest,
    ProviderError,
    ProviderRegistry,
)


class RecordingProvider(LLMProvider):
    def __init__(self, key: str) -> None:
        self.key = key
        self.calls: list[LLMRequest] = []
        self._lock = Lock()

    def generate(self, request: LLMRequest, model: str) -> str:
        with self._lock:
            self.calls.append(request)
        return f"{self.key}/{model}:{request.metadata['stage']}"


class FailingProvider(RecordingProvider):
    def __init__(self, key: str, failed_stage: str) -> None:
        super().__init__(key)
        self._failed_stage = failed_stage

    def generate(self, request: LLMRequest, model: str) -> str:
        if request.metadata["stage"] == self._failed_stage:
            raise ProviderError("計画APIエラー")
        return super().generate(request, model)


def _spec(run_id: str = "council-20260902T120000-test") -> CouncilJobSpec:
    return CouncilJobSpec(
        run_id=run_id,
        brief=CouncilBriefData(objective="画面を閉じてもAI会議を完了する"),
        workflow_version="independent_v2",
        participant_providers=("openai", "anthropic", "gemini"),
        integrator_provider="openai",
        provider_models={
            "openai": "gpt-5.6-sol",
            "anthropic": "claude-fable-5-1",
            "gemini": "gemini-3.1-pro-preview",
        },
        debate_rounds=2,
        max_output_tokens=800,
    )


def test_background_worker_persists_progress_and_result(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'council.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    spec = _spec()
    with factory() as session:
        enqueue_council_job(session, spec)
        session.commit()

    providers = [
        RecordingProvider("openai"),
        RecordingProvider("anthropic"),
        RecordingProvider("gemini"),
    ]
    assert AICouncilJobWorker(ProviderRegistry(providers), factory).run(spec.run_id) is True

    with factory() as session:
        saved = get_council_job(session, spec.run_id)
    assert saved is not None
    assert saved.status is CouncilJobStatus.COMPLETED
    assert saved.result is not None
    assert saved.result["run_id"] == spec.run_id
    assert saved.result["workflow_version"] == "independent_v2"
    assert len(saved.result["independent_proposals"]) == 3
    assert len(saved.result["debate"]) == 2
    assert saved.events[-1].kind == "completed"
    saved_outputs = [event.output for event in saved.events if event.output]
    assert sum(output["output_type"] == "independent_stage" for output in saved_outputs) == 9
    assert sum(output["output_type"] == "debate" for output in saved_outputs) == 2
    assert sum(output["output_type"] == "final" for output in saved_outputs) == 1
    openai_research = next(
        output
        for output in saved_outputs
        if output["output_type"] == "independent_stage"
        and output["participant"]["provider"] == "openai"
        and output["stage"] == "research"
    )
    assert openai_research["contribution"]["content"] == "openai/gpt-5.6-sol:research"
    assert sum(len(provider.calls) for provider in providers) == 16


def test_worker_keeps_completed_outputs_when_later_stage_fails(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'failed-council.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    spec = _spec("council-20260902T120000-partial")
    with factory() as session:
        enqueue_council_job(session, spec)
        session.commit()

    providers = [
        RecordingProvider("openai"),
        FailingProvider("anthropic", "planning"),
        RecordingProvider("gemini"),
    ]
    worker = AICouncilJobWorker(ProviderRegistry(providers), factory)
    assert worker.run(spec.run_id) is False

    with factory() as session:
        saved = get_council_job(session, spec.run_id)
    assert saved is not None
    assert saved.status is CouncilJobStatus.FAILED
    assert saved.result is None
    saved_outputs = [event.output for event in saved.events if event.output]
    independent_outputs = [
        output for output in saved_outputs if output["output_type"] == "independent_stage"
    ]
    assert len(independent_outputs) == 8
    failed = next(output for output in independent_outputs if output["status"] == "failed")
    assert failed["participant"]["provider"] == "anthropic"
    assert failed["stage"] == "planning"
    assert "計画APIエラー" in failed["contribution"]["error"]
    assert not any(output["output_type"] in {"debate", "final"} for output in saved_outputs)


def test_worker_requires_all_three_ai_in_every_debate_round(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'failed-debate.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    spec = _spec("council-20260902T120000-debate-failure")
    with factory() as session:
        enqueue_council_job(session, spec)
        session.commit()

    providers = [
        RecordingProvider("openai"),
        FailingProvider("anthropic", "debate"),
        RecordingProvider("gemini"),
    ]
    worker = AICouncilJobWorker(ProviderRegistry(providers), factory)
    assert worker.run(spec.run_id) is False

    with factory() as session:
        saved = get_council_job(session, spec.run_id)
    assert saved is not None
    assert saved.status is CouncilJobStatus.FAILED
    saved_outputs = [event.output for event in saved.events if event.output]
    assert sum(output["output_type"] == "independent_stage" for output in saved_outputs) == 9
    debate_output = next(output for output in saved_outputs if output["output_type"] == "debate")
    assert len(debate_output["contributions"]) == 3
    failed = next(
        item for item in debate_output["contributions"] if item["provider"] == "anthropic"
    )
    assert failed["error"]
    assert not any(output["output_type"] == "final" for output in saved_outputs)


def test_job_spec_rejects_demo_provider() -> None:
    values = _spec().model_dump()
    values["provider_models"] = {"demo": "local-demo"}
    values["participant_providers"] = ("demo", "demo", "demo")
    values["integrator_provider"] = "demo"

    try:
        CouncilJobSpec.model_validate(values)
    except ValueError as exc:
        assert "デモプロバイダー" in str(exc)
    else:
        raise AssertionError("demo provider must be rejected")


def test_job_spec_requires_exact_three_provider_team() -> None:
    values = _spec().model_dump()
    values["participant_providers"] = ("openai", "anthropic")

    try:
        CouncilJobSpec.model_validate(values)
    except ValueError as exc:
        assert "OpenAI・Anthropic・Gemini" in str(exc)
    else:
        raise AssertionError("all three providers must be required")


def test_job_spec_can_read_legacy_saved_job() -> None:
    payload = {
        "run_id": "council-20260902T120000-legacy",
        "brief": {"objective": "旧履歴を表示する"},
        "stage_providers": {stage.value: "openai" for stage in STAGE_ORDER},
        "reviewer_providers": ["openai"],
        "integrator_provider": "openai",
        "provider_models": {"openai": "gpt-5.6-sol"},
    }

    spec = CouncilJobSpec.model_validate(payload)

    assert spec.workflow_version == "legacy_v1"
    assert spec.stage_providers["research"] == "openai"


def test_ai_council_models_use_flagship_defaults() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="openai-test-key",
        anthropic_api_key="anthropic-test-key",
        gemini_api_key="gemini-test-key",
    )
    assert settings.ai_council_provider_models == {
        "openai": "gpt-5.6-sol",
        "anthropic": "claude-fable-5-1",
        "gemini": "gemini-3.1-pro-preview",
    }
    assert settings.ai_council_timeout_seconds == 900
    assert settings.ai_council_max_output_tokens == 16_000

    registry, models = build_provider_registry(
        settings,
        include_demo=False,
        maximum_quality=True,
    )
    assert registry is not None
    assert set(registry.keys) == {"openai", "anthropic", "gemini"}
    assert models == settings.ai_council_provider_models


def test_launcher_starts_separate_process(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class FakeProcess:
        pid = 4321

        def __init__(self, command: list[str], **kwargs: Any) -> None:
            captured["command"] = command
            captured["kwargs"] = kwargs

    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    launched = launcher.launch_council_worker("council-20260902T120000-launch")

    assert launched.pid == 4321
    assert captured["command"][1:4] == [
        "-m",
        "app.ai_council.worker_cli",
        "--run-id",
    ]
    options = captured["kwargs"]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.STDOUT
    if launcher.sys.platform == "win32":
        assert options["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert options["start_new_session"] is True


def test_launcher_rejects_unsafe_run_id(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    try:
        launcher.launch_council_worker("../../outside")
    except ValueError as exc:
        assert "実行ID" in str(exc)
    else:
        raise AssertionError("unsafe run ID must be rejected")
