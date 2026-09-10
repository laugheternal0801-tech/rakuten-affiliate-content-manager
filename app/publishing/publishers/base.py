from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol

from app.publishing.hashing import canonical_hash
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


class PublisherError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        outcome_unknown: bool = False,
        remote_post_id: str = "",
        request_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.remote_post_id = remote_post_id
        self.request_id = request_id
        self.metadata = metadata or {}


class PublishingProvider(Protocol):
    key: str
    platform: PublishingPlatform

    async def health_check(self) -> PublisherCapability: ...

    async def get_capabilities(
        self, account: SocialAccountConnection | None = None
    ) -> PublisherCapability: ...

    async def validate_credentials(self, account: SocialAccountConnection) -> bool: ...

    async def validate_content(self, payload: PublishPayload) -> list[str]: ...

    async def publish(self, payload: PublishPayload) -> PublishResult: ...

    async def get_publish_status(self, result: PublishResult) -> PublishResult: ...

    async def delete(
        self, publication: ExternalPublication, *, approved_by: str
    ) -> PublishResult: ...

    async def fetch_metrics(
        self, publication: ExternalPublication, window: str
    ) -> ContentPerformanceSnapshot | None: ...


class DryRunOfficialPublisher(ABC):
    """Builds official-API-shaped payloads but cannot make a live request."""

    live_adapter_connected = False

    def __init__(
        self,
        *,
        dry_run: bool,
        publishing_enabled: bool,
        external_api_enabled: bool,
        capability_overrides: dict[str, object] | None = None,
    ) -> None:
        self._dry_run = dry_run
        self._publishing_enabled = publishing_enabled
        self._external_api_enabled = external_api_enabled
        self._capability_overrides = capability_overrides or {}

    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    @abstractmethod
    def platform(self) -> PublishingPlatform: ...

    @abstractmethod
    def base_capability(self) -> PublisherCapability: ...

    @abstractmethod
    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]: ...

    async def health_check(self) -> PublisherCapability:
        return await self.get_capabilities()

    async def get_capabilities(
        self, account: SocialAccountConnection | None = None
    ) -> PublisherCapability:
        capability = self.base_capability()
        overrides = {
            key: value
            for key, value in self._capability_overrides.items()
            if key in PublisherCapability.model_fields
        }
        if self._dry_run:
            availability = CapabilityAvailability.DRY_RUN
            enabled = True
            message = "Payload/Preflight検証専用。外部APIは呼びません。"
        elif not self._publishing_enabled or not self._external_api_enabled:
            availability = CapabilityAvailability.DISABLED
            enabled = False
            message = "Publishing kill switchまたは外部API gateがOFFです。"
        elif not self.live_adapter_connected:
            availability = CapabilityAvailability.NOT_CONFIGURED
            enabled = False
            message = "Live API adapterは未接続です。"
        else:
            availability = CapabilityAvailability.AVAILABLE
            enabled = True
            message = "Live API adapter available"
        if account and not set(capability.required_scopes) <= set(account.scopes):
            availability = CapabilityAvailability.PERMISSION_REQUIRED
            enabled = False
            message = "Target Accountに必要なScopeがありません。"
        return capability.model_copy(
            update={
                **overrides,
                "availability": availability,
                "enabled": enabled,
                "message": message,
                "refreshed_at": utcnow(),
            }
        )

    async def validate_credentials(self, account: SocialAccountConnection) -> bool:
        if self._dry_run:
            return True
        capability = await self.get_capabilities(account)
        return bool(
            capability.enabled
            and account.credential_reference
            and account.status is CapabilityAvailability.AVAILABLE
        )

    async def validate_content(self, payload: PublishPayload) -> list[str]:
        capability = await self.get_capabilities()
        problems: list[str] = []
        content = payload.caption or payload.text
        if capability.max_text_length and len(content) > capability.max_text_length:
            problems.append(f"本文が上限{capability.max_text_length}文字を超えています。")
        if capability.max_title_length and len(payload.title) > capability.max_title_length:
            problems.append(f"Titleが上限{capability.max_title_length}文字を超えています。")
        if (
            capability.max_description_length
            and len(payload.description) > capability.max_description_length
        ):
            problems.append(
                f"Descriptionが上限{capability.max_description_length}文字を超えています。"
            )
        if capability.max_media_count and len(payload.assets) > capability.max_media_count:
            problems.append(f"Media数が上限{capability.max_media_count}件を超えています。")
        unsupported = [
            asset.mime_type
            for asset in payload.assets
            if capability.supported_mime_types
            and asset.mime_type not in capability.supported_mime_types
        ]
        if unsupported:
            problems.append("未対応MIME type: " + ", ".join(sorted(set(unsupported))))
        return problems

    async def publish(self, payload: PublishPayload) -> PublishResult:
        if not payload.dry_run or not self._dry_run:
            raise PublisherError(
                "Live API adapterはまだ接続されていません。",
                code="LIVE_ADAPTER_NOT_CONNECTED",
                retryable=False,
            )
        request_payload = self.build_request_payload(payload)
        return PublishResult(
            platform=self.platform,
            provider=self.key,
            snapshot_id=payload.snapshot_id,
            target_account_id=payload.target_account_id,
            status=PublishingStatus.DRY_RUN_COMPLETED,
            request_id=f"dry-{payload.job_id}",
            response_metadata={
                "dry_run": True,
                "payload_hash": canonical_hash(request_payload),
                "payload_keys": sorted(request_payload),
            },
        )

    async def get_publish_status(self, result: PublishResult) -> PublishResult:
        return result

    async def delete(self, publication: ExternalPublication, *, approved_by: str) -> PublishResult:
        raise PublisherError(
            "Remote delete adapterは未接続です。削除には別のHuman Approvalが必要です。",
            code="DELETE_NOT_CONNECTED",
            retryable=False,
        )

    async def fetch_metrics(
        self, publication: ExternalPublication, window: str
    ) -> ContentPerformanceSnapshot | None:
        return None
