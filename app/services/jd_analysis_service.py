"""Module 1 — Job Description parsing + live grounding merge.

The public surface is :func:`analyze_jd`, which:
  1. Validates the input JD is non-trivial (422 if it's just filler text).
  2. Calls Groq to extract core competencies, seniority / difficulty signals
     and an implicit tech stack as *structured JSON*.
  3. Attempts a live web grounding call; on timeout, degrades gracefully.
  4. Merges the two into a Role Context Matrix and persists it to Mongo.
  5. Returns the ``interview_id`` + summarized matrix to the caller.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from structlog import get_logger

from app.core.config import get_settings
from app.core.exceptions import GroundingTimeoutError, JDAnalysisError
from app.models.schemas import AnalyzeJDRequest, AnalyzeJDResponse
from app.services.llm_client import get_groq_client
from app.services.web_grounding_service import (
    GroundingResult,
    get_web_grounding_service,
)

log = get_logger(__name__)

# Common junk phrases we reject so the endpoint fails fast on placeholder JDs.
_GARBAGE_PATTERNS = (
    "lorem ipsum",
    "insert job description here",
    "sample job description",
)

_DIFFICULTY_INDEX = {"easy": 0, "medium": 1, "hard": 2}


def _validate_jd_text(text: str, job_title: str) -> None:
    """Raise JDAnalysisError if the JD looks like placeholder/gibberish text."""
    lowered = (text + " " + job_title).lower()
    for pattern in _GARBAGE_PATTERNS:
        if pattern in lowered:
            raise JDAnalysisError(
                "Job description appears to be placeholder text.",
                details={"matched_pattern": pattern},
            )
    # Require enough unique words to actually extract a signal from.
    unique_words = {w for w in re.sub(r"[^a-z0-9 ]", "", lowered).split() if len(w) > 2}
    if len(unique_words) < 15:
        raise JDAnalysisError(
            "Job description is too short or contains too little signal to parse.",
            details={"unique_words": len(unique_words)},
        )


import re  # noqa: E402 — kept close to the single caller above for readability.


def _build_interview_id() -> str:
    """Return an opaque, URL-safe interview identifier."""
    return f"intv_{uuid.uuid4().hex[:20]}"


# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_JD_PARSE_SYSTEM = """You are a senior technical recruiter parsing a job description.
Return STRICT JSON with these keys only (no extra prose):
{{
  "core_competencies": ["list of 6-10 specific skills, domains and behavioural competencies"],
  "difficulty_baseline": "easy" | "medium" | "hard" (based on seniority language: entry/0-2y=easy, mid/senior/2-6y=medium, staff/principal/7+y=hard),
  "tech_stack": ["explicit + implicit technologies mentioned"],
  "seniority_indicators": ["short list of phrases that drove the difficulty call"]
}}"""


async def _extract_with_llm(request: AnalyzeJDRequest) -> dict[str, Any]:
    """Invoke Groq LLM to return a structured parse of the JD."""
    client = await get_groq_client()
    user_prompt = (
        f"Job Title: {request.job_title}\n\n"
        f"Job Description:\n{request.job_description}\n\n"
        "Return valid JSON as instructed."
    )
    return await client.chat_completion_json(
        messages=[
            {"role": "system", "content": _JD_PARSE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=1024,
    )


# ---------------------------------------------------------------------------
# Grounding summary prompt
# ---------------------------------------------------------------------------

_GROUNDING_SUMMARY_SYSTEM = """You are an interview coach.  Based on the raw
grounding search hits below, write a single-paragraph summary that is SAFE
TO SHOW TO A CANDIDATE.  Summarize: typical interview format, areas of
technical depth often probed, and any known behavioural emphasis.  Do NOT
include salary numbers, specific URLs, or controversial claims.  Keep under
250 words."""


async def _summarize_grounding(
    request: AnalyzeJDRequest,
    parsed: dict[str, Any],
    grounding: GroundingResult | None,
) -> str:
    """Produce a short candidate-visible summary from grounding hits.

    Returns an empty-ish string if grounding is degraded, and uses the LLM
    only when we actually have hits to condense.
    """
    if grounding is None or not grounding.hits:
        return (
            f"Based on the JD for {request.job_title}, expect questions "
            f"across: {', '.join(parsed.get('core_competencies', [])[:5])}."
        )
    body = "\n".join(
        f"- [{h.title}] {h.snippet}" for h in grounding.hits
    )
    client = await get_groq_client()
    text = await client.chat_completion(
        messages=[
            {"role": "system", "content": _GROUNDING_SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Role: {request.job_title}\n"
                    f"Grounding hits:\n{body}\n\n"
                    "Write the single-paragraph summary now."
                ),
            },
        ],
        temperature=0.3,
        max_tokens=350,
    )
    assert isinstance(text, str)
    return text.strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def analyze_jd(
    request: AnalyzeJDRequest,
    db: AsyncIOMotorDatabase,
) -> AnalyzeJDResponse:
    """End-to-end JD analysis + grounding + persistence.

    This function deliberately catches :class:`GroundingTimeoutError` itself
    so that a slow search provider cannot break the user-visible workflow.
    Any other exception propagates unchanged and is translated by the
    global :class:`AppException` handler.
    """
    settings = get_settings()
    interview_id = _build_interview_id()
    log = get_logger().bind(interview_id=interview_id)

    _validate_jd_text(request.job_description, request.job_title)

    # 1. LLM parse — this is the non-negotiable core step.
    parsed = await _extract_with_llm(request)
    competencies = parsed.get("core_competencies") or []
    if not isinstance(competencies, list) or not competencies:
        raise JDAnalysisError(
            "The JD parser returned an empty competency list.",
            details={"llm_keys": list(parsed.keys())},
        )
    competencies = [str(c).strip() for c in competencies if str(c).strip()][:12]
    difficulty_baseline = parsed.get("difficulty_baseline")
    if difficulty_baseline not in {"easy", "medium", "hard"}:
        difficulty_baseline = "medium"

    # 2. Web grounding — explicitly wrapped so timeout degrades gracefully.
    grounding: GroundingResult | None = None
    grounding_status: str = "ok"
    grounding_raw: dict | None = None
    try:
        grounding = await get_web_grounding_service().research_role(
            request.job_title, request.company_name
        )
        grounding_raw = {
            "provider": grounding.provider,
            "query": grounding.query,
            "hits": [
                {"title": h.title, "url": h.url, "snippet": h.snippet}
                for h in grounding.hits
            ],
        }
    except GroundingTimeoutError as exc:
        log.warning("jd_analysis.grounding.degraded", detail=exc.message)
        grounding_status = "degraded"
        grounding_raw = {"error": exc.message, "details": exc.details}

    # 3. Candidate-visible summary.
    grounding_summary = await _summarize_grounding(request, parsed, grounding)

    # 4. Persist the full role context matrix.
    now = time.time()
    document = {
        "interview_id": interview_id,
        "job_title": request.job_title,
        "job_description": request.job_description,
        "company_name": request.company_name,
        "core_competencies": competencies,
        "difficulty_baseline": difficulty_baseline,
        "difficulty_index": _DIFFICULTY_INDEX[difficulty_baseline],
        "grounding_summary": grounding_summary,
        "grounding_status": grounding_status,
        "grounding_raw": grounding_raw,
        "tech_stack": parsed.get("tech_stack", []),
        "seniority_indicators": parsed.get("seniority_indicators", []),
        "created_at": now,
        "updated_at": now,
    }
    await db.role_context_matrices.insert_one(document)

    # 5. Seed an interview_sessions row so GET /{interview_id} works today.
    await db.interview_sessions.insert_one(
        {
            "interview_id": interview_id,
            "status": "in_progress",
            "difficulty_index": _DIFFICULTY_INDEX[difficulty_baseline],
            "competencies_probed": [],
            "competencies_pending": competencies,
            "started_at": now,
            "last_seen_at": now,
            "turn_count": 0,
        }
    )

    return AnalyzeJDResponse(
        interview_id=interview_id,
        core_competencies=competencies,
        difficulty_baseline=difficulty_baseline,  # type: ignore[arg-type]
        grounding_summary=grounding_summary,
        grounding_status=grounding_status,  # type: ignore[arg-type]
    )
