from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings
from app.publishing.approval import PublishingApprovalService
from app.publishing.media_probe import probe_media
from app.publishing.publishers.base import PublishingProvider
from app.publishing.repositories import (
    get_account,
    get_publication_by_key,
    get_snapshot,
    list_publications,
)
from app.publishing.schemas import (
    CapabilityAvailability,
    CheckResult,
    PreflightCheck,
    PreflightResult,
    PreflightStatus,
    PublicationJob,
    PublishingControl,
    PublishingPlatform,
    PublishingStatus,
    PublishPayload,
)
from app.publishing.social_connections import SocialConnectionService


class PublishingPreflightValidator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._approval = PublishingApprovalService()

    async def validate(
        self,
        session: Session,
        job: PublicationJob,
        provider: PublishingProvider,
        control: PublishingControl,
        payload: PublishPayload,
    ) -> PreflightResult:
        checks: list[PreflightCheck] = []
        snapshot = get_snapshot(session, job.snapshot_id)
        if snapshot is None:
            checks.append(_fail("snapshot", "Approved Snapshotが存在しません。"))
            return _result(job, checks)

        valid_lock, lock_problems = self._approval.validate_snapshot_lock(
            session, snapshot.snapshot_id
        )
        checks.append(
            PreflightCheck(
                name="approval_and_lock",
                result=CheckResult.PASS if valid_lock else CheckResult.FAIL,
                message=(
                    "Human ApprovalとVersion Lockは有効です。"
                    if valid_lock
                    else " / ".join(lock_problems)
                ),
            )
        )
        account = get_account(session, job.target_account_id)
        if account is None:
            checks.append(_fail("target_account", "Target Accountが存在しません。"))
        elif (
            account.connection_id != snapshot.target_account_id
            or account.connection_id != job.target_account_id
            or account.platform is not job.platform
        ):
            checks.append(_fail("target_account", "承認済みTarget Accountと一致しません。"))
        else:
            checks.append(
                PreflightCheck(
                    name="target_account",
                    result=CheckResult.PASS,
                    message=f"Target Account: {account.display_name}",
                    details={"account_id": account.account_id},
                )
            )

        token_refresh_error = ""
        if account and not payload.dry_run:
            connection_service: SocialConnectionService | None = None
            try:
                connection_service = SocialConnectionService(self._settings)
                if connection_service.should_refresh(account):
                    account = await asyncio.to_thread(
                        connection_service.refresh_account,
                        account,
                    )
                    connection_service.save_refreshed(
                        session,
                        account,
                        actor="preflight_auto_refresh",
                    )
                    checks.append(_pass("token_refresh", "OAuth Tokenを自動更新しました。"))
                if account.platform is PublishingPlatform.TIKTOK:
                    account = await asyncio.to_thread(
                        connection_service.refresh_tiktok_creator_info,
                        account,
                    )
                    connection_service.save_refreshed(
                        session,
                        account,
                        actor="preflight_creator_info_refresh",
                    )
                    checks.append(
                        _pass("tiktok_creator_info_refresh", "最新Creator Infoを同期しました。")
                    )
            except Exception as exc:
                token_refresh_error = str(exc)
            finally:
                if connection_service is not None:
                    await asyncio.to_thread(connection_service.close)

        if control.globally_paused:
            checks.append(_fail("global_pause", "GLOBAL PUBLISHING PAUSEが有効です。"))
        else:
            checks.append(_pass("global_pause", "Global pauseは無効です。"))

        if payload.dry_run:
            checks.append(
                PreflightCheck(
                    name="execution_mode",
                    result=CheckResult.PASS,
                    message="DRY RUN: 外部APIは呼ばれません。",
                )
            )
        elif not control.publishing_enabled or not self._settings.publishing_external_api_enabled:
            checks.append(_fail("kill_switch", "Production publishing gateがOFFです。"))
        else:
            checks.append(_pass("kill_switch", "Production publishing gateはONです。"))

        capability = await provider.get_capabilities(account)
        allowed_availability = {
            CapabilityAvailability.AVAILABLE,
            CapabilityAvailability.DRY_RUN,
        }
        if capability.availability not in allowed_availability:
            checks.append(
                _fail(
                    "provider_capability",
                    f"Provider capability: {capability.availability.value}。{capability.message}",
                )
            )
        else:
            checks.append(_pass("provider_capability", capability.message))

        if account:
            if token_refresh_error:
                checks.append(
                    _fail(
                        "credentials", f"OAuth Tokenの自動更新に失敗しました: {token_refresh_error}"
                    )
                )
            elif account.token_expiry and account.token_expiry <= datetime.now(UTC):
                checks.append(_fail("token_expiry", "OAuth Tokenが期限切れです。"))
            elif payload.dry_run:
                checks.append(
                    PreflightCheck(
                        name="credentials",
                        result=CheckResult.WARNING,
                        message="Dry RunのためCredential検証を省略しました。",
                    )
                )
            elif not await provider.validate_credentials(account):
                checks.append(_fail("credentials", "CredentialまたはScopeが無効です。"))
            else:
                checks.append(_pass("credentials", "Credential validation passed."))

        content_problems = await provider.validate_content(payload)
        checks.append(
            PreflightCheck(
                name="content_limits",
                result=CheckResult.FAIL if content_problems else CheckResult.PASS,
                message=" / ".join(content_problems)
                if content_problems
                else "文字数・Media数は範囲内です。",
            )
        )
        checks.extend(self._asset_checks(payload))
        checks.extend(self._url_checks(payload))
        checks.extend(self._platform_checks(payload, account.metadata if account else {}))
        checks.append(self._duplicate_check(session, job, snapshot.content_hash))
        return _result(job, checks)

    @staticmethod
    def _asset_checks(payload: PublishPayload) -> list[PreflightCheck]:
        problems: list[str] = []
        metadata_missing: list[str] = []
        details: dict[str, object] = {}
        suffixes = {
            "image/jpeg": {".jpg", ".jpeg"},
            "image/png": {".png"},
            "image/webp": {".webp"},
            "image/gif": {".gif"},
            "video/mp4": {".mp4"},
            "video/quicktime": {".mov"},
            "video/webm": {".webm"},
        }
        for asset in payload.assets:
            if asset.is_placeholder:
                problems.append(f"{asset.asset_id}はPlaceholderです。")
            path = Path(asset.file_path)
            allowed_suffixes = suffixes.get(asset.mime_type)
            if allowed_suffixes and path.suffix.lower() not in allowed_suffixes:
                problems.append(f"{asset.asset_id}の拡張子とMIME typeが一致しません。")
            probe = probe_media(path, asset.mime_type)
            details[asset.asset_id] = {
                "probe": probe.probe,
                **probe.metadata,
            }
            problems.extend(f"{asset.asset_id}: {message}" for message in probe.errors)
            metadata_missing.extend(f"{asset.asset_id}: {message}" for message in probe.warnings)
            for key in ("width", "height", "duration_seconds", "codec"):
                declared = asset.metadata.get(key)
                measured = probe.metadata.get(key)
                if declared is not None and measured is not None and declared != measured:
                    problems.append(f"{asset.asset_id}: 承認Metadata {key}と実測値が一致しません。")
        return [
            PreflightCheck(
                name="assets",
                result=CheckResult.FAIL if problems else CheckResult.PASS,
                message=" / ".join(problems) if problems else "Asset実体を確認しました。",
                details=details,
            ),
            PreflightCheck(
                name="media_technical_metadata",
                result=(
                    CheckResult.WARNING
                    if metadata_missing and payload.dry_run
                    else (
                        CheckResult.MANUAL_REVIEW_REQUIRED if metadata_missing else CheckResult.PASS
                    )
                ),
                message=(
                    "Dry Runでは技術Metadata未取得: " + " / ".join(metadata_missing)
                    if metadata_missing and payload.dry_run
                    else (
                        "本番前にMedia技術Metadataを確認してください: "
                        + " / ".join(metadata_missing)
                        if metadata_missing
                        else "Media解像度・尺・Codec Metadataを確認しました。"
                    )
                ),
            ),
        ]

    @staticmethod
    def _url_checks(payload: PublishPayload) -> list[PreflightCheck]:
        invalid = [
            url
            for url in payload.links
            if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc
        ]
        return [
            PreflightCheck(
                name="urls",
                result=CheckResult.FAIL if invalid else CheckResult.PASS,
                message=("Invalid URL: " + ", ".join(invalid))
                if invalid
                else "URL形式は有効です。",
            )
        ]

    @staticmethod
    def _platform_checks(
        payload: PublishPayload, account_metadata: dict[str, object]
    ) -> list[PreflightCheck]:
        metadata = payload.platform_metadata
        platform = payload.platform
        checks: list[PreflightCheck] = []
        if platform is PublishingPlatform.INSTAGRAM and not payload.assets:
            checks.append(_fail("instagram_media", "Instagram公開にはMediaが必要です。"))
        elif platform is PublishingPlatform.TIKTOK:
            if len(payload.assets) != 1 or payload.assets[0].mime_type != "video/mp4":
                checks.append(_fail("tiktok_media", "TikTok公開にはMP4動画が1件必要です。"))
            privacy = str(metadata.get("privacy_level", ""))
            if not privacy:
                checks.append(
                    _fail("tiktok_privacy", "Privacy levelをユーザーが選択してください。")
                )
            audited = bool(account_metadata.get("client_audited", False))
            if not audited and privacy not in {"", "SELF_ONLY"}:
                checks.append(
                    _fail(
                        "tiktok_client_audit",
                        "PUBLIC_POST_UNAVAILABLE: 未監査ClientではPublicへ変更しません。",
                    )
                )
            privacy_options = account_metadata.get("privacy_level_options")
            if isinstance(privacy_options, list) and privacy not in {
                str(value) for value in privacy_options
            }:
                checks.append(
                    _fail(
                        "tiktok_privacy_option",
                        "選択した公開範囲は最新Creator Infoで許可されていません。",
                    )
                )
            elif not privacy_options and not payload.dry_run:
                checks.append(
                    _manual(
                        "tiktok_privacy_options",
                        "Creator Infoの公開範囲候補を取得できていません。",
                    )
                )
            checked_at = str(account_metadata.get("creator_info_checked_at") or "")
            if not checked_at and not payload.dry_run:
                checks.append(
                    _manual("tiktok_creator_info", "最新Creator Info未確認のため公開できません。")
                )
            elif checked_at and not payload.dry_run:
                try:
                    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
                    is_fresh = checked.astimezone(UTC) >= datetime.now(UTC) - timedelta(minutes=15)
                except ValueError:
                    is_fresh = False
                if not is_fresh:
                    checks.append(
                        _manual(
                            "tiktok_creator_info_freshness",
                            "Creator Infoが15分以上前のため再同期が必要です。",
                        )
                    )
            for metadata_key, creator_key, label in (
                ("disable_comment", "comment_disabled", "コメント"),
                ("disable_duet", "duet_disabled", "デュエット"),
                ("disable_stitch", "stitch_disabled", "リミックス"),
            ):
                if account_metadata.get(creator_key) and not metadata.get(metadata_key):
                    checks.append(
                        _fail(
                            f"tiktok_{metadata_key}",
                            f"Creator設定により{label}を有効化できません。",
                        )
                    )
            try:
                max_duration = int(
                    str(account_metadata.get("max_video_post_duration_sec", 0) or 0)
                )
            except ValueError:
                max_duration = 0
                checks.append(
                    _manual(
                        "tiktok_creator_duration",
                        "Creator Infoの動画尺上限を再取得してください。",
                    )
                )
            declared_duration = (
                float(payload.assets[0].metadata.get("duration_seconds", 0) or 0)
                if payload.assets
                else 0
            )
            if max_duration and declared_duration > max_duration:
                checks.append(
                    _fail(
                        "tiktok_duration",
                        f"TikTok動画尺が対象Creatorの上限{max_duration}秒を超えています。",
                    )
                )
            if metadata.get("commercial_content") and not (
                metadata.get("brand_content_toggle") or metadata.get("brand_organic_toggle")
            ):
                checks.append(
                    _fail(
                        "tiktok_commercial_disclosure",
                        "商用コンテンツの開示種別を選択してください。",
                    )
                )
            if not payload.dry_run and not metadata.get("tiktok_consent_confirmed"):
                checks.append(
                    _fail(
                        "tiktok_explicit_consent",
                        "TikTokへこの内容を投稿する明示同意が承認版にありません。",
                    )
                )
        elif platform is PublishingPlatform.YOUTUBE:
            if not any(asset.mime_type.startswith("video/") for asset in payload.assets):
                checks.append(_fail("youtube_video", "YouTube公開にはVideo Assetが必要です。"))
            if not payload.title:
                checks.append(_fail("youtube_title", "YouTube titleが必要です。"))
            privacy = str(metadata.get("privacy", ""))
            if privacy not in {"private", "unlisted", "public"}:
                checks.append(_fail("youtube_privacy", "公開範囲を明示してください。"))
            if privacy == "public" and not account_metadata.get("api_project_audited", False):
                checks.append(
                    _fail(
                        "youtube_project_audit",
                        "未監査API ProjectではPublic uploadを利用できません。",
                    )
                )
        elif platform is PublishingPlatform.PINTEREST:
            if not metadata.get("board_id"):
                checks.append(_fail("pinterest_board", "Pinterest boardを指定してください。"))
            if not payload.assets:
                checks.append(_fail("pinterest_media", "PinにはImageまたはVideoが必要です。"))
        elif platform is PublishingPlatform.REDDIT:
            if not metadata.get("subreddit"):
                checks.append(_fail("subreddit", "Subredditを明示してください。"))
            if not metadata.get("subreddit_rules_verified"):
                checks.append(
                    _manual(
                        "subreddit_rules",
                        "Subreddit rules未確認のため自動投稿を停止します。",
                    )
                )
            if not metadata.get("community_relevance_confirmed"):
                checks.append(
                    _manual("community_relevance", "Community relevanceの人手確認が必要です。")
                )
        elif platform is PublishingPlatform.GENERIC:
            checks.append(_fail("official_api", "Generic Publisherの公式APIが未接続です。"))
        return checks

    def _duplicate_check(
        self,
        session: Session,
        job: PublicationJob,
        content_hash: str,
    ) -> PreflightCheck:
        existing = get_publication_by_key(session, job.idempotency_key)
        if existing and existing.status not in {
            PublishingStatus.FAILED,
            PublishingStatus.CANCELLED,
        }:
            return _fail("idempotency", "同一Idempotency Keyは既に公開済みです。")
        threshold = datetime.now(UTC) - timedelta(
            days=self._settings.publishing_duplicate_window_days
        )
        for publication in list_publications(session):
            if (
                publication.platform is job.platform
                and publication.target_account_id == job.target_account_id
                and publication.published_at
                and publication.published_at >= threshold
            ):
                previous = get_snapshot(session, publication.snapshot_id)
                if previous and previous.content_hash == content_hash:
                    return _fail(
                        "duplicate_content",
                        "同一Accountへ同一Content Hashが最近公開されています。",
                    )
        return _pass("duplicate_content", "最近の同一Content公開はありません。")


def _pass(name: str, message: str) -> PreflightCheck:
    return PreflightCheck(name=name, result=CheckResult.PASS, message=message)


def _fail(name: str, message: str) -> PreflightCheck:
    return PreflightCheck(name=name, result=CheckResult.FAIL, message=message)


def _manual(name: str, message: str) -> PreflightCheck:
    return PreflightCheck(
        name=name,
        result=CheckResult.MANUAL_REVIEW_REQUIRED,
        message=message,
    )


def _result(job: PublicationJob, checks: list[PreflightCheck]) -> PreflightResult:
    results = {check.result for check in checks}
    if CheckResult.FAIL in results or CheckResult.MANUAL_REVIEW_REQUIRED in results:
        status = PreflightStatus.BLOCKED
    elif CheckResult.WARNING in results:
        status = PreflightStatus.WARNING
    else:
        status = PreflightStatus.PASS
    return PreflightResult(
        job_id=job.job_id,
        platform=job.platform,
        status=status,
        checks=checks,
    )
