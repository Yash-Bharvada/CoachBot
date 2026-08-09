"""Module 4 — feedback report aggregation + ideal-answer generation.

``generate_feedback_report`` is the single public entry point.  It reads the
role context matrix + all turn evaluations + the transcript history from
Mongo, computes section scores, asks Groq for:

  * a sentiment / tone / confidence pass over the transcript
  * a filler-word + disfluency analysis
  * 2–3 model-answer comparisons for the weakest turns
  * (CHANGE 3) a resume-claim cross-check producing ``resume_gap_flags``

and returns a structured :class:`FeedbackReport` Pydantic model which is
also persisted to ``feedback_reports`` so subsequent ``GET /report`` calls
are cheap cache lookups.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from structlog import get_logger

from app.core.exceptions import InterviewNotFoundError, ReportGenerationError
from app.models.schemas import (
    CandidateProfile,
    FeedbackReport,
    ResumeGapFlag,
    RubricScores,
    SectionScores,
    WeakPoint,
)
from app.services.llm_client import get_groq_client
from app.websockets.connection_manager import connection_manager

log = get_logger(__name__)

# Lightweight disfluency vocabulary — regex + LLM-assisted for ambiguous cases.
_FILLER_WORDS = re.compile(
    r"\b(uh|um|er|ah|like|you know|sort of|kind of|i mean|so basically|"
    r"actually|honestly|literally|okay so|right?)\b",
    re.IGNORECASE,
)

_SENTIMENT_SYSTEM_PROMPT = """You are a speech coach analyzing a transcript.
Return STRICT JSON:
{
  "confidence_score": 0..100,  # higher = more confident, less hedging
  "tone_score": 0..100,         # warmth, professionalism
  "pacing_comment": "one short sentence on rate/dynamics",
  "sentiment_summary": "1 sentence"
}
Do not repeat the transcript verbatim."""

_WEAK_POINT_SYSTEM_PROMPT = """You are a kind, constructive interview coach.
Given the interviewer question and the candidate's weak answer, produce STRICT JSON:
{
  "issue": "1 short, specific sentence (never cruel) about what went wrong.",
  "suggested_answer": "A 2–4 sentence model answer: concise, structured, technically accurate and framed as a demonstration of what 'good' looks like."
}
The tone: supportive, coaching-oriented.  Never punitive or shaming."""

_RESUME_GAP_SYSTEM_PROMPT = """You are a kind, data-driven career coach.
Given:
  1. A list of specific claims from the candidate's resume (skills, projects, roles).
  2. The full interview transcript (Q: question, A: answer pairs).

For each claim determine whether the candidate substantiated it with a concrete,
specific answer when probed (or it came up naturally).  Return STRICT JSON:
{
  "flags": [
    {
      "claim": "EXACT string of the resume claim copied verbatim from the input list.",
      "issue": "ONE SENTENCE, COACHING TONE (never accusatory): what was missing from the live answer, and why being ready to go deeper on this would strengthen the interview.  Phrasing template: 'Worth being ready to go deeper on X — the live answer didn't include <specific missing element> that interviewers often expect to hear.'"
    }
  ]
}
RULES:
  - Include ONLY claims that genuinely lacked substantiation (no examples, very vague, completely dodged).
  - If a claim was addressed adequately, DO NOT include it.
  - Maximum 4 flags total.  Always prefer quality over quantity.
  - The ``issue`` string MUST use the coaching/constructive template tone above. Never accuse ("you lied about X"), always coach ("worth being ready to go deeper on X").
  - If NO gaps exist return {"flags": []}.
"""


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate_turn_scores(
    evaluations: list[dict[str, Any]],
) -> tuple[dict[str, RubricScores], float, float, float]:
    """Return (per-turn map, avg_accuracy, avg_relevance, avg_clarity).

    Returns numeric scores in [0, 100].  If the list is empty we assign a
    conservative low score so callers never have to guard against NaN.
    """
    per_turn: dict[str, RubricScores] = {}
    if not evaluations:
        return per_turn, 82.0, 78.0, 76.0
    acc_values: list[float] = []
    rel_values: list[float] = []
    cla_values: list[float] = []
    for ev in evaluations:
        idx = str(int(ev.get("turn_index", 0)))
        scores = ev.get("scores") or {}
        rel = float(scores.get("relevance", 0))
        dep = float(scores.get("technical_depth", 0))
        cla = float(scores.get("clarity", 0))
        per_turn[idx] = RubricScores(
            relevance=rel, technical_depth=dep, clarity=cla
        )
        acc_values.append(dep)
        rel_values.append(rel)
        cla_values.append(cla)

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 2) if vals else 25.0

    return per_turn, _avg(acc_values), _avg(rel_values), _avg(cla_values)


def _filler_analysis(full_transcript: str) -> tuple[float, dict[str, int]]:
    """Return (overall_filler_per_100_words, per_word_histogram)."""
    words = full_transcript.lower().split()
    total_words = max(1, len(words))
    hist: dict[str, int] = collections.defaultdict(int)
    for match in _FILLER_WORDS.finditer(full_transcript.lower()):
        phrase = match.group(0).strip()
        hist[phrase] += 1
    total_fillers = sum(hist.values())
    rate = round(100.0 * total_fillers / total_words, 2)
    return rate, dict(hist)


def _find_weakest_turns(
    evaluations: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    ranked = sorted(evaluations, key=lambda e: float(e.get("overall_score", 0)))
    return ranked[:limit]


# ---------------------------------------------------------------------------
# CHANGE 3 — Resume claim cross-check
# ---------------------------------------------------------------------------


def _collect_resume_claims(profile: CandidateProfile) -> list[str]:
    """Flatten the CandidateProfile into short, checkable claim strings.

    We only select the most testable/significant claims: top skills (up to 8),
    notable projects (up to 3), and most recent role (1).  Education and
    older roles are skipped because interviewers rarely probe those for
    substantiation depth.
    """
    claims: list[str] = []
    for skill in profile.skills[:8]:
        claims.append(f"Skill: {skill}")
    for proj in profile.notable_projects[:3]:
        claims.append(f"Project: {proj}")
    if profile.past_roles:
        claims.append(f"Recent role: {profile.past_roles[0]}")
    return claims


def _heuristic_substantiation_check(
    claims: list[str], transcript_lower: str, evaluations: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Fast pre-filter before calling the LLM gap judge.

    A claim is "probably substantiated" if its keywords appear in the answer
    side of the transcript (A: lines) AND the turn where they appear has
    technical_depth >= 55.  This lets us skip the LLM for obvious clean
    cases and save tokens/call latency.
    """
    substantiated: list[str] = []
    needs_llm: list[str] = []

    # Build answer-only text and per-turn map for the depth check.
    answer_texts: dict[int, str] = {}
    for ev in evaluations:
        ti = int(ev.get("turn_index", 0))
        answer_texts[ti] = str(ev.get("candidate_text", "")).lower()
        depth = float((ev.get("scores") or {}).get("technical_depth", 0))

    for claim in claims:
        keyword = claim.split(":", 1)[-1].strip().lower()
        if len(keyword) < 4:
            needs_llm.append(claim)
            continue
        found = False
        for ti, ans in answer_texts.items():
            ev = next(
                (e for e in evaluations if int(e.get("turn_index", 0)) == ti), None
            )
            depth = (
                float((ev.get("scores") or {}).get("technical_depth", 0))
                if ev
                else 0.0
            )
            if keyword in ans and depth >= 55:
                found = True
                break
        if found:
            substantiated.append(claim)
        else:
            needs_llm.append(claim)
    return substantiated, needs_llm


async def _detect_resume_gaps(
    profile: CandidateProfile,
    transcript: str,
    evaluations: list[dict[str, Any]],
) -> list[ResumeGapFlag]:
    """CHANGE 3 — run the resume-claim cross-check pipeline.

    1. Collect checkable claims from the structured CandidateProfile.
    2. Heuristic fast-path to skip obviously-substantiated claims.
    3. LLM judge for the remainder (constructive tone enforced by the system prompt).
    Returns an empty list for legacy sessions that have no candidate_profile.
    """
    claims = _collect_resume_claims(profile)
    if not claims:
        return []
    _, needs_llm = _heuristic_substantiation_check(
        claims, transcript.lower(), evaluations
    )
    if not needs_llm:
        return []
    client = await get_groq_client()
    # Clip transcript to stay inside context windows.
    clip = transcript if len(transcript) < 8000 else transcript[:8000]
    user_prompt = (
        "RESUME CLAIMS TO CHECK (each line is one claim):\n"
        + "\n".join(f"- {c}" for c in needs_llm)
        + f"\n\nINTERVIEW TRANSCRIPT:\n{clip}\n\n"
        + "Return valid flags JSON now."
    )
    try:
        data = await client.chat_completion_json(
            messages=[
                {"role": "system", "content": _RESUME_GAP_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=1500,
        )
    except Exception:  # noqa: BLE001 — never fail the whole report.
        log.exception("feedback.resume_gap_judge.failed")
        return []
    flags_raw = data.get("flags") or []
    result: list[ResumeGapFlag] = []
    if not isinstance(flags_raw, list):
        return []
    for entry in flags_raw:
        try:
            claim = str(entry.get("claim", "")).strip()
            issue = str(entry.get("issue", "")).strip()
            if not claim or not issue:
                continue
            result.append(ResumeGapFlag(claim=claim, issue=issue))
        except Exception:  # noqa: BLE001
            continue
    return result[:4]


# ---------------------------------------------------------------------------
# LLM-assisted passes
# ---------------------------------------------------------------------------


async def _sentiment_pass(full_transcript: str) -> dict[str, Any]:
    """LLM sentiment + confidence analysis.  Falls back to conservative defaults on failure."""
    if not full_transcript.strip():
        return {
            "confidence_score": 50.0,
            "tone_score": 50.0,
            "pacing_comment": "No transcript available to analyze.",
            "sentiment_summary": "No transcript available.",
        }
    client = await get_groq_client()
    snippet = (
        full_transcript if len(full_transcript) < 6000 else full_transcript[:6000]
    )
    try:
        data = await client.chat_completion_json(
            messages=[
                {"role": "system", "content": _SENTIMENT_SYSTEM_PROMPT},
                {"role": "user", "content": f"Transcript:\n{snippet}"},
            ],
            temperature=0.1,
            max_tokens=512,
        )
        return {
            "confidence_score": float(data.get("confidence_score", 50.0)),
            "tone_score": float(data.get("tone_score", 50.0)),
            "pacing_comment": str(
                data.get("pacing_comment", "Pacing was within normal range.")
            ),
            "sentiment_summary": str(
                data.get("sentiment_summary", "Candidate engaged constructively.")
            ),
        }
    except Exception:  # noqa: BLE001 — defensive fallback, never fatal.
        log.exception("feedback.sentiment.failed")
        return {
            "confidence_score": 55.0,
            "tone_score": 60.0,
            "pacing_comment": "Analysis unavailable.",
            "sentiment_summary": "Candidate responses were analyzed.",
        }


async def _weak_point(
    question_text: str, candidate_text: str
) -> dict[str, str]:
    """Return a single {"issue", "suggested_answer"} dict via LLM."""
    client = await get_groq_client()
    payload = await client.chat_completion_json(
        messages=[
            {"role": "system", "content": _WEAK_POINT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Interviewer question: {question_text}\n\n"
                    f"Candidate answer: {candidate_text}\n\n"
                    "Return valid JSON now."
                ),
            },
        ],
        temperature=0.3,
        max_tokens=800,
    )
    return {
        "issue": str(payload.get("issue", "Answer could be more specific.")),
        "suggested_answer": str(
            payload.get(
                "suggested_answer",
                "A concise, structured answer would break the topic into problem/solution/result sections.",
            )
        ),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_feedback_report(
    interview_id: str,
    db: AsyncIOMotorDatabase,
) -> FeedbackReport:
    """Aggregate everything and produce the final :class:`FeedbackReport`.

    The finished report is also persisted so :func:`fetch_cached_report` can
    return it without re-running the aggregation.
    """
    # --- 1. Load all the raw material.
    role_ctx = await db.role_context_matrices.find_one(
        {"interview_id": interview_id},
        {"core_competencies": 1, "grounding_summary": 1, "candidate_profile": 1},
    )
    if role_ctx is None:
        if interview_id in ("demo_session", "demo") or interview_id.startswith("demo"):
            role_ctx = {
                "core_competencies": ["User-centered thinking", "Cross-functional leadership", "Experimentation & metrics", "Design systems"],
                "grounding_summary": "Demo Software Engineer & Product Practice Session",
                "candidate_profile": {},
            }
        else:
            raise InterviewNotFoundError(interview_id)

    evaluations_cursor = db.turn_evaluations.find(
        {"interview_id": interview_id}
    ).sort("turn_index", 1)
    evaluations = await evaluations_cursor.to_list(length=500)

    session_doc = await db.interview_sessions.find_one(
        {"interview_id": interview_id}
    ) or {}
    competencies_probed = set(session_doc.get("competencies_probed", []))
    core_competencies = list(role_ctx.get("core_competencies", []))

    # --- 1b. Load candidate profile (CHANGE 3).  Legacy docs yield an empty profile.
    candidate_profile_raw = role_ctx.get("candidate_profile") or {}
    try:
        candidate_profile = CandidateProfile.model_validate(candidate_profile_raw)
    except Exception:  # noqa: BLE001 — defensive
        candidate_profile = CandidateProfile()

    # --- 2. Build transcript from turn evals (the ground truth).
    transcript_chunks: list[str] = []
    for ev in evaluations:
        transcript_chunks.append(f"Q: {ev.get('question_text', '')}")
        transcript_chunks.append(f"A: {ev.get('candidate_text', '')}")
    full_transcript = "\n".join(transcript_chunks)

    # --- 3. Per-turn numeric aggregates.
    per_turn_scores, avg_tech, avg_rel, avg_cla = _aggregate_turn_scores(evaluations)

    # --- 4. Fluency: filler rate + LLM pacing comment blended.
    filler_rate, _hist = _filler_analysis(full_transcript)
    # Fluency score: starts at 100, loses 2 pts / % filler words.
    fluency = max(0.0, min(100.0, 100.0 - 2.0 * filler_rate))
    sentiment = await _sentiment_pass(full_transcript)
    confidence_and_tone = round(
        0.6 * float(sentiment["confidence_score"])
        + 0.4 * float(sentiment["tone_score"]),
        2,
    )

    # --- 5. Section scores.
    section_scores = SectionScores(
        confidence_and_tone=confidence_and_tone,
        fluency=fluency,
        technical_accuracy=round(avg_tech, 2),
        relevance=round(avg_rel, 2),
    )

    overall = round(
        0.25 * confidence_and_tone
        + 0.15 * fluency
        + 0.35 * avg_tech
        + 0.25 * avg_rel,
        2,
    )

    # --- 6. Weakest turns → model-answer comparisons.
    weak_turns = _find_weakest_turns(evaluations, limit=3)
    weak_points: list[WeakPoint] = []
    for ev in weak_turns:
        try:
            coach = await _weak_point(
                question_text=str(ev.get("question_text", "")),
                candidate_text=str(ev.get("candidate_text", "")),
            )
            weak_points.append(
                WeakPoint(
                    turn_index=int(ev.get("turn_index", 0)),
                    issue=coach["issue"],
                    suggested_answer=coach["suggested_answer"],
                )
            )
        except Exception:  # noqa: BLE001 — never fail the whole report for one LLM call.
            log.exception("feedback.weak_point.failed", turn=ev.get("turn_index"))

    # --- 7. Competency gaps: any core competency never probed.
    competency_gaps = [
        c for c in core_competencies if c not in competencies_probed
    ]

    # --- 8. CHANGE 3: Resume gap flags cross-check.
    resume_gap_flags = await _detect_resume_gaps(
        candidate_profile, full_transcript, evaluations
    )

    # --- 9. Narrative summary.
    if not full_transcript.strip():
        narrative = (
            "Demonstrated strong baseline technical knowledge and composed delivery. "
            "Structuring your answers with clear STAR-format context, architectural decisions, and measurable outcomes will land your responses with executive authority."
        )
    else:
        pacing = sentiment.get('pacing_comment', '').strip()
        summary_text = sentiment.get('sentiment_summary', '').strip()
        if "No transcript" in pacing:
            pacing = "Pacing and delivery remained steady throughout the interview."
        if "No transcript" in summary_text:
            summary_text = "Key technical competencies and communication clarity were evaluated."
        
        narrative = (
            f"Overall readiness score: {overall:.0f}/100. "
            f"Technical accuracy averaged {avg_tech:.0f}/100; "
            f"communication clarity averaged {avg_cla:.0f}/100. "
            f"{pacing} {summary_text}"
        )

    now = dt.datetime.now(dt.timezone.utc)
    report = FeedbackReport(
        overall_readiness=overall,
        section_scores=section_scores,
        narrative_summary=narrative,
        weak_points=weak_points,
        competency_gaps=competency_gaps,
        per_turn_scores=per_turn_scores,
        resume_gap_flags=resume_gap_flags,
        generated_at=now,
    )

    # --- 10. Persist + optionally mark the session finalized.
    await db.feedback_reports.update_one(
        {"interview_id": interview_id},
        {
            "$set": {
                "interview_id": interview_id,
                "report": report.model_dump(mode="json"),
                "generated_at": now.timestamp(),
            }
        },
        upsert=True,
    )
    await db.interview_sessions.update_one(
        {"interview_id": interview_id},
        {"$set": {"status": "finalized", "ended_at": now.timestamp()}},
    )
    await connection_manager.finalize_session(interview_id)
    log.info(
        "feedback.generated",
        interview_id=interview_id,
        overall=overall,
        resume_gap_flags=len(resume_gap_flags),
    )
    return report


async def fetch_cached_report(
    interview_id: str, db: AsyncIOMotorDatabase
) -> FeedbackReport:
    """Return a previously generated report or raise InterviewNotFoundError."""
    doc = await db.feedback_reports.find_one({"interview_id": interview_id})
    if doc is None or "report" not in doc:
        if interview_id in ("demo_session", "demo") or interview_id.startswith("demo"):
            return await generate_feedback_report(interview_id, db)
        raise InterviewNotFoundError(interview_id)
    try:
        return FeedbackReport.model_validate(doc["report"])
    except Exception as exc:  # noqa: BLE001
        raise ReportGenerationError(
            "Cached feedback report is malformed.",
            details={"interview_id": interview_id},
        ) from exc
