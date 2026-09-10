from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.ai_council.repositories import (
    append_council_event,
    claim_council_job,
    complete_council_job,
    fail_council_job,
    get_council_job,
)
from app.ai_council.schemas import CouncilJobEvent
from app.services.ai_council import (
    CouncilRunError,
    IndependentMultiAgentCouncil,
    MultiAgentCouncil,
    ProviderRegistry,
    WorkflowBrief,
    WorkflowEvent,
    WorkflowStage,
    build_independent_council_config,
    build_standard_council_config,
)

SessionFactory = sessionmaker[Session]


class AICouncilJobWorker:
    def __init__(
        self,
        providers: ProviderRegistry,
        session_factory: SessionFactory,
        council_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._providers = providers
        self._session_factory = session_factory
        self._council_factory = council_factory

    def run(self, run_id: str) -> bool:
        with self._session_factory() as session:
            job = get_council_job(session, run_id)
            if job is None or not claim_council_job(session, run_id):
                return False
            session.commit()

        try:
            spec = job.spec
            brief = WorkflowBrief(**spec.brief.model_dump())
            config: Any
            if spec.workflow_version == "legacy_v1":
                config = build_standard_council_config(
                    stage_providers={
                        WorkflowStage(stage): provider
                        for stage, provider in spec.stage_providers.items()
                    },
                    reviewer_providers=spec.reviewer_providers,
                    integrator_provider=spec.integrator_provider,
                    provider_models=spec.provider_models,
                    debate_rounds=spec.debate_rounds,
                    max_output_tokens=spec.max_output_tokens,
                    enable_openai_web_search=spec.enable_openai_web_search,
                )
                council_type: Callable[..., Any] = MultiAgentCouncil
            else:
                config = build_independent_council_config(
                    participant_providers=spec.participant_providers,
                    integrator_provider=spec.integrator_provider,
                    provider_models=spec.provider_models,
                    debate_rounds=spec.debate_rounds,
                    max_output_tokens=spec.max_output_tokens,
                    enable_web_search=spec.enable_openai_web_search,
                )
                council_type = IndependentMultiAgentCouncil
            council = (self._council_factory or council_type)(self._providers, config)
            result = council.run(brief, on_event=self._event_handler(run_id), run_id=run_id)
            with self._session_factory() as session:
                complete_council_job(session, run_id, result.to_dict())
                session.commit()
            return True
        except (ValueError, CouncilRunError) as exc:
            message = str(exc)
        except Exception as exc:  # Defensive boundary for the detached worker.
            message = f"予期しない{type(exc).__name__}が発生しました。"

        with self._session_factory() as session:
            fail_council_job(session, run_id, message)
            session.commit()
        return False

    def _event_handler(self, run_id: str) -> Callable[[WorkflowEvent], None]:
        persist_lock = Lock()

        def persist(event: WorkflowEvent) -> None:
            item = CouncilJobEvent(
                kind=event.kind,
                message=event.message,
                stage=event.stage.value if event.stage is not None else "",
                output=dict(event.output),
            )
            with persist_lock:
                with self._session_factory() as session:
                    append_council_event(session, run_id, item)
                    session.commit()

        return persist
