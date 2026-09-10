from __future__ import annotations

from app.publishing.publishers.base import DryRunOfficialPublisher
from app.publishing.schemas import (
    CapabilityAvailability,
    PublisherCapability,
    PublishingPlatform,
    PublishPayload,
)


class GenericWebPublisher(DryRunOfficialPublisher):
    key = "generic_official_api_only"
    platform = PublishingPlatform.GENERIC

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.NOT_CONFIGURED,
            operations=[],
            content_types=[],
            constraints={
                "official_api_required": True,
                "browser_automation_forbidden": True,
            },
        )

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        return {
            "text": payload.text,
            "assets": [asset.asset_id for asset in payload.assets],
            "metadata": payload.platform_metadata,
        }
