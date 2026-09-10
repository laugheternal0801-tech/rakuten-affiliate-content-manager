from __future__ import annotations

from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.creative_production.repositories import save_package
from app.creative_production.schemas import (
    AssetKind,
    AssetRecord,
    BrandVisualProfile,
    BrandVoiceProfile,
    CampaignPlan,
    ClaimBinding,
    ContentBrief,
    ContentCandidate,
    ContentPackage,
    CreativePlatform,
    CreativeQualityLevel,
    CreativeScore,
)
from app.publishing.approval import PublishingApprovalService
from app.publishing.hashing import publishing_platform
from app.publishing.repositories import save_account
from app.publishing.schemas import (
    ApprovedContentSnapshot,
    CapabilityAvailability,
    PublishingPlatform,
    SocialAccountConnection,
)

SCOPES = {
    PublishingPlatform.X: ["tweet.read", "tweet.write", "users.read"],
    PublishingPlatform.INSTAGRAM: [
        "instagram_business_basic",
        "instagram_business_content_publish",
    ],
    PublishingPlatform.TIKTOK: ["video.publish"],
    PublishingPlatform.YOUTUBE: ["https://www.googleapis.com/auth/youtube.upload"],
    PublishingPlatform.PINTEREST: ["boards:read", "pins:read", "pins:write"],
    PublishingPlatform.REDDIT: ["identity", "read", "submit"],
    PublishingPlatform.GENERIC: [],
}

PLATFORM_METADATA: dict[CreativePlatform, dict[str, object]] = {
    CreativePlatform.X: {},
    CreativePlatform.INSTAGRAM_FEED: {"media_type": "IMAGE"},
    CreativePlatform.INSTAGRAM_CAROUSEL: {"media_type": "CAROUSEL"},
    CreativePlatform.INSTAGRAM_REEL: {"media_type": "REELS"},
    CreativePlatform.TIKTOK: {
        "privacy_level": "SELF_ONLY",
        "disable_comment": False,
        "disable_duet": False,
        "disable_stitch": False,
    },
    CreativePlatform.YOUTUBE_LONG: {
        "privacy": "private",
        "category_id": "22",
        "made_for_kids": False,
    },
    CreativePlatform.YOUTUBE_SHORTS: {
        "privacy": "private",
        "category_id": "22",
        "made_for_kids": False,
    },
    CreativePlatform.PINTEREST: {
        "board_id": "123456",
        "alt_text": "Evidence-based creative",
    },
}


class PublishingFactory:
    def __init__(self, session: Session, tmp_path: Path) -> None:
        self.session = session
        self.tmp_path = tmp_path

    def account(
        self,
        platform: PublishingPlatform,
        *,
        suffix: str = "primary",
        scopes: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SocialAccountConnection:
        default_metadata: dict[str, object] = {}
        if platform is PublishingPlatform.TIKTOK:
            default_metadata = {
                "client_audited": False,
                "creator_info_checked_at": "2026-08-25T00:00:00Z",
            }
        if platform is PublishingPlatform.YOUTUBE:
            default_metadata = {"api_project_audited": False}
        account = SocialAccountConnection(
            platform=platform,
            account_id=f"external-{platform.value}-{suffix}",
            display_name=f"{platform.value} {suffix}",
            status=CapabilityAvailability.DRY_RUN,
            scopes=scopes if scopes is not None else SCOPES[platform],
            credential_reference=f"secret://{platform.value}/{suffix}",
            metadata={**default_metadata, **(metadata or {})},
        )
        save_account(self.session, account)
        return account

    def package(
        self,
        platforms: list[CreativePlatform],
        *,
        content: str = "確認済みEvidenceから、判断基準を整理します。",
        with_assets: bool = False,
    ) -> tuple[ContentPackage, dict[CreativePlatform, list[str]]]:
        campaign = CampaignPlan(
            research_run_id="MI-publishing-test",
            name="Publishing safety test",
            objective="安全な公開",
            target="検討層",
            pain_point="判断基準が不明確",
            core_insight="比較時の迷いが確認されている",
            primary_message="確認できた根拠だけを伝える",
            angles=["判断基準"],
            hooks=["何を基準に選びますか？"],
            cta="詳細を確認する",
            platform_priority=platforms,
            evidence_ids=["EV-PUB-1"],
        )
        voice = BrandVoiceProfile()
        visual = BrandVisualProfile()
        claim = ClaimBinding(
            claim_id="CL-PUB-1",
            claim="比較時の迷いが確認されています。",
            evidence_ids=["EV-PUB-1"],
        )
        brief = ContentBrief(
            research_run_id="MI-publishing-test",
            strategy_id="STR-publishing-test",
            campaign_id=campaign.campaign_id,
            market="AI副業",
            target_audience="初心者",
            target_problem="判断基準が不明確",
            consumer_insight="比較時に迷う",
            objective="安全な公開",
            primary_message="確認できた根拠だけを伝える",
            platforms=platforms,
            content_types=[item.value for item in platforms],
            cta="詳細を確認する",
            allowed_claims=[claim],
            evidence_ids=["EV-PUB-1"],
            brand_voice_profile_id=voice.profile_id,
            brand_visual_profile_id=visual.profile_id,
            creative_direction="Evidence first",
            visual_direction="clean",
            tone=["誠実"],
            quality_level=CreativeQualityLevel.STANDARD,
        )
        candidates: list[ContentCandidate] = []
        final_content: dict[CreativePlatform, ContentCandidate] = {}
        scores: list[CreativeScore] = []
        assets: list[AssetRecord] = []
        asset_mapping: dict[CreativePlatform, list[str]] = {}
        for index, platform in enumerate(platforms):
            structured = self._structured(platform, content)
            candidate = ContentCandidate(
                content_brief_id=brief.content_brief_id,
                platform=platform,
                provider="local-editorial",
                model="deterministic-editorial-v1",
                variant=f"variant-{index}",
                content=content,
                structured_content=structured,
                claims_used=[claim.claim],
                evidence_ids=claim.evidence_ids,
            )
            candidates.append(candidate)
            final_content[platform] = candidate
            scores.append(
                CreativeScore(
                    candidate_id=candidate.candidate_id,
                    judge_name="Objective Judge",
                    rubric={"evidence": 90, "clarity": 88},
                    overall=89,
                )
            )
            if with_assets and platform is not CreativePlatform.X:
                asset = self._asset(campaign.campaign_id, brief.content_brief_id, platform)
                assets.append(asset)
                asset_mapping[platform] = [asset.asset_id]
        package = ContentPackage(
            campaign=campaign,
            content_brief=brief,
            final_content=final_content,
            candidates=candidates,
            assets=assets,
            scores=scores,
            brand_voice_profile=voice,
            brand_visual_profile=visual,
            evidence_ids=["EV-PUB-1"],
            claims=[claim],
        )
        save_package(self.session, package)
        return package, asset_mapping

    def approve(
        self,
        package: ContentPackage,
        platforms: list[CreativePlatform],
        accounts: dict[CreativePlatform, SocialAccountConnection],
        asset_mapping: dict[CreativePlatform, list[str]] | None = None,
        metadata: dict[CreativePlatform, dict[str, object]] | None = None,
    ) -> list[ApprovedContentSnapshot]:
        target_accounts = {
            platform: account.connection_id for platform, account in accounts.items()
        }
        merged_metadata = {
            platform: {
                **PLATFORM_METADATA.get(platform, {}),
                **(metadata or {}).get(platform, {}),
            }
            for platform in platforms
        }
        _, snapshots = PublishingApprovalService().approve_and_lock(
            self.session,
            package.package_id,
            platforms,
            "test-reviewer",
            target_accounts,
            platform_assets=asset_mapping or {},
            platform_metadata=merged_metadata,
        )
        return snapshots

    def account_for_creative(
        self, platform: CreativePlatform, *, suffix: str = "primary"
    ) -> SocialAccountConnection:
        mapped = publishing_platform(platform)
        if mapped is None:
            raise ValueError("Not publishable")
        return self.account(mapped, suffix=suffix)

    def _asset(self, campaign_id: str, brief_id: str, platform: CreativePlatform) -> AssetRecord:
        video = platform in {
            CreativePlatform.TIKTOK,
            CreativePlatform.YOUTUBE_LONG,
            CreativePlatform.YOUTUBE_SHORTS,
            CreativePlatform.INSTAGRAM_REEL,
        }
        extension = ".mp4" if video else ".jpg"
        mime = "video/mp4" if video else "image/jpeg"
        path = self.tmp_path / f"{platform.value}-{len(list(self.tmp_path.iterdir()))}{extension}"
        if video:
            path.write_bytes(b"test-video-bytes")
        else:
            Image.new("RGB", (1200, 1200), color=(240, 240, 240)).save(path, "JPEG")
        return AssetRecord(
            asset_id=f"AST-{platform.value}-{path.stem}",
            campaign_id=campaign_id,
            content_brief_id=brief_id,
            kind=AssetKind.VIDEO if video else AssetKind.GENERATED_IMAGE,
            provider="test-asset",
            model="test-model",
            file_path=str(path),
            mime_type=mime,
        )

    @staticmethod
    def _structured(platform: CreativePlatform, content: str) -> dict[str, object]:
        if platform.value.startswith("youtube"):
            return {
                "title": "Evidenceで選ぶ判断基準",
                "description": content,
                "hashtags": ["#AI", "#学び"],
            }
        if platform is CreativePlatform.PINTEREST:
            return {
                "title": "比較で迷わないための判断基準",
                "description": content,
            }
        return {"caption": content, "hashtags": ["#AI"]}
