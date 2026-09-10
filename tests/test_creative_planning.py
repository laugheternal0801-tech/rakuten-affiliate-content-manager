from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.creative_production.api_contracts import CREATIVE_API_ROUTES
from app.creative_production.planning import BrandVoiceGuard, ClaimGuard, PlatformRouter
from app.creative_production.schemas import (
    BrandVisualProfile,
    BrandVoiceProfile,
    ClaimBinding,
    ClaimUseStatus,
    ContentBrief,
    ContentCandidate,
    CreativePlatform,
    CreativeQualityLevel,
)


def make_brief() -> ContentBrief:
    binding = ClaimBinding(
        claim_id="CL-1",
        claim="初心者の比較時の迷いが標本で確認されています。",
        evidence_ids=["EV-1"],
    )
    return ContentBrief(
        research_run_id="MI-1",
        strategy_id="STR-1",
        campaign_id="CAM-1",
        market="coffee",
        target_audience="初心者",
        target_problem="比較条件が分からない",
        consumer_insight=binding.claim,
        objective="教育",
        primary_message="判断基準を整理する",
        platforms=[CreativePlatform.X, CreativePlatform.INSTAGRAM_CAROUSEL],
        content_types=["x", "instagram_carousel"],
        cta="条件を書き出す",
        allowed_claims=[binding],
        evidence_ids=["EV-1"],
        brand_voice_profile_id="BVP-1",
        brand_visual_profile_id="BVIS-1",
        creative_direction="Evidence first",
        visual_direction="editorial",
        tone=["誠実"],
        quality_level=CreativeQualityLevel.STANDARD,
    )


def test_content_brief_requires_all_allowed_claim_evidence() -> None:
    brief = make_brief().model_dump()
    brief["evidence_ids"] = []
    with pytest.raises(ValidationError):
        ContentBrief.model_validate(brief)


def test_brand_visual_rejects_invalid_color() -> None:
    with pytest.raises(ValidationError):
        BrandVisualProfile(colors=["blue"])


def test_claim_guard_blocks_unapproved_numeric_assertion() -> None:
    brief = make_brief()
    candidate = ContentCandidate(
        content_brief_id=brief.content_brief_id,
        platform=CreativePlatform.X,
        provider="local",
        model="test",
        variant="information",
        content="男性の80%が購入に迷っています。",
        claims_used=["男性の80%が購入に迷っています。"],
    )

    checks = ClaimGuard(brief.allowed_claims).check(candidate)

    assert any(check.status is ClaimUseStatus.UNSUPPORTED for check in checks)
    assert ClaimGuard.blocks(checks)


def test_platform_router_creates_distinct_platform_requirements() -> None:
    tasks = PlatformRouter().route(make_brief())

    assert [task.platform for task in tasks] == [
        CreativePlatform.X,
        CreativePlatform.INSTAGRAM_CAROUSEL,
    ]
    assert tasks[0].requirements != tasks[1].requirements
    assert "slides" in tasks[1].output_schema


def test_brand_voice_guard_detects_forbidden_phrase() -> None:
    profile = BrandVoiceProfile(forbidden_phrases=["絶対"])

    assert BrandVoiceGuard().check("これは絶対に成功します。", profile)


def test_creative_api_contract_declares_required_routes() -> None:
    assert "POST /campaigns" in CREATIVE_API_ROUTES
    assert "POST /content/{id}/approve" in CREATIVE_API_ROUTES
    assert "GET /creative/capabilities" in CREATIVE_API_ROUTES
