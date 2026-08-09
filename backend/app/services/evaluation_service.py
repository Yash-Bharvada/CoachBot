"""Module 3 — adaptive per-turn evaluation engine.

Implements the *evaluator-optimizer* pattern from the agentic-eval skill:

  1. ``evaluate_turn`` scores a candidate answer using a fixed 3-axis rubric
     (relevance / technical_depth / clarity) via an LLM-as-judge call that
     returns strict JSON.
  2. ``apply_difficulty_state_machine`` deterministically updates the
     session's difficulty index.  The LLM never directly selects difficulty
     — it can only produce numeric scores, and a bounded state machine
     reacts to them.  We never jump more than one level per turn.
  3. ``next_question_plan`` feeds the updated difficulty + uncovered
     competencies back to Module 2's prompt assembly so the interviewer
     selects an appropriate next question.
  4. Per-turn records are persisted to ``turn_evaluations`` so Module 4 can
     aggregate them without hitting the LLM again.

Difficulty state machine rules (deterministic, LLM-never-decides):
  * Turn score ``>= 75`` is a STRONG answer; ``< 45`` is WEAK; else MID.
  * Two consecutive STRONG → escalate one level (capped at 2 = hard).
  * One WEAK → step down one level (floored at 0 = easy).
  * Otherwise hold.
"""

from __future__ import annotations

import time
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from structlog import get_logger

from app.core.exceptions import EvaluationError
from app.models.schemas import RubricScores
from app.services.llm_client import get_groq_client
from app.websockets.connection_manager import InterviewSession

log = get_logger(__name__)

_DIFFICULTY_LABELS = {0: "easy", 1: "medium", 2: "hard"}
_COMPETENCY_COVERAGE_THRESHOLD = 60  # rubric score needed to consider it "covered"

# ---------------------------------------------------------------------------
# LLM-as-judge prompt — rubric-based, structured-JSON output
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = """You are a senior hiring manager acting as an
independent judge.  Evaluate the candidate's answer against the question
and role competencies.  Return ONLY valid JSON with these keys and NO extra
prose:

{{
  "scores": {{
    "relevance": 0..100,
    "technical_depth": 0..100,
    "clarity": 0..100
  }},
  "covered_competencies": ["subset of competencies list that were demonstrated"],
  "short_rationale": "1 sentence, < 120 chars, e.g. 'Solid example of caching but missed CAP tradeoffs.'"
}}

Scoring guide (rubric — be strict):
  90–100: Exemplary, production-grade, clearly articulates trade-offs.
  75–89:  Strong, correct in the main, minor gaps only.
  55–74:  Adequate, recognises the topic but lacks depth.
  35–54:  Weak, partial or tangential answer.
  0–34:   Off-topic, factually wrong, or no signal.
"""


def _validate_scores(data: dict[str, Any]) -> tuple[float, float, float]:
    """Return (relevance, depth, clarity) clamped to 0..100 floats."""
    try:
        scores = data["scores"]
        rel = float(scores["relevance"])
        dep = float(scores["technical_depth"])
        cla = float(scores["clarity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationError(
            "Judge output is missing required numeric score fields.",
            details={"raw": data},
        ) from exc
    return (
        max(0.0, min(100.0, rel)),
        max(0.0, min(100.0, dep)),
        max(0.0, min(100.0, cla)),
    )


# ---------------------------------------------------------------------------
# Deterministic difficulty state machine
# ---------------------------------------------------------------------------


def _overall_score(relevance: float, depth: float, clarity: float) -> float:
    """Weighted aggregate used by the difficulty state machine.

    Technical depth carries the most weight because the primary purpose of
    adaptive difficulty is to match *technical* challenge to the candidate.
    """
    return 0.35 * relevance + 0.45 * depth + 0.20 * clarity


def apply_difficulty_state_machine(
    current_index: int,
    current_score: float,
    previous_score: float | None,
) -> int:
    """Return the new difficulty index (0/1/2) — never jumps more than one.

    Pure function (no DB, no LLM) so it is trivial to unit test and reason
    about.  The LLM is *never* allowed to set difficulty directly.
    """
    if current_index < 0 or current_index > 2:
        current_index = 1
    strong = current_score >= 75
    prev_strong = previous_score is not None and previous_score >= 75
    weak = current_score < 45

    new_index = current_index
    if strong and prev_strong:
        new_index = min(2, current_index + 1)
    elif weak:
        new_index = max(0, current_index - 1)
    # else hold
    return new_index


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_turn(
    *,
    interview_id: str,
    turn_index: int,
    question_text: str,
    candidate_text: str,
    competencies: list[str],
    session: InterviewSession,
    db: AsyncIOMotorDatabase,
) -> tuple[RubricScores, float, int, int]:
    """Score a single candidate turn, persist it, and update session state.

    Returns
    -------
    rubric_scores, overall_score, difficulty_before, difficulty_after
    """
    if not candidate_text.strip():
        raise EvaluationError(
            "Cannot evaluate an empty answer.",
            details={"interview_id": interview_id, "turn_index": turn_index},
        )

    client = await get_groq_client()
    log = get_logger().bind(interview_id=interview_id, turn_index=turn_index)

    judge_user_prompt = (
        f"Question: {question_text}\n\n"
        f"Role competencies to watch for: {competencies}\n\n"
        f"Candidate answer:\n{candidate_text}\n\n"
        "Return the JSON evaluation now."
    )
    try:
        judge_output = await client.chat_completion_json(
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": judge_user_prompt},
            ],
            temperature=0.0,
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001 — LLM failures are translated
        log.exception("evaluate_turn.llm_failed")
        raise EvaluationError(
            "Failed to score the candidate turn (LLM error).",
            details={"interview_id": interview_id, "turn_index": turn_index},
        ) from exc

    relevance, depth, clarity = _validate_scores(judge_output)
    overall = _overall_score(relevance, depth, clarity)
    scores_obj = RubricScores(
        relevance=relevance, technical_depth=depth, clarity=clarity
    )

    # Update competency coverage deterministically (not via the LLM dict — we
    # use the LLM's list only as a hint; the numeric score is the source of
    # truth for "covered" to avoid drift between runs).
    hinted = [str(c).lower() for c in judge_output.get("covered_competencies", [])]
    for comp in competencies:
        if (comp.lower() in hinted or overall >= 70) and overall >= _COMPETENCY_COVERAGE_THRESHOLD:
            session.competencies_probed.add(comp)
            session.competencies_pending.discard(comp)

    difficulty_before = session.difficulty_index
    # Feed the rolling history of previous scores to the state machine.
    previous_overall: float | None = None
    prev_idx = turn_index - 1
    if prev_idx in session.scores:
        prev = session.scores[prev_idx]
        previous_overall = 0.35 * prev.get("relevance", 0) + 0.45 * prev.get(
            "technical_depth", 0
        ) + 0.20 * prev.get("clarity", 0)
    difficulty_after = apply_difficulty_state_machine(
        difficulty_before, overall, previous_overall
    )
    session.difficulty_index = difficulty_after
    session.scores[turn_index] = scores_obj.model_dump()

    # Persist the turn evaluation record for Module 4 aggregation.
    ts = time.time()
    await db.turn_evaluations.insert_one(
        {
            "interview_id": interview_id,
            "turn_index": turn_index,
            "question_text": question_text,
            "candidate_text": candidate_text,
            "scores": scores_obj.model_dump(),
            "overall_score": round(overall, 2),
            "difficulty_before": difficulty_before,
            "difficulty_after": difficulty_after,
            "timestamp": ts,
        }
    )
    log.info(
        "evaluate_turn.done",
        overall=round(overall, 2),
        difficulty_before=_DIFFICULTY_LABELS[difficulty_before],
        difficulty_after=_DIFFICULTY_LABELS[difficulty_after],
    )
    return scores_obj, overall, difficulty_before, difficulty_after


def next_question_plan(session: InterviewSession) -> dict[str, Any]:
    """Return a structured hint used by Module 2 to craft the next question.

    Pure helper — no I/O, just packs the current difficulty + pending
    competencies into a JSON-serialisable dict the prompt template can
    consume.  Module 2 calls this *after* evaluate_turn updated state.
    """
    pending = sorted(session.competencies_pending) or sorted(
        session.competencies_probed
    )
    return {
        "difficulty": _DIFFICULTY_LABELS[session.difficulty_index],
        "pending_competencies": pending[:5],
        "probed_count": len(session.competencies_probed),
        "total_turns": sum(1 for t in session.turns if t.role == "candidate"),
    }
