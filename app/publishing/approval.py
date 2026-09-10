from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy.orm import Session

from app.creative_production.models import (
    CreativeApprovalRow,
    CreativePackageRow,
)
from app.creative_production.repositories import get_package
from app.creative_production.schemas import (
    ContentCandidate,
    ContentPackage,
    CreativePlatform,
    CreativeStatus,
)
from app.publishing.hashing import (
    candidate_hash,
    canonical_hash,
    file_sha256,
    publishing_platform,
    snapshot_content_hash,
    snapshot_integrity_hash,
)
from app.publishing.repositories import (
    get_account,
    get_approval,
    get_snapshot,
    invalidate_approval,
    list_approvals,
    list_snapshots,
    save_approval,
    save_audit,
    save_notification,
    save_snapshot,
)
from app.publishing.schemas import (
    ApprovalRecord,
    ApprovalRecordStatus,
    ApprovedContentSnapshot,
    AuditEventType,
    AuditLog,
    NotificationEvent,
    NotificationSeverity,
    PublishingPlatform,
    PublishingStatus,
    SnapshotAsset,
)

LINK_RE = re.compile(r"https?://[^\s<>()]+")
HASHTAG_RE = re.compile(r"(?<!\w)#[\w一-龥ぁ-んァ-ヴー]+")
REVISION_REASONS = {
    "too_generic",
    "too_salesy",
    "weak_hook",
    "wrong_visual",
    "fact_issue",
    "brand_mismatch",
    "other",
}


def _strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


class CandidateSnapshotFields(TypedDict):
    text: str
    caption: str
    title: str
    description: str
    hashtags: list[str]
    links: list[str]


def _candidate_fields(candidate: ContentCandidate) -> CandidateSnapshotFields:
    structured = candidate.structured_content
    caption = str(structured.get("caption", "")).strip()
    title = str(structured.get("title", "")).strip()
    description = str(structured.get("description", "")).strip()
    hashtags = _strings(structured.get("hashtags"))
    if not hashtags:
        hashtags = HASHTAG_RE.findall(candidate.content)
    link_values = _strings(structured.get("links"))
    links = list(dict.fromkeys(link_values + LINK_RE.findall(candidate.content)))
    return {
        "text": candidate.content,
        "caption": caption,
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "links": links,
    }


class PublishingApprovalService:
    """Creates immutable, account-bound snapshots from explicitly selected content."""

    def approve_and_lock(
        self,
        session: Session,
        package_id: str,
        selected_platforms: list[CreativePlatform],
        approved_by: str,
        target_accounts: Mapping[CreativePlatform, str],
        *,
        platform_assets: Mapping[CreativePlatform, list[str]] | None = None,
        platform_metadata: Mapping[CreativePlatform, dict[str, object]] | None = None,
    ) -> tuple[ApprovalRecord, list[ApprovedContentSnapshot]]:
        package = get_package(session, package_id)
        if package is None:
            raise LookupError(f"Content package {package_id} was not found.")
        if not approved_by.strip():
            raise ValueError("承認者名が必要です。")
        selected = list(dict.fromkeys(selected_platforms))
        if not selected:
            raise ValueError("承認対象Platformを1つ以上選択してください。")

        publishable: list[tuple[CreativePlatform, PublishingPlatform, ContentCandidate]] = []
        for creative_platform in selected:
            candidate = package.final_content.get(creative_platform)
            if candidate is None:
                raise ValueError(f"{creative_platform.value}のFinal Contentがありません。")
            publish_platform = publishing_platform(creative_platform)
            if publish_platform is None:
                raise ValueError(f"{creative_platform.value}はPublishing対象外です。")
            publishable.append((creative_platform, publish_platform, candidate))

        active_ids = {
            item.approval_id for item in list_approvals(session, package_id, active_only=True)
        }
        active_candidate_ids = {
            snapshot.source_candidate_id
            for snapshot in list_snapshots(session, package_id)
            if snapshot.approval_id in active_ids
        }
        duplicate_candidates = {
            candidate.candidate_id
            for _, _, candidate in publishable
            if candidate.candidate_id in active_candidate_ids
        }
        if duplicate_candidates:
            raise ValueError("選択Contentには有効なApproval Snapshotが既にあります。")

        existing_versions = [item.version for item in list_snapshots(session, package_id)]
        lock_version = max(package.version, max(existing_versions, default=0) + 1)
        approval_id = (
            f"APR-{canonical_hash([package_id, lock_version, approved_by, utc_iso()])[:32]}"
        )
        snapshots: list[ApprovedContentSnapshot] = []
        all_asset_hashes: list[str] = []
        assets_by_id = {asset.asset_id: asset for asset in package.assets}
        platform_assets = platform_assets or {}
        platform_metadata = platform_metadata or {}

        for creative_platform, publish_platform, candidate in publishable:
            connection_id = target_accounts.get(creative_platform, "")
            account = get_account(session, connection_id)
            if account is None:
                raise ValueError(f"{creative_platform.value}のTarget Accountを明示してください。")
            if account.platform is not publish_platform:
                raise ValueError(
                    f"Target Account {account.display_name} は"
                    f"{publish_platform.value}用ではありません。"
                )

            snapshot_assets: list[SnapshotAsset] = []
            for asset_id in platform_assets.get(creative_platform, []):
                asset = assets_by_id.get(asset_id)
                if asset is None:
                    raise ValueError(f"Asset {asset_id} はContent Packageに存在しません。")
                if asset.is_placeholder:
                    raise ValueError("Placeholder Assetは公開承認できません。")
                digest = file_sha256(asset.file_path)
                snapshot_assets.append(
                    SnapshotAsset(
                        asset_id=asset.asset_id,
                        file_path=asset.file_path,
                        mime_type=asset.mime_type,
                        sha256=digest,
                        is_placeholder=False,
                        metadata={
                            "provider": asset.provider,
                            "model": asset.model,
                            **asset.parameters,
                        },
                    )
                )
                all_asset_hashes.append(digest)

            fields = _candidate_fields(candidate)
            metadata = {
                "creative_platform": creative_platform.value,
                "content_brief_id": candidate.content_brief_id,
                "evidence_ids": candidate.evidence_ids,
                "claims_used": candidate.claims_used,
                "variant": candidate.variant,
                "revision_round": candidate.revision_round,
                "target_external_account_id": account.account_id,
                "target_account_display_name": account.display_name,
                "providers": sorted(
                    {
                        str(asset.metadata.get("provider", ""))
                        for asset in snapshot_assets
                        if asset.metadata.get("provider")
                    }
                    | {candidate.provider}
                ),
                "models": sorted(
                    {
                        str(asset.metadata.get("model", ""))
                        for asset in snapshot_assets
                        if asset.metadata.get("model")
                    }
                    | {candidate.model}
                ),
                **platform_metadata.get(creative_platform, {}),
            }
            snapshot = ApprovedContentSnapshot(
                approval_id=approval_id,
                campaign_id=package.campaign.campaign_id,
                content_package_id=package.package_id,
                version=lock_version,
                platform=publish_platform,
                target_account_id=account.connection_id,
                source_candidate_id=candidate.candidate_id,
                source_candidate_hash=candidate_hash(candidate),
                text=str(fields["text"]),
                caption=str(fields["caption"]),
                title=str(fields["title"]),
                description=str(fields["description"]),
                assets=snapshot_assets,
                hashtags=list(fields["hashtags"]),
                links=list(fields["links"]),
                metadata=metadata,
                content_hash="0" * 64,
                integrity_hash="0" * 64,
            )
            snapshot = snapshot.model_copy(update={"content_hash": snapshot_content_hash(snapshot)})
            snapshot = snapshot.model_copy(
                update={"integrity_hash": snapshot_integrity_hash(snapshot)}
            )
            snapshots.append(snapshot)

        approval = ApprovalRecord(
            approval_id=approval_id,
            content_package_id=package.package_id,
            content_version=lock_version,
            approved_platforms=list(dict.fromkeys(item.platform for item in snapshots)),
            approved_by=approved_by.strip(),
            content_hash=canonical_hash([item.content_hash for item in snapshots]),
            asset_hashes=list(dict.fromkeys(all_asset_hashes)),
        )
        save_approval(session, approval)
        for snapshot in snapshots:
            save_snapshot(session, snapshot)
            save_audit(
                session,
                AuditLog(
                    event_type=AuditEventType.SNAPSHOT_LOCKED,
                    actor=approved_by.strip(),
                    campaign_id=snapshot.campaign_id,
                    content_package_id=package.package_id,
                    snapshot_id=snapshot.snapshot_id,
                    platform=snapshot.platform,
                    target_account_id=snapshot.target_account_id,
                    status=PublishingStatus.LOCKED.value,
                    metadata={"content_hash": snapshot.content_hash},
                ),
            )
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.HUMAN_APPROVED,
                actor=approved_by.strip(),
                campaign_id=package.campaign.campaign_id,
                content_package_id=package.package_id,
                status=PublishingStatus.APPROVED.value,
                metadata={
                    "approval_id": approval.approval_id,
                    "snapshot_ids": [item.snapshot_id for item in snapshots],
                },
            ),
        )

        all_publishable_candidates = {
            candidate.candidate_id
            for platform, candidate in package.final_content.items()
            if publishing_platform(platform) is not None
        }
        if all_publishable_candidates <= active_candidate_ids | {
            item.source_candidate_id for item in snapshots
        }:
            self._mark_package_approved(session, package, approved_by)
        return approval, snapshots

    def validate_snapshot_lock(self, session: Session, snapshot_id: str) -> tuple[bool, list[str]]:
        snapshot = get_snapshot(session, snapshot_id)
        if snapshot is None:
            return False, ["Approved Snapshotが存在しません。"]
        problems: list[str] = []
        approval = get_approval(session, snapshot.approval_id)
        if approval is None or approval.status is not ApprovalRecordStatus.ACTIVE:
            problems.append("Approvalが無効です。")
        if snapshot_content_hash(snapshot) != snapshot.content_hash:
            problems.append("Snapshot content hashが一致しません。")
        if snapshot_integrity_hash(snapshot) != snapshot.integrity_hash:
            problems.append("Snapshot integrity hashが一致しません。")
        for asset in snapshot.assets:
            try:
                current_hash = file_sha256(asset.file_path)
            except (FileNotFoundError, OSError):
                problems.append(f"Asset {asset.asset_id}を読み込めません。")
                continue
            if current_hash != asset.sha256:
                problems.append(f"Asset {asset.asset_id}は承認後に変更されています。")

        package = get_package(session, snapshot.content_package_id)
        creative_value = str(snapshot.metadata.get("creative_platform", ""))
        if package is None:
            problems.append("元Content Packageが存在しません。")
        else:
            try:
                creative_platform = CreativePlatform(creative_value)
            except ValueError:
                problems.append("元Creative Platformを特定できません。")
            else:
                current = package.final_content.get(creative_platform)
                if current is None or current.candidate_id != snapshot.source_candidate_id:
                    problems.append("承認対象Candidateが差し替えられています。")
                elif candidate_hash(current) != snapshot.source_candidate_hash:
                    problems.append("承認後にContentが変更されています。")
        return not problems, problems

    def invalidate_modified_approvals(
        self, session: Session, package_id: str, actor: str = "system"
    ) -> list[ApprovalRecord]:
        invalidated: list[ApprovalRecord] = []
        for approval in list_approvals(session, package_id, active_only=True):
            problems: list[str] = []
            approval_snapshots = [
                snapshot
                for snapshot in list_snapshots(session, package_id)
                if snapshot.approval_id == approval.approval_id
            ]
            for snapshot in approval_snapshots:
                valid, snapshot_problems = self.validate_snapshot_lock(
                    session, snapshot.snapshot_id
                )
                if not valid:
                    problems.extend(snapshot_problems)
            if problems:
                reason = " / ".join(dict.fromkeys(problems))
                updated = invalidate_approval(session, approval.approval_id, reason)
                invalidated.append(updated)
                save_audit(
                    session,
                    AuditLog(
                        event_type=AuditEventType.APPROVAL_INVALIDATED,
                        actor=actor,
                        content_package_id=package_id,
                        status=PublishingStatus.REQUIRES_REAPPROVAL.value,
                        error=reason,
                    ),
                )
                package = get_package(session, package_id)
                save_notification(
                    session,
                    NotificationEvent(
                        dedupe_key=canonical_hash(
                            ["reapproval_required", approval.approval_id, reason]
                        ),
                        event_type="reapproval_required",
                        severity=NotificationSeverity.WARNING,
                        title="承認後の変更を検出しました",
                        message=reason,
                        campaign_id=package.campaign.campaign_id if package else "",
                        payload={
                            "content_package_id": package_id,
                            "approval_id": approval.approval_id,
                        },
                    ),
                )
        return invalidated

    def request_revision(
        self,
        session: Session,
        package_id: str,
        requested_by: str,
        reason_code: str,
        feedback: str = "",
    ) -> list[ApprovalRecord]:
        actor = requested_by.strip()
        if not actor:
            raise ValueError("Revision依頼者が必要です。")
        normalized_reason = reason_code.strip().lower()
        if normalized_reason not in REVISION_REASONS:
            raise ValueError("Revision reasonが不正です。")
        row = session.get(CreativePackageRow, package_id)
        package = get_package(session, package_id)
        if row is None or package is None:
            raise LookupError(f"Content package {package_id} was not found.")
        active = list_approvals(session, package_id, active_only=True)
        if not active:
            raise ValueError("Revision対象のActive Approvalがありません。")

        detail = feedback.strip()
        reason = normalized_reason + (f": {detail}" if detail else "")
        invalidated = [
            invalidate_approval(session, approval.approval_id, reason) for approval in active
        ]
        updated = package.model_copy(
            update={"status": CreativeStatus.REVISING, "updated_at": datetime.now(UTC)}
        )
        row.status = CreativeStatus.REVISING.value
        row.package_json = updated.model_dump(mode="json")
        row.updated_at = updated.updated_at
        session.add(
            CreativeApprovalRow(
                package_id=package_id,
                decision="request_revision",
                reviewer=actor,
                feedback=reason,
            )
        )
        save_audit(
            session,
            AuditLog(
                event_type=AuditEventType.REVISION_REQUESTED,
                actor=actor,
                campaign_id=package.campaign.campaign_id,
                content_package_id=package_id,
                status=PublishingStatus.REQUIRES_REAPPROVAL.value,
                metadata={
                    "reason_code": normalized_reason,
                    "feedback": detail,
                    "invalidated_approval_ids": [item.approval_id for item in invalidated],
                },
            ),
        )
        save_notification(
            session,
            NotificationEvent(
                dedupe_key=canonical_hash(
                    ["revision_requested", package_id, *[item.approval_id for item in active]]
                ),
                event_type="reapproval_required",
                severity=NotificationSeverity.WARNING,
                title="Revisionが依頼されました",
                message=reason,
                campaign_id=package.campaign.campaign_id,
                payload={
                    "content_package_id": package_id,
                    "reason_code": normalized_reason,
                },
            ),
        )
        session.flush()
        return invalidated

    @staticmethod
    def _mark_package_approved(session: Session, package: ContentPackage, approved_by: str) -> None:
        row = session.get(CreativePackageRow, package.package_id)
        if row is None:
            return
        updated = package.model_copy(
            update={"status": CreativeStatus.APPROVED, "updated_at": datetime.now(UTC)}
        )
        row.status = CreativeStatus.APPROVED.value
        row.package_json = updated.model_dump(mode="json")
        row.updated_at = updated.updated_at
        session.add(
            CreativeApprovalRow(
                package_id=package.package_id,
                decision="approve",
                reviewer=approved_by,
                feedback="Publishing snapshot locked",
            )
        )
        session.flush()


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()
