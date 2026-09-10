from __future__ import annotations

from collections.abc import Iterable

from app.publishing.publishers.base import PublisherError
from app.publishing.schemas import (
    CapabilityAvailability,
    ContentPerformanceSnapshot,
    ExternalPublication,
    PublisherCapability,
    PublishingPlatform,
    PublishingStatus,
    PublishPayload,
    PublishResult,
    SocialAccountConnection,
    utcnow,
)


class MockPublishingProvider:
    """Deterministic integration-test provider; never registered in the application."""

    key = "mock_publisher"

    def __init__(
        self,
        platform: PublishingPlatform,
        outcomes: Iterable[PublisherError | PublishingStatus] = (),
        metrics: dict[str, int | float | None] | None = None,
    ) -> None:
        self.platform = platform
        self._outcomes = list(outcomes)
        self._metrics = metrics or {}
        self.publish_calls = 0

    async def health_check(self) -> PublisherCapability:
        return await self.get_capabilities()

    async def get_capabilities(
        self, account: SocialAccountConnection | None = None
    ) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.AVAILABLE,
            enabled=True,
            operations=["publish", "status", "metrics"],
            content_types=["text", "image", "video"],
            max_text_length=10_000,
        )

    async def validate_credentials(self, account: SocialAccountConnection) -> bool:
        return account.platform is self.platform

    async def validate_content(self, payload: PublishPayload) -> list[str]:
        return []

    async def publish(self, payload: PublishPayload) -> PublishResult:
        self.publish_calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else PublishingStatus.PUBLISHED
        if isinstance(outcome, PublisherError):
            raise outcome
        if outcome is PublishingStatus.DRY_RUN_COMPLETED:
            return PublishResult(
                platform=self.platform,
                provider=self.key,
                snapshot_id=payload.snapshot_id,
                target_account_id=payload.target_account_id,
                status=outcome,
                request_id=f"mock-request-{self.publish_calls}",
                response_metadata={"dry_run": True},
            )
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=payload.snapshot_id,
            target_account_id=payload.target_account_id,
            remote_post_id=f"remote-{payload.idempotency_key[:16]}",
            remote_url=f"https://example.invalid/{self.platform.value}/{payload.idempotency_key[:16]}",
            published_at=utcnow() if outcome is PublishingStatus.PUBLISHED else None,
            status=outcome,
            request_id=f"mock-request-{self.publish_calls}",
            response_metadata={"mock": True},
        )

    async def get_publish_status(self, result: PublishResult) -> PublishResult:
        if result.status in {PublishingStatus.SUBMITTED, PublishingStatus.PROCESSING}:
            return result.model_copy(
                update={"status": PublishingStatus.PUBLISHED, "published_at": utcnow()}
            )
        return result

    async def delete(self, publication: ExternalPublication, *, approved_by: str) -> PublishResult:
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=publication.snapshot_id,
            target_account_id=publication.target_account_id,
            status=PublishingStatus.CANCELLED,
            request_id=f"mock-delete-{publication.publication_id}",
            response_metadata={"approved_by": approved_by},
        )

    async def fetch_metrics(
        self, publication: ExternalPublication, window: str
    ) -> ContentPerformanceSnapshot | None:
        return ContentPerformanceSnapshot(
            publication_id=publication.publication_id,
            campaign_id=publication.campaign_id,
            platform=publication.platform,
            measured_at=utcnow(),
            age_hours=24,
            window=window,
            impressions=_as_int(self._metrics.get("impressions")),
            views=_as_int(self._metrics.get("views")),
            likes=_as_int(self._metrics.get("likes")),
            comments=_as_int(self._metrics.get("comments")),
            shares=_as_int(self._metrics.get("shares")),
            saves=_as_int(self._metrics.get("saves")),
            clicks=_as_int(self._metrics.get("clicks")),
            conversions=_as_int(self._metrics.get("conversions")),
            revenue=_as_float(self._metrics.get("revenue")),
            completion_rate=_as_float(self._metrics.get("completion_rate")),
            raw_metrics={"mock": True},
        )


def _as_int(value: int | float | None) -> int | None:
    return int(value) if value is not None else None


def _as_float(value: int | float | None) -> float | None:
    return float(value) if value is not None else None
