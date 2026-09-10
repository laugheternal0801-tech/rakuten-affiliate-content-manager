from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.models import PublishingControlRow
from app.publishing.repositories import get_control, save_audit, save_control
from app.publishing.schemas import (
    AuditEventType,
    AuditLog,
    PublishingAutonomy,
    PublishingControl,
)


def ensure_control(session: Session, settings: Settings) -> PublishingControl:
    if session.get(PublishingControlRow, "global") is not None:
        return get_control(session)
    control = PublishingControl(
        publishing_enabled=settings.publishing_enabled,
        dry_run=settings.publishing_dry_run,
        autonomy=PublishingAutonomy(settings.publishing_autonomy),
    )
    save_control(session, control)
    return control


def effective_control(session: Session, settings: Settings) -> PublishingControl:
    current = get_control(session)
    return current.model_copy(
        update={
            "publishing_enabled": (
                current.publishing_enabled
                and settings.publishing_enabled
                and settings.publishing_external_api_enabled
            ),
            "dry_run": current.dry_run or settings.publishing_dry_run,
        }
    )


def set_global_pause(
    session: Session, paused: bool, actor: str, settings: Settings
) -> PublishingControl:
    current = get_control(session)
    updated = current.model_copy(
        update={
            "globally_paused": paused,
            "publishing_enabled": current.publishing_enabled and settings.publishing_enabled,
            "dry_run": current.dry_run or settings.publishing_dry_run,
            "updated_by": actor.strip() or "unknown",
            "updated_at": datetime.now(UTC),
        }
    )
    save_control(session, updated)
    save_audit(
        session,
        AuditLog(
            event_type=(AuditEventType.GLOBAL_PAUSED if paused else AuditEventType.GLOBAL_RESUMED),
            actor=updated.updated_by,
            status="paused" if paused else "resumed",
        ),
    )
    return updated
