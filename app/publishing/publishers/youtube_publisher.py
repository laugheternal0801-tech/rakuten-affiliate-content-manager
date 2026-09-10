from __future__ import annotations

from app.publishing.publishers.base import DryRunOfficialPublisher
from app.publishing.schemas import (
    CapabilityAvailability,
    PublisherCapability,
    PublishingPlatform,
    PublishPayload,
)


class YouTubePublisher(DryRunOfficialPublisher):
    key = "youtube_official"
    platform = PublishingPlatform.YOUTUBE

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.NOT_CONFIGURED,
            operations=["long_video", "short_video", "resumable_upload", "status"],
            content_types=["video", "short"],
            max_title_length=100,
            max_description_length=5_000,
            max_media_count=1,
            supported_mime_types=["video/mp4", "video/quicktime", "video/webm"],
            required_scopes=["https://www.googleapis.com/auth/youtube.upload"],
            constraints={
                "resumable_upload": True,
                "processing_status_polling": True,
                "unverified_project_public_upload_unavailable": True,
            },
        )

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        return {
            "snippet": {
                "title": payload.title,
                "description": payload.description or payload.caption,
                "tags": payload.hashtags,
                "categoryId": payload.platform_metadata.get("category_id", "22"),
            },
            "status": {
                "privacyStatus": payload.platform_metadata.get("privacy", "private"),
                "selfDeclaredMadeForKids": payload.platform_metadata.get("made_for_kids", False),
                "containsSyntheticMedia": payload.disclosure.ai_generated,
            },
            "upload": {
                "resumable": True,
                "asset_id": payload.assets[0].asset_id if payload.assets else None,
            },
        }
