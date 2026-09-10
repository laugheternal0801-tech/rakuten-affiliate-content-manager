from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.operating_system.models import (
    OSCacheRow,
    OSCostLedgerRow,
    OSDecisionOutcomeRow,
    OSDecisionRow,
    OSMemoryRow,
    OSModelCallRow,
    OSPredictionRow,
    OSRunRow,
)
from app.operating_system.schemas import (
    ApprovalStatus,
    DecisionOutcome,
    DecisionOutput,
    DecisionRequest,
    ModelCallRecord,
    ModelPerformanceSummary,
    ModelRoutePlan,
    OutcomeErrorCause,
    OutcomeStatus,
    PredictionRecord,
    RunStatus,
)


def create_run(
    session: Session,
    *,
    run_id: str,
    request: DecisionRequest,
    route: ModelRoutePlan,
) -> OSRunRow:
    row = OSRunRow(
        id=run_id,
        status=RunStatus.RUNNING.value,
        research_mode=request.research_mode.value,
        objective=request.objective,
        request_json=request.model_dump(mode="json"),
        route_json=route.model_dump(mode="json"),
        budget_json=request.budget.model_dump(mode="json"),
        estimated_cost_usd=route.estimated_cost_usd,
    )
    session.add(row)
    session.flush()
    return row


def save_model_call(session: Session, record: ModelCallRecord) -> None:
    session.add(
        OSModelCallRow(
            id=record.call_id,
            run_id=record.run_id,
            task=record.task,
            agent=record.agent,
            provider=record.provider,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            estimated_cost_usd=record.estimated_cost_usd,
            latency_ms=record.latency_ms,
            success=record.success,
            cached=record.cached,
            fallback=record.fallback,
            error_type=record.error_type,
            error_message=record.error_message,
            timestamp=record.timestamp,
        )
    )
    session.add(
        OSCostLedgerRow(
            id=record.call_id,
            run_id=record.run_id,
            decision_id=record.decision_id,
            task=record.task,
            agent=record.agent,
            provider=record.provider,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            estimated_cost_usd=record.estimated_cost_usd,
            actual_cost_usd=record.actual_cost_usd,
            latency_ms=record.latency_ms,
            success=record.success,
            cached=record.cached,
            fallback=record.fallback,
            timestamp=record.timestamp,
        )
    )
    session.flush()


def save_decision(session: Session, output: DecisionOutput) -> OSDecisionRow:
    row = OSDecisionRow(
        id=output.decision_id,
        run_id=output.run_id,
        decision=output.decision.value,
        opportunity_score=output.opportunity_score,
        confidence=output.confidence,
        evidence_score=output.evidence_score,
        estimated_value=output.estimated_value,
        ai_cost=output.ai_cost,
        approval_status=output.approval_status.value,
        decision_json=output.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    for prediction in output.predictions:
        save_prediction(session, prediction)
    _save_memory(
        session,
        memory_type="decision",
        memory_key=output.decision_id,
        source_id=output.run_id,
        payload=output.model_dump(mode="json"),
    )
    return row


def finish_run(
    session: Session,
    *,
    run_id: str,
    output: DecisionOutput,
    duration_ms: int,
) -> None:
    row = session.get(OSRunRow, run_id)
    if row is None:
        raise LookupError(f"Operating System Run {run_id} が見つかりません。")
    row.status = RunStatus.COMPLETED.value
    row.actual_estimated_cost_usd = output.ai_cost
    row.api_calls = output.api_calls
    row.search_calls = output.search_calls
    row.duration_ms = duration_ms
    row.stopped_early = output.stopped_early
    row.budget_exhausted = output.budget_exhausted
    row.completed_at = datetime.now(UTC)
    session.flush()


def fail_run(session: Session, run_id: str, exc: Exception, duration_ms: int) -> None:
    row = session.get(OSRunRow, run_id)
    if row is None:
        return
    row.status = RunStatus.FAILED.value
    row.duration_ms = duration_ms
    row.error_json = {"type": type(exc).__name__, "message": str(exc)}
    row.completed_at = datetime.now(UTC)
    session.flush()


def list_runs(session: Session, limit: int = 50) -> list[OSRunRow]:
    return list(session.scalars(select(OSRunRow).order_by(OSRunRow.created_at.desc()).limit(limit)))


def list_decisions(session: Session, limit: int = 100) -> list[OSDecisionRow]:
    return list(
        session.scalars(
            select(OSDecisionRow).order_by(OSDecisionRow.created_at.desc()).limit(limit)
        )
    )


def get_decision(session: Session, run_id: str) -> DecisionOutput | None:
    row = session.scalar(select(OSDecisionRow).where(OSDecisionRow.run_id == run_id))
    return DecisionOutput.model_validate(row.decision_json) if row else None


def list_model_calls(session: Session, run_id: str) -> list[OSModelCallRow]:
    return list(
        session.scalars(
            select(OSModelCallRow)
            .where(OSModelCallRow.run_id == run_id)
            .order_by(OSModelCallRow.timestamp)
        )
    )


def daily_estimated_spend(session: Session, now: datetime | None = None) -> float:
    current = now or datetime.now(UTC)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    amount = session.scalar(
        select(func.coalesce(func.sum(OSCostLedgerRow.estimated_cost_usd), 0.0)).where(
            OSCostLedgerRow.timestamp >= start
        )
    )
    return float(amount or 0.0)


def set_decision_approval(
    session: Session,
    run_id: str,
    status: ApprovalStatus,
    note: str = "",
) -> DecisionOutput:
    row = session.scalar(select(OSDecisionRow).where(OSDecisionRow.run_id == run_id))
    if row is None:
        raise LookupError(f"Decision for {run_id} が見つかりません。")
    output = DecisionOutput.model_validate(row.decision_json).model_copy(
        update={"approval_status": status}
    )
    row.approval_status = status.value
    row.approval_note = note
    row.reviewed_at = datetime.now(UTC)
    row.decision_json = output.model_dump(mode="json")
    session.flush()
    return output


def save_prediction(session: Session, prediction: PredictionRecord) -> OSPredictionRow:
    row = session.get(OSPredictionRow, prediction.prediction_id)
    values = {
        "decision_id": prediction.decision_id,
        "metric": prediction.metric,
        "predicted_value": prediction.predicted_value,
        "predicted_range_json": (
            list(prediction.predicted_range) if prediction.predicted_range else None
        ),
        "confidence": prediction.confidence,
        "actual_value": prediction.actual_value,
        "error": prediction.error,
        "evaluated_at": prediction.evaluated_at,
        "created_at": prediction.created_at,
    }
    if row is None:
        row = OSPredictionRow(id=prediction.prediction_id, **values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()
    return row


def list_predictions(
    session: Session,
    decision_id: str | None = None,
) -> list[PredictionRecord]:
    query = select(OSPredictionRow).order_by(OSPredictionRow.created_at.desc())
    if decision_id:
        query = query.where(OSPredictionRow.decision_id == decision_id)
    return [_prediction_from_row(row) for row in session.scalars(query)]


def save_decision_outcome(
    session: Session,
    outcome: DecisionOutcome,
) -> DecisionOutcome:
    decision_row = session.get(OSDecisionRow, outcome.decision_id)
    if decision_row is None:
        raise LookupError(f"Decision {outcome.decision_id} が見つかりません。")
    decision = DecisionOutput.model_validate(decision_row.decision_json)
    ai_cost = outcome.ai_cost or decision.ai_cost
    gross_profit = outcome.gross_profit
    if gross_profit == 0:
        gross_profit = outcome.commission or outcome.revenue
    total_cost = round(
        ai_cost + outcome.content_cost + outcome.ad_cost + outcome.platform_cost,
        8,
    )
    net_profit = round(gross_profit - total_cost, 8)
    actual_roi = round(net_profit / total_cost, 6) if total_cost > 0 else None
    normalized = outcome.model_copy(
        update={
            "run_id": decision.run_id,
            "ai_cost": ai_cost,
            "gross_profit": gross_profit,
            "total_cost": total_cost,
            "net_profit": net_profit,
            "decision_roi": actual_roi,
            "actual_decision_roi": actual_roi,
        }
    )
    row = session.scalar(
        select(OSDecisionOutcomeRow).where(OSDecisionOutcomeRow.decision_id == outcome.decision_id)
    )
    values = {
        "run_id": normalized.run_id,
        "status": normalized.status.value,
        "impressions": normalized.impressions,
        "clicks": normalized.clicks,
        "conversions": normalized.conversions,
        "revenue": normalized.revenue,
        "commission": normalized.commission,
        "gross_profit": normalized.gross_profit,
        "ai_cost": normalized.ai_cost,
        "content_cost": normalized.content_cost,
        "ad_cost": normalized.ad_cost,
        "platform_cost": normalized.platform_cost,
        "total_cost": normalized.total_cost,
        "net_profit": normalized.net_profit,
        "decision_roi": normalized.actual_decision_roi,
        "error_causes_json": [item.value for item in normalized.error_causes],
        "actual_result_json": normalized.actual_result,
        "lessons_json": normalized.lessons,
        "recorded_at": normalized.recorded_at,
    }
    if row is None:
        row = OSDecisionOutcomeRow(
            id=normalized.outcome_id,
            decision_id=normalized.decision_id,
            **values,
        )
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)

    actual_values: dict[str, float] = {
        "impressions": float(normalized.impressions),
        "clicks": float(normalized.clicks),
        "conversions": float(normalized.conversions),
        "revenue": normalized.revenue,
        "commission": normalized.commission,
        "gross_profit": normalized.gross_profit,
        "net_profit": normalized.net_profit,
        "decision_roi": normalized.actual_decision_roi or 0.0,
    }
    actual_values.update(
        {
            key: float(value)
            for key, value in normalized.actual_result.items()
            if isinstance(value, int | float) and not isinstance(value, bool)
        }
    )
    evaluated_at = datetime.now(UTC)
    for prediction_row in session.scalars(
        select(OSPredictionRow).where(OSPredictionRow.decision_id == normalized.decision_id)
    ):
        actual = actual_values.get(prediction_row.metric)
        if actual is None:
            continue
        prediction_row.actual_value = actual
        prediction_row.error = (
            abs(actual - prediction_row.predicted_value)
            if prediction_row.predicted_value is not None
            else None
        )
        prediction_row.evaluated_at = evaluated_at

    updated_decision = decision.model_copy(
        update={
            "actual_outcome": normalized.model_dump(mode="json"),
            "decision_roi": actual_roi,
        }
    )
    decision_row.decision_json = updated_decision.model_dump(mode="json")
    _save_memory(
        session,
        memory_type="outcome",
        memory_key=normalized.decision_id,
        source_id=normalized.outcome_id,
        payload=normalized.model_dump(mode="json"),
    )
    session.flush()
    return normalized


def list_decision_outcomes(session: Session) -> list[DecisionOutcome]:
    rows = session.scalars(
        select(OSDecisionOutcomeRow).order_by(OSDecisionOutcomeRow.recorded_at.desc())
    )
    return [_outcome_from_row(row) for row in rows]


def get_decision_outcome(
    session: Session,
    decision_id: str,
) -> DecisionOutcome | None:
    row = session.scalar(
        select(OSDecisionOutcomeRow).where(OSDecisionOutcomeRow.decision_id == decision_id)
    )
    return _outcome_from_row(row) if row else None


def model_performance_summaries(session: Session) -> list[ModelPerformanceSummary]:
    calls = list(session.scalars(select(OSCostLedgerRow)))
    decisions = {row.run_id: row for row in session.scalars(select(OSDecisionRow))}
    outcomes = {row.decision_id: row for row in session.scalars(select(OSDecisionOutcomeRow))}
    predictions: dict[str, list[OSPredictionRow]] = {}
    for row in session.scalars(select(OSPredictionRow)):
        predictions.setdefault(row.decision_id, []).append(row)

    grouped: dict[tuple[str, str, str], list[OSCostLedgerRow]] = {}
    for call in calls:
        grouped.setdefault((call.provider, call.model, call.task), []).append(call)

    results: list[ModelPerformanceSummary] = []
    for (provider, model, task), rows in sorted(grouped.items()):
        linked = [decisions[row.run_id] for row in rows if row.run_id in decisions]
        unique_decisions = {row.id: row for row in linked}
        prediction_accuracy: list[float] = []
        for decision_id in unique_decisions:
            for prediction in predictions.get(decision_id, []):
                if prediction.error is None:
                    continue
                scale = max(
                    abs(prediction.predicted_value or 0),
                    abs(prediction.actual_value or 0),
                    1.0,
                )
                prediction_accuracy.append(max(0.0, 1 - prediction.error / scale))
        reviewed = [
            row
            for row in unique_decisions.values()
            if row.approval_status != ApprovalStatus.PENDING.value
        ]
        approved = [row for row in reviewed if row.approval_status == ApprovalStatus.APPROVED.value]
        results.append(
            ModelPerformanceSummary(
                provider=provider,
                model=model,
                task_type=task,
                calls=len(rows),
                decisions=len(unique_decisions),
                average_cost=sum(row.estimated_cost_usd for row in rows) / len(rows),
                average_latency_ms=sum(row.latency_ms for row in rows) / len(rows),
                success_rate=sum(row.success for row in rows) / len(rows),
                prediction_accuracy=(
                    sum(prediction_accuracy) / len(prediction_accuracy)
                    if prediction_accuracy
                    else None
                ),
                user_approval_rate=(len(approved) / len(reviewed) if reviewed else None),
                associated_profit=sum(
                    outcomes[decision_id].net_profit
                    for decision_id in unique_decisions
                    if decision_id in outcomes
                ),
            )
        )
    return results


def calibration_summary(session: Session) -> list[dict[str, float | int | str]]:
    decisions = {row.id: row for row in session.scalars(select(OSDecisionRow))}
    buckets: dict[int, list[float]] = {}
    for outcome in session.scalars(select(OSDecisionOutcomeRow)):
        decision = decisions.get(outcome.decision_id)
        if decision is None or outcome.status == OutcomeStatus.UNKNOWN.value:
            continue
        bucket = min(4, int(decision.confidence * 5))
        observed = float(outcome.status == OutcomeStatus.SUCCESS.value)
        buckets.setdefault(bucket, []).append(observed)
    return [
        {
            "confidence_band": f"{bucket * 20}-{(bucket + 1) * 20}%",
            "decisions": len(values),
            "observed_success_rate": round(sum(values) / len(values), 3),
        }
        for bucket, values in sorted(buckets.items())
    ]


def _prediction_from_row(row: OSPredictionRow) -> PredictionRecord:
    predicted_range = row.predicted_range_json
    return PredictionRecord(
        prediction_id=row.id,
        decision_id=row.decision_id,
        metric=row.metric,
        predicted_value=row.predicted_value,
        predicted_range=(
            (float(predicted_range[0]), float(predicted_range[1]))
            if predicted_range and len(predicted_range) == 2
            else None
        ),
        confidence=row.confidence,
        actual_value=row.actual_value,
        error=row.error,
        evaluated_at=row.evaluated_at,
        created_at=row.created_at,
    )


def _outcome_from_row(row: OSDecisionOutcomeRow) -> DecisionOutcome:
    return DecisionOutcome(
        outcome_id=row.id,
        decision_id=row.decision_id,
        run_id=row.run_id,
        status=OutcomeStatus(row.status),
        impressions=row.impressions,
        clicks=row.clicks,
        conversions=row.conversions,
        revenue=row.revenue,
        commission=row.commission,
        gross_profit=row.gross_profit,
        ai_cost=row.ai_cost,
        content_cost=row.content_cost,
        ad_cost=row.ad_cost,
        platform_cost=row.platform_cost,
        total_cost=row.total_cost,
        net_profit=row.net_profit,
        decision_roi=row.decision_roi,
        actual_decision_roi=row.decision_roi,
        error_causes=[OutcomeErrorCause(value) for value in row.error_causes_json],
        actual_result=row.actual_result_json,
        lessons=row.lessons_json,
        recorded_at=row.recorded_at,
    )


def _save_memory(
    session: Session,
    *,
    memory_type: str,
    memory_key: str,
    source_id: str,
    payload: dict[str, object],
) -> None:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(OSMemoryRow)
        .where(
            OSMemoryRow.memory_type == memory_type,
            OSMemoryRow.memory_key == memory_key,
        )
        .order_by(OSMemoryRow.version.desc())
    )
    if existing and existing.content_hash == content_hash:
        return
    session.add(
        OSMemoryRow(
            id=f"OSM-{uuid4().hex}",
            memory_type=memory_type,
            memory_key=memory_key,
            source_id=source_id,
            content_hash=content_hash,
            version=(existing.version + 1 if existing else 1),
            payload_json=payload,
        )
    )


def get_cached_response(session: Session, key: str) -> str | None:
    row = session.get(OSCacheRow, key)
    if row is None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    return row.response_text


def save_cached_response(
    session: Session,
    *,
    key: str,
    provider: str,
    model: str,
    response_text: str,
    content_hash: str,
    ttl_seconds: int,
) -> None:
    row = session.get(OSCacheRow, key)
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    if row is None:
        session.add(
            OSCacheRow(
                key=key,
                kind="llm",
                provider=provider,
                model=model,
                response_text=response_text,
                content_hash=content_hash,
                expires_at=expires_at,
            )
        )
    else:
        row.provider = provider
        row.model = model
        row.response_text = response_text
        row.content_hash = content_hash
        row.expires_at = expires_at
    session.flush()
