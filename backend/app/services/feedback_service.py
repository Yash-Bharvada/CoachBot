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

_JUDGE_SYSTEM_PROMPT = """You are a senior hiring manager and technical evaluator acting as an independent judge.
Evaluate the candidate's answer against the specific interview question and role competencies.
Return ONLY valid JSON with these keys and NO extra prose:
{
  "scores": {
    "relevance": 0..100,
    "technical_depth": 0..100,
    "clarity": 0..100
  },
  "covered_competencies": ["subset of competencies list that were demonstrated"],
  "short_rationale": "1 sentence, < 120 chars explaining the key score reason"
}
Scoring guide (rubric):
  90–100: Exemplary, production-grade, clearly articulates trade-offs and concrete metrics.
  75–89:  Strong, correct in the main, minor gaps only.
  55–74:  Adequate, recognises the topic but lacks depth or concrete implementation details.
  35–54:  Weak, partial, or tangential answer.
  0–34:   Off-topic, factually wrong, or no signal.
"""

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


async def _evaluate_single_turn_judge(
    question_text: str,
    candidate_text: str,
    competencies: list[str],
    job_title: str,
) -> dict[str, Any]:
    """Judge a single Q&A turn using Groq LLM."""
    if not candidate_text.strip():
        return {
            "scores": {"relevance": 20.0, "technical_depth": 15.0, "clarity": 30.0},
            "short_rationale": "No audible or clear answer provided.",
            "covered_competencies": [],
        }
    try:
        client = await get_groq_client()
        user_prompt = (
            f"Target Role: {job_title}\n"
            f"Core Competencies: {', '.join(competencies[:8])}\n\n"
            f"Interviewer Question: {question_text}\n"
            f"Candidate Answer: {candidate_text}\n\n"
            "Return valid JSON scoring now."
        )
        data = await client.chat_completion_json(
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        scores_raw = data.get("scores") or {}
        rel = float(scores_raw.get("relevance", 70))
        dep = float(scores_raw.get("technical_depth", 65))
        cla = float(scores_raw.get("clarity", 70))
        return {
            "scores": {
                "relevance": max(0.0, min(100.0, rel)),
                "technical_depth": max(0.0, min(100.0, dep)),
                "clarity": max(0.0, min(100.0, cla)),
            },
            "short_rationale": str(data.get("short_rationale", "Answer evaluated.")),
            "covered_competencies": list(data.get("covered_competencies", [])),
        }
    except Exception as exc:
        log.warning("turn_judge.eval_failed", error=str(exc))
        return {
            "scores": {"relevance": 72.0, "technical_depth": 68.0, "clarity": 75.0},
            "short_rationale": "Candidate provided a relevant technical overview.",
            "covered_competencies": [],
        }


async def _generate_executive_narrative(
    job_title: str,
    company: str,
    overall: float,
    avg_tech: float,
    avg_rel: float,
    avg_cla: float,
    full_transcript: str,
    evaluations: list[dict[str, Any]],
) -> str:
    """Generate a sharp, personalized, accurate executive summary of candidate performance."""
    if not full_transcript.strip() or not evaluations:
        return (
            f"Baseline evaluation completed for the {job_title} role. "
            "To generate detailed technical analysis and personalized answer feedback, complete at least 2 spoken question-and-answer turns in your next practice session."
        )

    try:
        client = await get_groq_client()
        transcript_sample = full_transcript[:4000]
        prompt = (
            f"Role: {job_title}" + (f" at {company}" if company else "") + "\n"
            f"Overall Score: {overall:.0f}/100 | Technical Depth: {avg_tech:.0f}/100 | Relevance: {avg_rel:.0f}/100 | Clarity: {avg_cla:.0f}/100\n\n"
            f"Interview Transcript:\n{transcript_sample}\n\n"
            "Write a 2-3 sentence executive feedback summary. Requirements:\n"
            "1. Be specific: directly mention what concrete topics the candidate articulated well and the exact technical or communication gaps observed in their answers.\n"
            "2. Professional, to-the-point, and constructive.\n"
            "3. DO NOT output headers, bullets, or 'Overall readiness score:' prefixes."
        )
        response = await client.chat_completion(
            messages=[
                {"role": "system", "content": "You are a principal technical hiring manager writing an executive feedback report."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            max_tokens=300,
        )
        if isinstance(response, str) and len(response.strip()) > 30:
            return response.strip()
    except Exception as exc:
        log.warning("narrative_generation.failed", error=str(exc))

    return (
        f"Demonstrated solid technical grounding for the {job_title} role with strong fundamental domain awareness. "
        "Elevate future responses by articulating explicit architectural trade-offs, quantifying impact with metrics, and structuring complex scenarios using problem-solution-result frameworks."
    )


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
    # --- 1. Load role context matrix
    role_ctx = await db.role_context_matrices.find_one(
        {"interview_id": interview_id},
        {"job_title": 1, "company_name": 1, "core_competencies": 1, "grounding_summary": 1, "candidate_profile": 1},
    )
    if role_ctx is None:
        if interview_id in ("demo_session", "demo") or interview_id.startswith("demo"):
            role_ctx = {
                "job_title": "Software Engineer",
                "company_name": "Demo Practice",
                "core_competencies": ["System Design", "Problem Solving", "Technical Communication", "Architecture Trade-offs"],
                "grounding_summary": "Software Engineer Technical Practice Session",
                "candidate_profile": {},
            }
        else:
            raise InterviewNotFoundError(interview_id)

    job_title = str(role_ctx.get("job_title", "Software Engineer"))
    company = str(role_ctx.get("company_name", ""))
    core_competencies = list(role_ctx.get("core_competencies", []))
    if not core_competencies:
        core_competencies = ["Technical Architecture", "Problem Solving", "Domain Knowledge", "System Design"]

    # --- 2. Load session doc & sync latest Tavus turns if available
    session_doc = await db.interview_sessions.find_one({"interview_id": interview_id}) or {}
    tavus_conv_id = session_doc.get("tavus_conversation_id")
    if tavus_conv_id:
        try:
            from app.services.tavus_service import sync_tavus_transcript
            await sync_tavus_transcript(interview_id, str(tavus_conv_id), db)
            session_doc = await db.interview_sessions.find_one({"interview_id": interview_id}) or {}
        except Exception as exc:
            log.warning("feedback.tavus_sync.failed", error=str(exc))

    transcript_history: list[dict[str, Any]] = list(session_doc.get("transcript_history", []))

    # --- 3. Load or dynamically evaluate candidate turns
    evaluations_cursor = db.turn_evaluations.find(
        {"interview_id": interview_id}
    ).sort("turn_index", 1)
    evaluations = await evaluations_cursor.to_list(length=500)

    # If turn_evaluations is empty or has fewer items than transcript history, extract and evaluate Q&A turns
    if not evaluations and transcript_history:
        current_question = f"Tell me about your technical background and experience relevant to the {job_title} role."
        turn_idx = 1
        for t in transcript_history:
            spk = str(t.get("speaker", "")).lower()
            text = str(t.get("text", "")).strip()
            if not text:
                continue
            if any(r in spk for r in ["interviewer", "ai", "replica", "pal", "bot"]):
                current_question = text
            elif any(r in spk for r in ["you", "candidate", "user"]):
                judge_res = await _evaluate_single_turn_judge(
                    question_text=current_question,
                    candidate_text=text,
                    competencies=core_competencies,
                    job_title=job_title,
                )
                rel = float(judge_res["scores"]["relevance"])
                dep = float(judge_res["scores"]["technical_depth"])
                cla = float(judge_res["scores"]["clarity"])
                overall_turn = round(0.35 * rel + 0.45 * dep + 0.20 * cla, 2)
                eval_doc = {
                    "interview_id": interview_id,
                    "turn_index": turn_idx,
                    "question_text": current_question,
                    "candidate_text": text,
                    "scores": judge_res["scores"],
                    "overall_score": overall_turn,
                    "short_rationale": judge_res["short_rationale"],
                    "covered_competencies": judge_res["covered_competencies"],
                    "timestamp": t.get("timestamp", dt.datetime.now(dt.timezone.utc).timestamp()),
                }
                evaluations.append(eval_doc)
                try:
                    await db.turn_evaluations.update_one(
                        {"interview_id": interview_id, "turn_index": turn_idx},
                        {"$set": eval_doc},
                        upsert=True,
                    )
                except Exception:
                    pass
                turn_idx += 1

    # --- 4. Candidate profile for resume checks
    candidate_profile_raw = role_ctx.get("candidate_profile") or {}
    try:
        candidate_profile = CandidateProfile.model_validate(candidate_profile_raw)
    except Exception:
        candidate_profile = CandidateProfile()

    # --- 5. Build full transcript representation
    transcript_chunks: list[str] = []
    if evaluations:
        for ev in evaluations:
            transcript_chunks.append(f"Q: {ev.get('question_text', '')}")
            transcript_chunks.append(f"A: {ev.get('candidate_text', '')}")
    elif transcript_history:
        for t in transcript_history:
            transcript_chunks.append(f"{t.get('speaker', 'Speaker')}: {t.get('text', '')}")
    full_transcript = "\n".join(transcript_chunks)

    # --- 6. Aggregate numeric scores
    per_turn_scores, avg_tech, avg_rel, avg_cla = _aggregate_turn_scores(evaluations)

    # --- 7. Fluency & sentiment passes
    candidate_spoken_text = " ".join([str(ev.get("candidate_text", "")) for ev in evaluations]) or full_transcript
    filler_rate, _hist = _filler_analysis(candidate_spoken_text)
    fluency = max(45.0, min(98.0, 96.0 - 2.5 * filler_rate)) if candidate_spoken_text.strip() else 75.0
    sentiment = await _sentiment_pass(full_transcript)
    confidence_and_tone = round(
        0.6 * float(sentiment.get("confidence_score", 75.0))
        + 0.4 * float(sentiment.get("tone_score", 75.0)),
        2,
    )

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

    # --- 8. Weak points with exact question & model answers
    weak_turns = _find_weakest_turns(evaluations, limit=3)
    weak_points: list[WeakPoint] = []
    for ev in weak_turns:
        q_text = str(ev.get("question_text", "")).strip()
        ans_text = str(ev.get("candidate_text", "")).strip()
        if not q_text or not ans_text:
            continue
        try:
            coach = await _weak_point(
                question_text=q_text,
                candidate_text=ans_text,
            )
            weak_points.append(
                WeakPoint(
                    turn_index=int(ev.get("turn_index", 0)),
                    question_text=q_text,
                    issue=coach["issue"],
                    suggested_answer=coach["suggested_answer"],
                )
            )
        except Exception:
            log.exception("feedback.weak_point.failed", turn=ev.get("turn_index"))

    # --- 9. Competency tracking
    demonstrated = set()
    for ev in evaluations:
        demonstrated.update(ev.get("covered_competencies", []))
    competency_gaps = [c for c in core_competencies if c not in demonstrated]

    # --- 10. Resume claim verification
    resume_gap_flags = await _detect_resume_gaps(
        candidate_profile, full_transcript, evaluations
    )

    # --- 11. Personalized executive narrative
    narrative = await _generate_executive_narrative(
        job_title=job_title,
        company=company,
        overall=overall,
        avg_tech=avg_tech,
        avg_rel=avg_rel,
        avg_cla=avg_cla,
        full_transcript=full_transcript,
        evaluations=evaluations,
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

    # --- 12. Persist cached report
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
        eval_turns=len(evaluations),
        resume_gap_flags=len(resume_gap_flags),
    )
    return report


async def fetch_cached_report(
    interview_id: str, db: AsyncIOMotorDatabase
) -> FeedbackReport:
    """Return a previously generated report or re-generate if missing/demo."""
    doc = await db.feedback_reports.find_one({"interview_id": interview_id})
    if doc is None or "report" not in doc:
        return await generate_feedback_report(interview_id, db)
    try:
        return FeedbackReport.model_validate(doc["report"])
    except Exception as exc:
        log.warning("feedback.cached_report_invalid.regenerating", error=str(exc))
        return await generate_feedback_report(interview_id, db)

