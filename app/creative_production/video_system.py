from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.creative_production.schemas import (
    AssetKind,
    AssetRecord,
    CaptionCue,
    ContentBrief,
    CreativePlatform,
    CreativeQualityLevel,
    CreativeScore,
    CreativeStatus,
    ProviderAvailability,
    Storyboard,
    VideoJob,
    VideoShot,
    VisualIdentityProfile,
)


@dataclass(frozen=True)
class VideoProviderCapability:
    provider: str
    status: ProviderAvailability
    model: str
    external: bool
    asynchronous: bool
    operations: tuple[str, ...]
    message: str = ""


class VideoGenerationProvider(ABC):
    key: str
    model: str
    external: bool

    @abstractmethod
    def health_check(self) -> VideoProviderCapability:
        """Return locally known provider capability."""

    @abstractmethod
    def text_to_video(
        self,
        shot: VideoShot,
        identity: VisualIdentityProfile,
        output_dir: Path,
    ) -> VideoJob:
        """Submit one shot as an asynchronous-compatible job."""

    def image_to_video(
        self,
        shot: VideoShot,
        image_path: str,
        identity: VisualIdentityProfile,
        output_dir: Path,
    ) -> VideoJob:
        return self.text_to_video(shot, identity, output_dir)

    def extend(self, job: VideoJob, duration: float) -> VideoJob:
        raise NotImplementedError("This provider does not support extend.")

    def edit(self, job: VideoJob, instructions: list[str]) -> VideoJob:
        raise NotImplementedError("This provider does not support edit.")

    def get_status(self, job: VideoJob) -> VideoJob:
        return job

    def download(self, job: VideoJob, output_dir: Path) -> str:
        if not job.asset_id:
            raise RuntimeError("Video jobにasset_idがありません。")
        return job.asset_id

    @staticmethod
    def estimate_cost(duration_seconds: float) -> float | None:
        return None


def _safe_output(output_dir: Path, filename: str) -> Path:
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = (root / Path(filename).name).resolve()
    if path.parent != root:
        raise ValueError("Video asset output pathが許可ディレクトリ外です。")
    return path


class LocalStoryboardVideoProvider(VideoGenerationProvider):
    """Produces shot manifests only; never labels them as generated video."""

    key = "local_storyboard"
    model = "deterministic-shot-manifest-v1"
    external = False

    def health_check(self) -> VideoProviderCapability:
        return VideoProviderCapability(
            provider=self.key,
            status=ProviderAvailability.AVAILABLE,
            model=self.model,
            external=False,
            asynchronous=True,
            operations=("shot_manifest", "get_status", "download_manifest"),
            message="Shot設計確認用。動画ファイルは生成しません。",
        )

    def text_to_video(
        self,
        shot: VideoShot,
        identity: VisualIdentityProfile,
        output_dir: Path,
    ) -> VideoJob:
        asset_id = f"AST-{shot.shot_id}"
        path = _safe_output(output_dir, f"{asset_id}-shot.json")
        payload = {
            "notice": "STORYBOARD PLACEHOLDER - NOT A GENERATED VIDEO",
            "shot": shot.model_dump(mode="json"),
            "visual_identity": identity.model_dump(mode="json"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return VideoJob(
            provider=self.key,
            model=self.model,
            status=CreativeStatus.PARTIAL,
            shot_id=shot.shot_id,
            asset_id=asset_id,
            progress=1.0,
            error="Shot manifestのみ。実動画Providerは未接続です。",
        )


class UnavailableVideoProvider(VideoGenerationProvider):
    external = True

    def __init__(self, key: str, model: str, message: str) -> None:
        self.key = key
        self.model = model
        self._message = message

    def health_check(self) -> VideoProviderCapability:
        return VideoProviderCapability(
            provider=self.key,
            status=ProviderAvailability.NOT_CONFIGURED,
            model=self.model or "—",
            external=True,
            asynchronous=True,
            operations=(
                "text_to_video",
                "image_to_video",
                "extend",
                "edit",
                "get_status",
                "download",
            ),
            message=self._message,
        )

    def text_to_video(
        self,
        shot: VideoShot,
        identity: VisualIdentityProfile,
        output_dir: Path,
    ) -> VideoJob:
        raise RuntimeError(self._message)


class VideoProviderRegistry:
    def __init__(self, providers: list[VideoGenerationProvider]) -> None:
        self._providers = {provider.key: provider for provider in providers}

    @property
    def available(self) -> list[VideoGenerationProvider]:
        return [
            provider
            for provider in self._providers.values()
            if provider.health_check().status is ProviderAvailability.AVAILABLE
        ]

    def capabilities(self) -> dict[str, VideoProviderCapability]:
        return {key: provider.health_check() for key, provider in self._providers.items()}


class StoryboardAgent:
    def create(self, brief: ContentBrief, platform: CreativePlatform) -> Storyboard:
        short = platform in {
            CreativePlatform.TIKTOK,
            CreativePlatform.INSTAGRAM_REEL,
            CreativePlatform.YOUTUBE_SHORTS,
            CreativePlatform.VIDEO,
        }
        durations = [2.0, 4.0, 9.0, 10.0, 5.0] if short else [5.0, 15.0, 25.0, 25.0, 20.0]
        purposes = ["Hook", "Problem", "Evidence", "Solution", "CTA"]
        narrations = [
            brief.target_problem,
            "比較条件が増えるほど、判断の軸が見えにくくなります。",
            brief.consumer_insight,
            "目的、比較条件、不明点の順に整理します。",
            brief.cta,
        ]
        shots = [
            VideoShot(
                scene=index,
                order=index,
                duration=duration,
                purpose=purpose,
                subject=brief.target_audience,
                action=("視線を止める一動作" if index == 1 else "判断ステップを視覚的に進める"),
                location="brand-neutral editorial environment",
                camera_angle="eye level" if index != 3 else "top-down evidence view",
                camera_motion="controlled push-in" if index == 1 else "slow lateral move",
                lens="35mm equivalent",
                lighting="soft key light with consistent direction",
                composition="subject on thirds with caption-safe lower area",
                generation_prompt=f"{purpose}: {narration}",
                negative_prompt="logos, watermark, illegible text, identity drift",
                narration=narration,
                audio_notes="Narration clear; music stays below voice",
                continuity_notes="Same wardrobe, environment, palette and key-light direction",
                transition="cut" if index < 5 else "end card",
            )
            for index, (duration, purpose, narration) in enumerate(
                zip(durations, purposes, narrations, strict=True), start=1
            )
        ]
        return Storyboard(
            content_brief_id=brief.content_brief_id,
            platform=platform,
            title=f"{brief.market}: {brief.target_problem}",
            aspect_ratio="9:16" if short else "16:9",
            total_duration=sum(durations),
            shots=shots,
            retention_notes=[
                "2秒以内に問題を提示",
                "価値提供を6秒以内に開始",
                "同じ構図を連続させない",
                "CTA直前に要点を再提示",
            ],
            is_placeholder=True,
        )


class CaptionSystem:
    def create(self, storyboard: Storyboard) -> list[CaptionCue]:
        cues: list[CaptionCue] = []
        cursor = 0.0
        for shot in storyboard.shots:
            cues.append(
                CaptionCue(
                    text=shot.narration[:80],
                    start=cursor,
                    end=cursor + shot.duration,
                    position="bottom",
                    emphasis=[shot.purpose],
                    max_lines=2,
                )
            )
            cursor += shot.duration
        return cues


class CinematographerAgent:
    def continuity_profile(
        self,
        brief: ContentBrief,
        colors: list[str],
    ) -> VisualIdentityProfile:
        return VisualIdentityProfile(
            subject_description=brief.target_audience,
            product_description="No unverified product depiction",
            wardrobe="consistent neutral editorial wardrobe",
            environment="consistent brand-neutral environment",
            colors=colors,
            forbidden_changes=[
                "identity change",
                "wardrobe change",
                "third-party logo appearance",
                "product shape invention",
            ],
        )


class VideoJudge:
    def score(
        self,
        storyboard: Storyboard,
        jobs: list[VideoJob],
    ) -> list[CreativeScore]:
        pacing = 92.0 if storyboard.shots[0].duration <= 2 else 55.0
        continuity = 90.0 if all(shot.continuity_notes for shot in storyboard.shots) else 50.0
        actual_video = any(job.status is CreativeStatus.APPROVED for job in jobs)
        execution = 90.0 if actual_video else 30.0
        rubrics = [
            (
                "Retention Judge",
                {"first_2_seconds": pacing, "pacing": 86.0, "value_timing": 90.0},
            ),
            (
                "Continuity Judge",
                {"identity": continuity, "camera": 84.0, "transitions": 82.0},
            ),
            (
                "Objective Video QA",
                {"shot_manifest": 100.0, "duration": 100.0, "rendered_video": execution},
            ),
        ]
        return [
            CreativeScore(
                candidate_id=storyboard.storyboard_id,
                judge_name=name,
                rubric=rubric,
                overall=round(sum(rubric.values()) / len(rubric), 2),
                blocking_issues=(
                    ["実動画ファイルは未生成です。"]
                    if name == "Objective Video QA" and not actual_video
                    else []
                ),
            )
            for name, rubric in rubrics
        ]


class FFmpegEditor:
    def health_check(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def compose(self, jobs: list[VideoJob], output_path: Path) -> str:
        if any(job.status is CreativeStatus.PARTIAL for job in jobs):
            raise RuntimeError("Placeholder Shotは実動画として結合できません。")
        if not self.health_check():
            raise RuntimeError("FFmpegが見つかりません。")
        raise NotImplementedError("実Provider接続後にShot downloadとFFmpeg結合を有効化します。")


@dataclass(frozen=True)
class VideoProductionResult:
    storyboard: Storyboard
    captions: list[CaptionCue]
    jobs: list[VideoJob]
    assets: list[AssetRecord]
    scores: list[CreativeScore]
    degraded: bool
    degradation_reasons: tuple[str, ...]


class ShotBasedVideoArena:
    def __init__(self, registry: VideoProviderRegistry) -> None:
        self._registry = registry

    def run(
        self,
        brief: ContentBrief,
        platform: CreativePlatform,
        colors: list[str],
        campaign_id: str,
        output_dir: Path,
    ) -> VideoProductionResult:
        storyboard = StoryboardAgent().create(brief, platform)
        identity = CinematographerAgent().continuity_profile(brief, colors)
        providers = self._registry.available
        if not providers:
            raise RuntimeError("利用可能なVideo Providerがありません。")
        jobs: list[VideoJob] = []
        reasons: list[str] = []
        for index, shot in enumerate(storyboard.shots):
            provider = providers[index % len(providers)]
            try:
                jobs.append(provider.text_to_video(shot, identity, output_dir))
            except Exception as exc:
                reasons.append(f"{provider.key}/{shot.shot_id}: {type(exc).__name__}")
        captions = CaptionSystem().create(storyboard)
        storyboard_path = _safe_output(
            output_dir, f"AST-{storyboard.storyboard_id}-storyboard.json"
        )
        storyboard_path.write_text(
            json.dumps(
                {
                    "notice": "STORYBOARD PLACEHOLDER - NOT A GENERATED VIDEO",
                    "storyboard": storyboard.model_dump(mode="json"),
                    "captions": [cue.model_dump(mode="json") for cue in captions],
                    "visual_identity": identity.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        assets = [
            AssetRecord(
                asset_id=f"AST-{storyboard.storyboard_id}",
                campaign_id=campaign_id,
                content_brief_id=brief.content_brief_id,
                kind=AssetKind.STORYBOARD,
                provider="storyboard_agent",
                model="deterministic-storyboard-v1",
                file_path=str(storyboard_path),
                mime_type="application/json",
                prompt="Shot-based storyboard",
                is_placeholder=True,
            )
        ]
        for job in jobs:
            if not job.asset_id or not job.shot_id:
                continue
            assets.append(
                AssetRecord(
                    asset_id=job.asset_id,
                    campaign_id=campaign_id,
                    content_brief_id=brief.content_brief_id,
                    kind=AssetKind.VIDEO_SHOT,
                    provider=job.provider,
                    model=job.model,
                    file_path=str(_safe_output(output_dir, f"{job.asset_id}-shot.json")),
                    mime_type="application/json",
                    prompt=next(
                        shot.generation_prompt
                        for shot in storyboard.shots
                        if shot.shot_id == job.shot_id
                    ),
                    parent_asset_id=f"AST-{storyboard.storyboard_id}",
                    is_placeholder=True,
                )
            )
        external = {job.provider for job in jobs if job.provider != "local_storyboard"}
        needs_multiple = brief.quality_level in {
            CreativeQualityLevel.PREMIUM,
            CreativeQualityLevel.FLAGSHIP,
        }
        degraded = not external or (needs_multiple and len(external) < 2)
        if not external:
            reasons.append("実Video Provider未接続のためShot manifestのみ生成しました。")
        elif needs_multiple and len(external) < 2:
            reasons.append("Premium/Flagshipの複数Video Provider要件を満たしていません。")
        return VideoProductionResult(
            storyboard=storyboard,
            captions=captions,
            jobs=jobs,
            assets=assets,
            scores=VideoJudge().score(storyboard, jobs),
            degraded=degraded,
            degradation_reasons=tuple(dict.fromkeys(reasons)),
        )
