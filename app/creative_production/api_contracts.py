from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.creative_production.engine import CreativeProductionEngine
from app.creative_production.provider_factory import (
    create_image_registry,
    create_text_registry,
    create_video_registry,
)
from app.creative_production.repositories import (
    asset_lineage,
    get_package,
    record_approval,
)
from app.creative_production.schemas import (
    ApprovalDecision,
    ContentPackage,
    CreativeProductionRequest,
)

CREATIVE_API_ROUTES = (
    "POST /campaigns",
    "GET /campaigns/{id}",
    "POST /content/generate",
    "GET /content/{id}",
    "POST /content/{id}/revise",
    "POST /content/{id}/approve",
    "GET /content/{id}/scores",
    "GET /content/{id}/evidence",
    "GET /assets/{id}",
    "GET /creative/providers",
    "GET /creative/capabilities",
)


class CreativeAPIService:
    """Transport-neutral service contract ready for a future FastAPI adapter."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def generate(
        self,
        session: Session,
        request: CreativeProductionRequest,
    ) -> ContentPackage:
        return CreativeProductionEngine(self._settings).run(session, request)

    @staticmethod
    def get_content(session: Session, package_id: str) -> ContentPackage | None:
        return get_package(session, package_id)

    @staticmethod
    def revise(
        session: Session,
        package_id: str,
        reviewer: str,
        feedback: str,
    ) -> ContentPackage:
        return record_approval(
            session,
            package_id,
            ApprovalDecision.REQUEST_REVISION,
            reviewer,
            feedback,
        )

    @staticmethod
    def approve(
        session: Session,
        package_id: str,
        reviewer: str,
        feedback: str = "",
    ) -> ContentPackage:
        return record_approval(
            session,
            package_id,
            ApprovalDecision.APPROVE,
            reviewer,
            feedback,
        )

    @staticmethod
    def scores(session: Session, package_id: str) -> list[dict[str, Any]]:
        package = get_package(session, package_id)
        return [score.model_dump(mode="json") for score in package.scores] if package else []

    @staticmethod
    def evidence(session: Session, package_id: str) -> list[str]:
        package = get_package(session, package_id)
        return package.evidence_ids if package else []

    @staticmethod
    def asset(session: Session, asset_id: str) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in asset_lineage(session, asset_id)]

    def capabilities(self) -> dict[str, Any]:
        return {
            "text": {
                key: value.model_dump(mode="json")
                for key, value in create_text_registry(self._settings, allow_external=False)
                .capabilities()
                .items()
            },
            "image": {
                key: asdict(value)
                for key, value in create_image_registry(self._settings, allow_external=False)
                .capabilities()
                .items()
            },
            "video": {
                key: asdict(value)
                for key, value in create_video_registry(self._settings, allow_external=False)
                .capabilities()
                .items()
            },
        }
