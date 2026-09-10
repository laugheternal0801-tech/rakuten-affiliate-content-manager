from __future__ import annotations

from pathlib import Path

from app.creative_production.image_system import (
    ImageArena,
    ImageObjectiveQA,
    ImageProviderRegistry,
    LocalPatternImageProvider,
)
from app.creative_production.schemas import (
    BrandVisualProfile,
    CreativePlatform,
)
from app.creative_production.video_system import (
    LocalStoryboardVideoProvider,
    ShotBasedVideoArena,
    VideoProviderRegistry,
)
from tests.test_creative_planning import make_brief


def test_image_arena_separates_visual_generation_and_typography(tmp_path: Path) -> None:
    brief = make_brief()
    brand = BrandVisualProfile()
    result = ImageArena(ImageProviderRegistry([LocalPatternImageProvider()])).run(
        brief,
        CreativePlatform.X,
        brand,
        tmp_path,
        candidate_count=2,
    )

    assert result.final.parent_asset_id is not None
    assert result.final.provider == "deterministic_layout"
    assert result.final.is_placeholder is True
    assert Path(result.final.file_path).is_file()
    qa = ImageObjectiveQA().check(result.final, result.visual_brief)
    assert qa.passed is True
    assert len({item.duplicate_hash for item in result.qa}) == 2


def test_video_arena_creates_shots_captions_and_asset_lineage(tmp_path: Path) -> None:
    brief = make_brief().model_copy(update={"platforms": [CreativePlatform.TIKTOK]})
    result = ShotBasedVideoArena(VideoProviderRegistry([LocalStoryboardVideoProvider()])).run(
        brief,
        CreativePlatform.TIKTOK,
        ["#17324D", "#F3B61F"],
        "CAM-1",
        tmp_path,
    )

    assert len(result.storyboard.shots) == 5
    assert result.storyboard.total_duration == 30
    assert len(result.captions) == 5
    assert all(job.progress == 1 for job in result.jobs)
    assert all(asset.is_placeholder for asset in result.assets)
    assert all(Path(asset.file_path).is_file() for asset in result.assets)
    assert result.degraded is True
