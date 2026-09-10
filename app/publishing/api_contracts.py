from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings
from app.creative_production.schemas import CreativePlatform
from app.publishing.approval import PublishingApprovalService
from app.publishing.controls import set_global_pause
from app.publishing.orchestrator import PublishingOrchestrator
from app.publishing.registry import PublisherRegistry, build_publisher_registry
from app.publishing.repositories import (
    acknowledge_notification,
    get_job,
    get_publication,
    list_accounts,
    list_notifications,
    list_performance_snapshots,
    list_worker_heartbeats,
    list_worker_leases,
)
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    ApprovalRecord,
    ApprovedContentSnapshot,
    ExternalPublication,
    PublicationJob,
    PublishingControl,
    PublishingSchedule,
    PublishResult,
)

PUBLISHING_API_ROUTES = (
    "POST /content/{id}/approve",
    "POST /content/{id}/request-revision",
    "POST /content/{id}/lock",
    "POST /publications",
    "GET /publications/{id}",
    "POST /publications/{id}/schedule",
    "POST /publications/{id}/cancel",
    "POST /publications/{id}/publish",
    "GET /publications/{id}/status",
    "GET /publications/{id}/preflight",
    "GET /publications/{id}/metrics",
    "GET /campaigns/{id}/performance",
    "GET /publishing/providers",
    "GET /publishing/accounts",
    "GET /publishing/capabilities",
    "POST /publishing/pause",
    "POST /publishing/resume",
    "GET /publishing/workers",
    "GET /publishing/notifications",
    "POST /publishing/notifications/{id}/acknowledge",
)


class PublishingAPIService:
    """Transport-neutral contract for a future HTTP adapter."""

    def __init__(self, settings: Settings, registry: PublisherRegistry | None = None) -> None:
        self._settings = settings
        self._registry = registry or build_publisher_registry(settings)
        self._approval = PublishingApprovalService()
        self._scheduler = PublishingScheduler(settings)
        self._orchestrator = PublishingOrchestrator(settings, self._registry)

    def approve_and_lock(
        self,
        session: Session,
        package_id: str,
        platforms: list[CreativePlatform],
        approved_by: str,
        target_accounts: Mapping[CreativePlatform, str],
        *,
        platform_assets: Mapping[CreativePlatform, list[str]] | None = None,
        platform_metadata: Mapping[CreativePlatform, dict[str, object]] | None = None,
    ) -> tuple[ApprovalRecord, list[ApprovedContentSnapshot]]:
        return self._approval.approve_and_lock(
            session,
            package_id,
            platforms,
            approved_by,
            target_accounts,
            platform_assets=platform_assets,
            platform_metadata=platform_metadata,
        )

    def schedule(
        self,
        session: Session,
        snapshot_id: str,
        scheduled_at: datetime,
        timezone: str,
        approved_by: str,
    ) -> tuple[PublishingSchedule, PublicationJob]:
        return self._scheduler.create_approved_schedule(
            session, snapshot_id, scheduled_at, timezone, approved_by
        )

    def request_revision(
        self,
        session: Session,
        package_id: str,
        requested_by: str,
        reason_code: str,
        feedback: str = "",
    ) -> list[ApprovalRecord]:
        return self._approval.request_revision(
            session,
            package_id,
            requested_by,
            reason_code,
            feedback,
        )

    def cancel(
        self, session: Session, schedule_id: str, actor: str
    ) -> tuple[PublishingSchedule, PublicationJob]:
        return self._scheduler.cancel(session, schedule_id, actor)

    async def publish(self, session: Session, job_id: str) -> PublishResult:
        return await self._orchestrator.dispatch(session, job_id)

    async def status(self, session: Session, publication_id: str) -> ExternalPublication:
        return await self._orchestrator.poll_status(session, publication_id)

    @staticmethod
    def publication(session: Session, publication_id: str) -> ExternalPublication | None:
        return get_publication(session, publication_id)

    @staticmethod
    def job(session: Session, job_id: str) -> PublicationJob | None:
        return get_job(session, job_id)

    @staticmethod
    def metrics(session: Session, publication_id: str) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in list_performance_snapshots(session, publication_id=publication_id)
        ]

    def pause(self, session: Session, actor: str) -> PublishingControl:
        return set_global_pause(session, True, actor, self._settings)

    def resume(self, session: Session, actor: str) -> PublishingControl:
        return set_global_pause(session, False, actor, self._settings)

    async def capabilities(self) -> dict[str, dict[str, object]]:
        values = await self._registry.capabilities()
        return {
            platform.value: capability.model_dump(mode="json")
            for platform, capability in values.items()
        }

    @staticmethod
    def accounts(session: Session) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json", exclude={"credential_reference"})
            for item in list_accounts(session)
        ]

    @staticmethod
    def workers(session: Session) -> dict[str, list[dict[str, object]]]:
        return {
            "heartbeats": [
                item.model_dump(mode="json") for item in list_worker_heartbeats(session)
            ],
            "leases": [item.model_dump(mode="json") for item in list_worker_leases(session)],
        }

    @staticmethod
    def notifications(session: Session) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json", exclude={"payload"})
            for item in list_notifications(session)
        ]

    @staticmethod
    def acknowledge_notification(session: Session, notification_id: str) -> dict[str, object]:
        return acknowledge_notification(session, notification_id).model_dump(
            mode="json",
            exclude={"payload"},
        )
