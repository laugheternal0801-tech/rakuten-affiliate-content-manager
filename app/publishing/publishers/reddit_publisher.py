from __future__ import annotations

from app.publishing.publishers.base import DryRunOfficialPublisher
from app.publishing.schemas import (
    CapabilityAvailability,
    PublisherCapability,
    PublishingPlatform,
    PublishPayload,
)


class RedditPublisher(DryRunOfficialPublisher):
    key = "reddit_official"
    platform = PublishingPlatform.REDDIT

    def base_capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            provider=self.key,
            availability=CapabilityAvailability.MANUAL_REVIEW_REQUIRED,
            operations=["self_post", "link_post", "delete", "metrics"],
            content_types=["text", "link"],
            max_title_length=300,
            required_scopes=["identity", "read", "submit"],
            constraints={
                "subreddit_required": True,
                "subreddit_rules_preflight": True,
                "community_relevance_review": True,
            },
        )

    def build_request_payload(self, payload: PublishPayload) -> dict[str, object]:
        return {
            "sr": payload.platform_metadata.get("subreddit"),
            "title": payload.title,
            "kind": "link" if payload.links else "self",
            "text": payload.text,
            "url": payload.links[0] if payload.links else None,
            "validate_on_submit": True,
        }
