"""Module 3 unit tests — deterministic difficulty state machine + rubric-based scoring loop.

Covers the agentic-eval requirements:
  * Evaluator-optimizer pattern: scores are structured JSON, never free text.
  * Rubric weights: technical_depth > relevance > clarity.
  * Difficulty state machine:
      - Two consecutive STRONG answers → escalate one level (cap: hard)
      - One WEAK answer → step down one level (floor: easy)
      - MID answers → hold
      - Never jump more than one level per turn.
  * End-to-end evaluate_turn call with 3 sample answers of varying quality
    and asserts the overall_score moves in the expected order.
"""

from __future__ import annotations

import pytest

from app.services.evaluation_service import (
    _overall_score,
    apply_difficulty_state_machine,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Difficulty state machine — pure unit tests, no I/O needed.
# ---------------------------------------------------------------------------


class TestDifficultyStateMachine:
    def test_two_strong_escalates_medium_to_hard(self) -> None:
        idx = apply_difficulty_state_machine(1, 80.0, 78.0)
        assert idx == 2

    def test_two_strong_already_hard_stays_hard(self) -> None:
        idx = apply_difficulty_state_machine(2, 92.0, 80.0)
        assert idx == 2

    def test_single_weak_steps_down_hard_to_medium(self) -> None:
        idx = apply_difficulty_state_machine(2, 30.0, None)
        assert idx == 1

    def test_single_weak_steps_down_medium_to_easy(self) -> None:
        idx = apply_difficulty_state_machine(1, 40.0, None)
        assert idx == 0

    def test_weak_on_easy_stays_easy(self) -> None:
        idx = apply_difficulty_state_machine(0, 10.0, None)
        assert idx == 0

    def test_mid_answer_holds(self) -> None:
        idx = apply_difficulty_state_machine(1, 60.0, 65.0)
        assert idx == 1

    def test_strong_but_prev_was_weak_only_holds(self) -> None:
        """One strong answer is NOT enough to escalate — must be two in a row."""
        idx = apply_difficulty_state_machine(1, 82.0, 20.0)
        assert idx == 1

    def test_never_jumps_two_levels(self) -> None:
        # Even with absurdly strong scores on medium, we only reach hard, not 3.
        idx = apply_difficulty_state_machine(1, 99.0, 99.0)
        assert idx == 2

    def test_out_of_range_index_clamps(self) -> None:
        idx = apply_difficulty_state_machine(-5, 50.0, None)
        assert idx == 0  # -5 → 1 → weak on 1 → 0


# ---------------------------------------------------------------------------
# Rubric scoring weights
# ---------------------------------------------------------------------------


class TestOverallScoreWeighting:
    def test_technical_depth_is_the_heaviest(self) -> None:
        high_tech = _overall_score(relevance=50, depth=90, clarity=50)
        high_rel = _overall_score(relevance=90, depth=50, clarity=50)
        high_cla = _overall_score(relevance=50, depth=50, clarity=90)
        assert high_tech > high_rel
        assert high_rel > high_cla  # relevance (0.35) > clarity (0.20)

    def test_perfect_score_is_100(self) -> None:
        assert _overall_score(100, 100, 100) == pytest.approx(100.0)

    def test_zero_score_is_zero(self) -> None:
        assert _overall_score(0, 0, 0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3 sample answer scoring loop — end-to-end evaluate_turn integration test
# using the mocked LLM judge (which always returns 85/80/75 when no
# customization is applied).  We vary the scores via monkeypatch to force
# low/medium/high runs and verify the ordering holds.
# ---------------------------------------------------------------------------


async def test_three_sample_answers_score_ordering(
    seeded_interview: str,
    session_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    app_with_mocks: Any,
) -> None:
    """Feed three answers of varying quality and assert their overall_scores
    come back in strictly ascending order."""
    import json as _json

    from app.services import evaluation_service as ev_mod
    from app.websockets.connection_manager import connection_manager

    # Create a session object so evaluate_turn can mutate state.
    role_ctx = await session_db.role_context_matrices.find_one(
        {"interview_id": seeded_interview}
    )
    comps = role_ctx.get("core_competencies", [])
    session = await connection_manager.create_session(
        seeded_interview,
        competencies_pending=comps,
        difficulty_baseline=1,
    )

    # Stub the judge LLM to return three known score tuples in order.
    canned_scores = [
        # Weak / low
        {"relevance": 30, "technical_depth": 25, "clarity": 40},
        # Medium / adequate
        {"relevance": 65, "technical_depth": 60, "clarity": 70},
        # Strong
        {"relevance": 92, "technical_depth": 88, "clarity": 85},
    ]
    call_counter = {"n": 0}

    from app.services import llm_client as llm_mod

    async def _judge_json(messages, *, temperature=0.1, max_tokens=1500):
        i = call_counter["n"]
        s = canned_scores[min(i, len(canned_scores) - 1)]
        call_counter["n"] += 1
        return {
            "scores": s,
            "covered_competencies": comps[:2],
            "short_rationale": "Mock judge.",
        }

    monkeypatch.setattr(llm_mod.GroqClient, "chat_completion_json", staticmethod(_judge_json))

    sample_answers = [
        "Uh, I don't really remember. We did something with threads I think.",  # weak
        "I used asyncio.gather to run the two tasks concurrently and aggregated results with a semaphore to bound DB concurrency.",  # medium
        (
            "I decomposed the flow into three stages: a producer coroutine that fans "
            "work onto a bounded asyncio.Queue, a pool of N worker coroutines that "
            "pull tasks and issue batched writes to Postgres, and a consumer that "
            "drains results.  The design respects backpressure because the queue "
            "size caps memory and we set connect_timeout on every DB call so a slow "
            "replica can't freeze the pipeline."
        ),
    ]
    overall_scores: list[float] = []
    difficulties_after: list[int] = []
    for turn_idx, answer in enumerate(sample_answers, start=1):
        scores, overall, before, after = await ev_mod.evaluate_turn(
            interview_id=seeded_interview,
            turn_index=turn_idx,
            question_text="How would you design an async pipeline?",
            candidate_text=answer,
            competencies=comps,
            session=session,
            db=session_db,
        )
        overall_scores.append(overall)
        difficulties_after.append(after)
    # Strictly ascending overall scores.
    assert overall_scores[0] < overall_scores[1] < overall_scores[2]
    # Assert the judge output was structured: each scores object matches the rubric.
    persisted = await session_db.turn_evaluations.find(
        {"interview_id": seeded_interview}
    ).to_list(length=10)
    assert len(persisted) == 3
    for doc in persisted:
        assert set(doc["scores"].keys()) == {"relevance", "technical_depth", "clarity"}
        for v in doc["scores"].values():
            assert 0 <= float(v) <= 100
