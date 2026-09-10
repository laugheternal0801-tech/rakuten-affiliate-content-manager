from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.ai_council.models import AICouncilJobRow
from app.ai_council.schemas import (
    CouncilJobEvent,
    CouncilJobRecord,
    CouncilJobSpec,
    CouncilJobStatus,
)


def _record(row: AICouncilJobRow) -> CouncilJobRecord:
    return CouncilJobRecord(
        run_id=row.run_id,
        status=CouncilJobStatus(row.status),
        spec=CouncilJobSpec.model_validate(row.spec_json),
        events=tuple(CouncilJobEvent.model_validate(item) for item in row.events_json),
        result=row.result_json,
        error_message=row.error_message,
        worker_pid=row.worker_pid,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
    )


def enqueue_council_job(session: Session, spec: CouncilJobSpec) -> CouncilJobRecord:
    now = datetime.now(UTC)
    row = AICouncilJobRow(
        run_id=spec.run_id,
        status=CouncilJobStatus.QUEUED.value,
        spec_json=spec.model_dump(mode="json"),
        events_json=[],
        result_json=None,
        error_message="",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return _record(row)


def get_council_job(session: Session, run_id: str) -> CouncilJobRecord | None:
    row = session.get(AICouncilJobRow, run_id)
    return _record(row) if row is not None else None


def list_council_jobs(session: Session, limit: int = 20) -> list[CouncilJobRecord]:
    statement = (
        select(AICouncilJobRow)
        .order_by(AICouncilJobRow.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return [_record(row) for row in session.scalars(statement)]


def claim_council_job(session: Session, run_id: str) -> bool:
    now = datetime.now(UTC)
    result = cast(
        CursorResult[Any],
        session.execute(
            update(AICouncilJobRow)
            .where(
                AICouncilJobRow.run_id == run_id,
                AICouncilJobRow.status == CouncilJobStatus.QUEUED.value,
            )
            .values(
                status=CouncilJobStatus.RUNNING.value,
                started_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    session.flush()
    return bool(result.rowcount)


def set_council_worker_pid(session: Session, run_id: str, worker_pid: int) -> None:
    row = session.get(AICouncilJobRow, run_id)
    if row is None:
        raise ValueError("AI会議ジョブが見つかりません。")
    row.worker_pid = worker_pid
    row.updated_at = datetime.now(UTC)
    session.flush()


def append_council_event(session: Session, run_id: str, event: CouncilJobEvent) -> None:
    row = session.get(AICouncilJobRow, run_id)
    if row is None:
        raise ValueError("AI会議ジョブが見つかりません。")
    row.events_json = [*row.events_json, event.model_dump(mode="json")]
    row.updated_at = datetime.now(UTC)
    session.flush()


def complete_council_job(
    session: Session,
    run_id: str,
    result_json: dict[str, Any],
) -> None:
    row = session.get(AICouncilJobRow, run_id)
    if row is None:
        raise ValueError("AI会議ジョブが見つかりません。")
    now = datetime.now(UTC)
    row.status = CouncilJobStatus.COMPLETED.value
    row.result_json = result_json
    row.error_message = ""
    row.completed_at = now
    row.updated_at = now
    session.flush()

def fail_council_job(session: Session, run_id: str, message: str) -> None:
    row = session.get(AICouncilJobRow, run_id)
    if row is None:
        raise ValueError("AI会議ジョブが見つかりません。")
    now = datetime.now(UTC)
    row.status = CouncilJobStatus.FAILED.value
    row.error_message = message[:4_000]
    row.completed_at = now
    row.updated_at = now
    session.flush()
