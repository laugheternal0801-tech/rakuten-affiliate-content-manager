from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.creative_production.schemas import ContentCandidate, CreativePlatform
from app.publishing.schemas import ApprovedContentSnapshot, PublishingPlatform


def canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve(strict=True)
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_hash(candidate: ContentCandidate) -> str:
    return canonical_hash(
        {
            "candidate_id": candidate.candidate_id,
            "platform": candidate.platform.value,
            "content": candidate.content,
            "structured_content": candidate.structured_content,
            "claims_used": candidate.claims_used,
            "evidence_ids": candidate.evidence_ids,
            "revision_round": candidate.revision_round,
        }
    )


INTERNAL_SNAPSHOT_METADATA = {
    "content_brief_id",
    "evidence_ids",
    "claims_used",
    "variant",
    "revision_round",
    "target_account_display_name",
}


def snapshot_content_hash(snapshot: ApprovedContentSnapshot) -> str:
    public_metadata = {
        key: value
        for key, value in snapshot.metadata.items()
        if key not in INTERNAL_SNAPSHOT_METADATA
    }
    return canonical_hash(
        {
            "platform": snapshot.platform.value,
            "target_account_id": snapshot.target_account_id,
            "text": snapshot.text,
            "caption": snapshot.caption,
            "title": snapshot.title,
            "description": snapshot.description,
            "assets": [
                {
                    "sha256": asset.sha256,
                    "mime_type": asset.mime_type,
                }
                for asset in snapshot.assets
            ],
            "hashtags": snapshot.hashtags,
            "links": snapshot.links,
            "metadata": public_metadata,
        }
    )


def snapshot_integrity_hash(snapshot: ApprovedContentSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    data.pop("integrity_hash", None)
    data.pop("created_at", None)
    return canonical_hash(data)


def idempotency_hash(
    campaign_id: str,
    snapshot_id: str,
    platform: PublishingPlatform,
    target_account_id: str,
    scheduled_iso: str,
) -> str:
    return canonical_hash(
        {
            "campaign_id": campaign_id,
            "snapshot_id": snapshot_id,
            "platform": platform.value,
            "target_account_id": target_account_id,
            "scheduled_at": scheduled_iso,
        }
    )


def publishing_platform(platform: CreativePlatform) -> PublishingPlatform | None:
    if platform is CreativePlatform.X:
        return PublishingPlatform.X
    if platform.value.startswith("instagram"):
        return PublishingPlatform.INSTAGRAM
    if platform is CreativePlatform.TIKTOK:
        return PublishingPlatform.TIKTOK
    if platform.value.startswith("youtube"):
        return PublishingPlatform.YOUTUBE
    if platform is CreativePlatform.PINTEREST:
        return PublishingPlatform.PINTEREST
    return None
