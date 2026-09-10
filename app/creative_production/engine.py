from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import Settings
from app.creative_production.arena import run_text_arena
from app.creative_production.image_system import ImageArena, ImageCandidate
from app.creative_production.planning import (
    BrandVoiceGuard,
    CampaignPlanner,
    ClaimGuard,
    ContentBriefBuilder,
    PlatformRouter,
)
from app.creative_production.prompt_loader import load_creative_prompt
from app.creative_production.provider_factory import (
    create_image_registry,
    create_text_registry,
    create_video_registry,
)
from app.creative_production.repositories import (
    create_campaign,
    save_assets,
    save_brief,
    save_candidates,
    save_costs,
    save_evaluations,
    save_package,
    update_job,
)
from app.creative_production.schemas import (
    AssetKind,
    AssetRecord,
    BrandVisualProfile,
    BrandVoiceProfile,
    ContentCandidate,
    ContentPackage,
    CostEntry,
    CreativePlatform,
    CreativeProductionRequest,
    CreativeQualityLevel,
    CreativeScore,
    CreativeStatus,
    ModelRun,
)
from app.creative_production.video_system import ShotBasedVideoArena
from app.market_intelligence.repositories import get_report
from app.publishing.hashing import publishing_platform
from app.publishing.performance import campaign_learning_signals

CreativeProgressHandler = Callable[[CreativeStatus, str, float], None]

TEXT_CANDIDATE_SETTINGS = {
    CreativeQualityLevel.DRAFT: "creative_candidates_draft",
    CreativeQualityLevel.STANDARD: "creative_candidates_standard",
    CreativeQualityLevel.PREMIUM: "creative_candidates_premium",
    CreativeQualityLevel.FLAGSHIP: "creative_candidates_flagship",
}
DEFAULT_REVISION_ROUNDS = {
    CreativeQualityLevel.DRAFT: 0,
    CreativeQualityLevel.STANDARD: 1,
    CreativeQualityLevel.PREMIUM: 2,
    CreativeQualityLevel.FLAGSHIP: 2,
}
VIDEO_PLATFORMS = {
    CreativePlatform.TIKTOK,
    CreativePlatform.INSTAGRAM_REEL,
    CreativePlatform.YOUTUBE_LONG,
    CreativePlatform.YOUTUBE_SHORTS,
    CreativePlatform.VIDEO,
}


def _prompt_name(platform: CreativePlatform) -> str:
    if platform is CreativePlatform.X:
        return "x_writer.md"
    if platform.value.startswith("instagram"):
        return "instagram_agent.md"
    if platform is CreativePlatform.TIKTOK:
        return "tiktok_agent.md"
    if platform.value.startswith("youtube"):
        return "youtube_agent.md"
    if platform is CreativePlatform.PINTEREST:
        return "pinterest_agent.md"
    return "article_writer.md"


class CreativeProductionEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @staticmethod
    def _emit(
        callback: CreativeProgressHandler | None,
        status: CreativeStatus,
        message: str,
        progress: float,
    ) -> None:
        if callback:
            callback(status, message, progress)

    def run(
        self,
        session: Session,
        request: CreativeProductionRequest,
        *,
        brand_voice: BrandVoiceProfile | None = None,
        brand_visual: BrandVisualProfile | None = None,
        on_progress: CreativeProgressHandler | None = None,
    ) -> ContentPackage:
        report = get_report(session, request.research_run_id)
        if report is None:
            raise LookupError("指定したResearch RunにMarket Intelligence Reportがありません。")
        voice = brand_voice or BrandVoiceProfile()
        visual = brand_visual or BrandVisualProfile()
        market = str(report.scope.get("market") or report.title)
        publishing_platforms = [
            mapped
            for platform in request.platforms
            if (mapped := publishing_platform(platform)) is not None
        ]
        performance_signals = campaign_learning_signals(session, market, publishing_platforms)
        campaign = CampaignPlanner().create(report, request, performance_signals)
        external_allowed = (
            request.allow_external_api and self._settings.creative_external_api_enabled
        )
        text_registry = create_text_registry(self._settings, allow_external=external_allowed)
        image_registry = create_image_registry(self._settings, allow_external=external_allowed)
        video_registry = create_video_registry(self._settings, allow_external=external_allowed)
        configuration_snapshot = {
            "text_capabilities": {
                key: value.model_dump(mode="json")
                for key, value in text_registry.capabilities().items()
            },
            "image_capabilities": {
                key: value.__dict__ for key, value in image_registry.capabilities().items()
            },
            "video_capabilities": {
                key: value.__dict__ for key, value in video_registry.capabilities().items()
            },
            "external_api_requested": request.allow_external_api,
            "external_api_enabled": external_allowed,
            "budget_limit": request.budget_limit,
        }
        create_campaign(session, campaign, request, configuration_snapshot)
        self._emit(on_progress, CreativeStatus.BRIEFING, "Content Briefを作成中", 0.10)
        brief = ContentBriefBuilder().create(report, request, campaign, voice, visual)
        save_brief(session, brief)
        tasks = PlatformRouter().route(brief)

        candidate_count = int(
            getattr(self._settings, TEXT_CANDIDATE_SETTINGS[request.quality_level])
        )
        default_revisions = min(
            self._settings.creative_max_revision_rounds,
            DEFAULT_REVISION_ROUNDS[request.quality_level],
        )
        revision_rounds = (
            request.max_revision_rounds
            if request.max_revision_rounds is not None
            else default_revisions
        )
        all_candidates: list[ContentCandidate] = []
        final_content: dict[CreativePlatform, ContentCandidate] = {}
        all_scores: list[CreativeScore] = []
        all_critics = []
        degradation_reasons: list[str] = []

        text_tasks = [
            task
            for task in tasks
            if task.platform not in {CreativePlatform.IMAGE, CreativePlatform.VIDEO}
        ]
        update_job(
            session,
            campaign.campaign_id,
            CreativeStatus.GENERATING,
            "independent_text_generation",
            0.20,
            details={"platforms": [task.platform.value for task in text_tasks]},
        )
        for index, task in enumerate(text_tasks, start=1):
            self._emit(
                on_progress,
                CreativeStatus.GENERATING,
                f"{task.platform.value}: 独立案を生成中",
                0.20 + 0.25 * index / max(1, len(text_tasks)),
            )
            result = run_text_arena(
                text_registry,
                brief,
                task,
                candidate_count=candidate_count,
                max_revision_rounds=revision_rounds,
                max_workers=self._settings.market_intelligence_max_concurrency,
            )
            all_candidates.extend(result.candidates)
            all_scores.extend(result.scores)
            all_critics.extend(result.critic_reports)
            final_content[task.platform] = result.winner
            degradation_reasons.extend(result.degradation_reasons)
            actual_voice_violations = BrandVoiceGuard().check(result.winner.content, voice)
            if actual_voice_violations:
                degradation_reasons.extend(actual_voice_violations)
            if ClaimGuard.blocks(ClaimGuard(brief.allowed_claims).check(result.winner)):
                degradation_reasons.append(
                    f"{task.platform.value}: Claim Guard blocking issueが残っています。"
                )

        update_job(
            session,
            campaign.campaign_id,
            CreativeStatus.COMPOSITING,
            "image_and_video_production",
            0.55,
        )
        asset_root = (self._settings.effective_creative_asset_dir / campaign.campaign_id).resolve()
        images: list[ImageCandidate] = []
        assets: list[AssetRecord] = []
        visual_platforms = [
            platform for platform in request.platforms if platform not in {CreativePlatform.VIDEO}
        ]
        if request.include_images:
            image_count = {
                CreativeQualityLevel.DRAFT: 1,
                CreativeQualityLevel.STANDARD: 2,
                CreativeQualityLevel.PREMIUM: 4,
                CreativeQualityLevel.FLAGSHIP: 6,
            }[request.quality_level]
            for platform in visual_platforms:
                image_result = ImageArena(image_registry).run(
                    brief,
                    platform,
                    visual,
                    asset_root / platform.value / "images",
                    candidate_count=image_count,
                )
                images.extend(image_result.candidates)
                all_scores.extend(image_result.scores)
                degradation_reasons.extend(image_result.degradation_reasons)
                assets.extend(
                    AssetRecord(
                        asset_id=image.asset_id,
                        campaign_id=campaign.campaign_id,
                        content_brief_id=brief.content_brief_id,
                        kind=(
                            AssetKind.COMPOSITE_IMAGE
                            if image.parent_asset_id
                            else AssetKind.GENERATED_IMAGE
                        ),
                        provider=image.provider,
                        model=image.model,
                        file_path=image.file_path,
                        mime_type=image.mime_type,
                        prompt=image.prompt,
                        seed=image.seed,
                        parameters=image.parameters,
                        parent_asset_id=image.parent_asset_id,
                        revision_number=image.revision_number,
                        score=next(
                            (
                                score.overall
                                for score in image_result.scores
                                if score.candidate_id == image.asset_id
                            ),
                            None,
                        ),
                        is_placeholder=image.is_placeholder,
                    )
                    for image in image_result.candidates
                )

        storyboards = []
        if request.include_video_storyboards:
            for platform in request.platforms:
                if platform not in VIDEO_PLATFORMS:
                    continue
                video_result = ShotBasedVideoArena(video_registry).run(
                    brief,
                    platform,
                    visual.colors,
                    campaign.campaign_id,
                    asset_root / platform.value / "video",
                )
                storyboards.append(video_result.storyboard)
                assets.extend(video_result.assets)
                all_scores.extend(video_result.scores)
                degradation_reasons.extend(video_result.degradation_reasons)

        self._emit(on_progress, CreativeStatus.QUALITY_CHECK, "Final QAを実行中", 0.88)
        update_job(
            session,
            campaign.campaign_id,
            CreativeStatus.QUALITY_CHECK,
            "final_qa",
            0.88,
        )
        model_runs = [
            ModelRun(
                campaign_id=campaign.campaign_id,
                content_id=candidate.candidate_id,
                provider=candidate.provider,
                model=candidate.model,
                task="independent_generation" if candidate.revision_round == 0 else "revision",
                platform=candidate.platform,
                creative_type=candidate.platform.value,
                prompt_version=load_creative_prompt(_prompt_name(candidate.platform)).version,
                input_tokens=candidate.input_tokens,
                output_tokens=candidate.output_tokens,
                estimated_cost=candidate.estimated_cost,
                latency_ms=candidate.duration_ms,
                judge_score=next(
                    (
                        score.overall
                        for score in all_scores
                        if score.candidate_id == candidate.candidate_id
                    ),
                    None,
                ),
            )
            for candidate in all_candidates
        ]
        costs = [
            CostEntry(
                campaign_id=campaign.campaign_id,
                content_id=candidate.candidate_id,
                provider=candidate.provider,
                model=candidate.model,
                operation="text_generation",
                quantity=1,
                estimated_cost=candidate.estimated_cost,
            )
            for candidate in all_candidates
        ]
        costs.extend(
            CostEntry(
                campaign_id=campaign.campaign_id,
                content_id=image.asset_id,
                provider=image.provider,
                model=image.model,
                operation="image_generation",
                quantity=1,
                estimated_cost=image.estimated_cost,
            )
            for image in images
        )
        degradation_reasons = list(dict.fromkeys(degradation_reasons))
        package = ContentPackage(
            campaign=campaign,
            content_brief=brief,
            final_content=final_content,
            candidates=all_candidates,
            images=images,
            storyboards=storyboards,
            assets=assets,
            model_runs=model_runs,
            critic_reports=all_critics,
            scores=all_scores,
            costs=costs,
            brand_voice_profile=voice,
            brand_visual_profile=visual,
            evidence_ids=brief.evidence_ids,
            claims=brief.allowed_claims,
            status=CreativeStatus.AWAITING_APPROVAL,
            degraded_quality_mode=bool(degradation_reasons),
            degradation_reasons=degradation_reasons,
            human_approval_required=True,
        )
        save_candidates(
            session,
            campaign.campaign_id,
            all_candidates,
            {candidate.candidate_id for candidate in final_content.values()},
        )
        save_evaluations(session, campaign.campaign_id, all_scores, all_critics)
        save_assets(session, assets)
        save_costs(session, costs)
        save_package(session, package)
        update_job(
            session,
            campaign.campaign_id,
            CreativeStatus.AWAITING_APPROVAL,
            "human_approval",
            1.0,
            details={"package_id": package.package_id},
            degradation_reasons=degradation_reasons,
        )
        self._emit(
            on_progress,
            CreativeStatus.AWAITING_APPROVAL,
            "Content Package生成完了。Human Approval待ちです。",
            1.0,
        )
        return package
