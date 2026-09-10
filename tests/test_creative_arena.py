from __future__ import annotations

from app.creative_production.arena import CreativeJury, GenerationArena, run_text_arena
from app.creative_production.planning import PlatformRouter
from app.creative_production.schemas import CreativePlatform
from app.creative_production.text_providers import (
    LocalEditorialProvider,
    TextProviderRegistry,
)
from tests.test_creative_planning import make_brief


def test_initial_candidates_are_independent_and_do_not_see_parent_drafts() -> None:
    brief = make_brief()
    task = PlatformRouter().route(brief)[0]
    registry = TextProviderRegistry([LocalEditorialProvider()])

    candidates, degraded, _ = GenerationArena(registry).generate(brief, task, candidate_count=3)

    assert len({candidate.candidate_id for candidate in candidates}) == 3
    assert all(candidate.parent_candidate_id is None for candidate in candidates)
    assert {candidate.variant for candidate in candidates} == {
        "information",
        "opinion",
        "discussion",
    }
    assert degraded is False


def test_creative_jury_uses_multiple_independent_rubrics() -> None:
    brief = make_brief()
    task = PlatformRouter().route(brief)[0]
    candidate = LocalEditorialProvider().generate(brief, task, variant="information")

    scores, critics, winner = CreativeJury().evaluate([candidate], brief, task)

    assert {score.judge_name for score in scores} == {
        "Objective QA",
        "Platform Judge",
        "Editorial Judge",
    }
    assert critics[0].candidate_id == candidate.candidate_id
    assert winner.candidate_id == candidate.candidate_id


def test_revision_loop_preserves_parent_lineage() -> None:
    brief = make_brief().model_copy(update={"platforms": [CreativePlatform.X]})
    task = PlatformRouter().route(brief)[0]
    registry = TextProviderRegistry([LocalEditorialProvider()])

    result = run_text_arena(
        registry,
        brief,
        task,
        candidate_count=2,
        max_revision_rounds=1,
    )

    assert result.winner in result.candidates
    revised = [candidate for candidate in result.candidates if candidate.revision_round > 0]
    if revised:
        assert revised[0].parent_candidate_id is not None
    assert result.scores
    assert result.critic_reports
