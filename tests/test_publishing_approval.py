from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.creative_production.models import CreativePackageRow
from app.creative_production.schemas import ContentPackage, CreativePlatform
from app.publishing.api_contracts import PUBLISHING_API_ROUTES
from app.publishing.approval import PublishingApprovalService
from app.publishing.models import ApprovedSnapshotRow
from app.publishing.scheduling import PublishingScheduler
from app.publishing.schemas import (
    ApprovalRecordStatus,
    PublishingPlatform,
)
from tests.publishing_support import PublishingFactory


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_human_approval_creates_account_bound_immutable_snapshot(
    publishing_factory: PublishingFactory,
) -> None:
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)

    snapshot = publishing_factory.approve(
        package, [CreativePlatform.X], {CreativePlatform.X: account}
    )[0]
    valid, problems = PublishingApprovalService().validate_snapshot_lock(
        publishing_factory.session, snapshot.snapshot_id
    )

    assert valid is True
    assert problems == []
    assert snapshot.target_account_id == account.connection_id
    assert snapshot.metadata["evidence_ids"] == ["EV-PUB-1"]
    assert len(snapshot.content_hash) == 64
    assert len(snapshot.integrity_hash) == 64


def test_approval_rejects_wrong_platform_account(
    publishing_factory: PublishingFactory,
) -> None:
    package, _ = publishing_factory.package([CreativePlatform.X])
    instagram = publishing_factory.account(PublishingPlatform.INSTAGRAM)

    with pytest.raises(ValueError, match="x用ではありません"):
        publishing_factory.approve(package, [CreativePlatform.X], {CreativePlatform.X: instagram})


def test_draft_change_invalidates_approval_and_requires_reapproval(
    publishing_factory: PublishingFactory,
) -> None:
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    first = publishing_factory.approve(
        package, [CreativePlatform.X], {CreativePlatform.X: account}
    )[0]
    row = publishing_factory.session.get(CreativePackageRow, package.package_id)
    assert row is not None
    stored = ContentPackage.model_validate(row.package_json)
    current = stored.final_content[CreativePlatform.X]
    changed = current.model_copy(update={"content": current.content + " 変更"})
    final_content = dict(stored.final_content)
    final_content[CreativePlatform.X] = changed
    candidates = [
        changed if item.candidate_id == changed.candidate_id else item for item in stored.candidates
    ]
    modified = stored.model_copy(update={"final_content": final_content, "candidates": candidates})
    row.package_json = modified.model_dump(mode="json")
    publishing_factory.session.flush()

    invalidated = PublishingApprovalService().invalidate_modified_approvals(
        publishing_factory.session, package.package_id
    )

    assert len(invalidated) == 1
    assert invalidated[0].status is ApprovalRecordStatus.INVALIDATED
    valid, problems = PublishingApprovalService().validate_snapshot_lock(
        publishing_factory.session, first.snapshot_id
    )
    assert valid is False
    assert any("変更" in problem or "無効" in problem for problem in problems)

    second = publishing_factory.approve(
        modified, [CreativePlatform.X], {CreativePlatform.X: account}
    )[0]
    assert second.version > first.version
    assert second.content_hash != first.content_hash


def test_asset_change_invalidates_locked_snapshot(
    publishing_factory: PublishingFactory,
) -> None:
    platform = CreativePlatform.PINTEREST
    package, assets = publishing_factory.package([platform], with_assets=True)
    account = publishing_factory.account_for_creative(platform)
    snapshot = publishing_factory.approve(package, [platform], {platform: account}, assets)[0]

    with open(snapshot.assets[0].file_path, "ab") as handle:
        handle.write(b"changed")

    valid, problems = PublishingApprovalService().validate_snapshot_lock(
        publishing_factory.session, snapshot.snapshot_id
    )
    assert valid is False
    assert any("Asset" in problem and "変更" in problem for problem in problems)


def test_snapshot_json_tamper_is_detected(publishing_factory: PublishingFactory) -> None:
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    snapshot = publishing_factory.approve(
        package, [CreativePlatform.X], {CreativePlatform.X: account}
    )[0]
    row = publishing_factory.session.get(ApprovedSnapshotRow, snapshot.snapshot_id)
    assert row is not None
    tampered = dict(row.snapshot_json)
    tampered["text"] = "承認後に書き換えた本文"
    row.snapshot_json = tampered
    publishing_factory.session.flush()

    valid, problems = PublishingApprovalService().validate_snapshot_lock(
        publishing_factory.session, snapshot.snapshot_id
    )
    assert valid is False
    assert any("hash" in problem for problem in problems)


def test_schedule_requires_existing_approved_snapshot(
    publishing_factory: PublishingFactory,
) -> None:
    with pytest.raises(LookupError, match="Snapshot"):
        PublishingScheduler(_settings()).create_approved_schedule(
            publishing_factory.session,
            "SNP-missing",
            datetime.now(UTC) + timedelta(minutes=5),
            "Asia/Tokyo",
            "reviewer",
        )


def test_human_revision_reason_invalidates_active_approval(
    publishing_factory: PublishingFactory,
) -> None:
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    snapshot = publishing_factory.approve(
        package,
        [CreativePlatform.X],
        {CreativePlatform.X: account},
    )[0]

    invalidated = PublishingApprovalService().request_revision(
        publishing_factory.session,
        package.package_id,
        "human-reviewer",
        "weak_hook",
        "冒頭を具体化してください。",
    )
    valid, problems = PublishingApprovalService().validate_snapshot_lock(
        publishing_factory.session, snapshot.snapshot_id
    )

    assert invalidated[0].status is ApprovalRecordStatus.INVALIDATED
    assert "weak_hook" in invalidated[0].reason
    assert valid is False
    assert "Approvalが無効です。" in problems


def test_schedule_rejects_naive_datetime(publishing_factory: PublishingFactory) -> None:
    package, _ = publishing_factory.package([CreativePlatform.X])
    account = publishing_factory.account_for_creative(CreativePlatform.X)
    snapshot = publishing_factory.approve(
        package, [CreativePlatform.X], {CreativePlatform.X: account}
    )[0]

    with pytest.raises(ValueError, match="Timezone-aware"):
        PublishingScheduler(_settings()).create_approved_schedule(
            publishing_factory.session,
            snapshot.snapshot_id,
            datetime.now(),
            "Asia/Tokyo",
            "reviewer",
        )


def test_publishing_api_contract_declares_required_routes() -> None:
    assert "POST /content/{id}/approve" in PUBLISHING_API_ROUTES
    assert "POST /publications/{id}/publish" in PUBLISHING_API_ROUTES
    assert "POST /publishing/pause" in PUBLISHING_API_ROUTES
    assert "GET /campaigns/{id}/performance" in PUBLISHING_API_ROUTES
