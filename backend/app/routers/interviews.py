"""Module 4 router — session metadata, /finalize, /report, Tavus integration.

Business logic lives in :mod:`app.services.feedback_service` and
:mod:`app.services.tavus_service`; this module is the HTTP glue layer:
dependency injection, rate limiting, status codes, webhook signature verification.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.routing import APIRouter
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from structlog import get_logger

from app.core.config import get_settings
from app.core.database import get_database
from app.core.exceptions import InterviewNotFoundError, SessionStateError
from app.core.security import http_rate_limiter
from app.models.schemas import (
    DifficultyLevel,
    FeedbackReport,
    FinalizeResponse,
    InterviewSessionSummary,
    TavusConversationResponse,
)
from app.services.feedback_service import (
    fetch_cached_report,
    generate_feedback_report,
)
from app.services.tavus_service import create_tavus_conversation
from app.websockets.connection_manager import connection_manager

log = get_logger(__name__)

router = APIRouter(dependencies=[Depends(http_rate_limiter())])

_DIFFICULTY_LABELS: dict[int, DifficultyLevel] = {
    0: "easy",
    1: "medium",
    2: "hard",
}


def _index_to_label(idx: int | None) -> DifficultyLevel:
    if idx is None:
        return "medium"
    return _DIFFICULTY_LABELS.get(int(idx), "medium")


@router.get(
    "/{interview_id}",
    summary="Fetch summary metadata for an interview session",
    response_model=InterviewSessionSummary,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"description": "Interview id does not exist."},
    },
)
async def get_session_summary(
    interview_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> InterviewSessionSummary:
    """Return a lightweight view of the session's progress and difficulty."""
    session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
    if session_doc is None:
        raise InterviewNotFoundError(interview_id)
    in_mem = await connection_manager.get_session(interview_id)

    status_val = str(session_doc.get("status", "in_progress"))
    if in_mem is not None and status_val == "in_progress":
        status_val = in_mem.status  # type: ignore[assignment]

    difficulty_idx = int(session_doc.get("difficulty_index", 1))
    started_at = datetime.fromtimestamp(
        float(session_doc.get("started_at", 0)), tz=timezone.utc
    )
    ended_ts = session_doc.get("ended_at")
    ended_at = (
        datetime.fromtimestamp(float(ended_ts), tz=timezone.utc)
        if ended_ts is not None
        else None
    )

    return InterviewSessionSummary(
        interview_id=interview_id,
        status=status_val,  # type: ignore[arg-type]
        turn_count=int(session_doc.get("turn_count", 0)),
        difficulty_current=_index_to_label(difficulty_idx),
        competencies_probed=list(session_doc.get("competencies_probed", [])),
        competencies_pending=list(session_doc.get("competencies_pending", [])),
        started_at=started_at,
        ended_at=ended_at,
    )


# ---------------------------------------------------------------------------
# Tavus PAL: conversation + webhook (CHANGE 2)
# ---------------------------------------------------------------------------


class _TavusConversationRequest(BaseModel):
    """Request body for POST /{interview_id}/conversation.

    The ``callback_url`` is required (fully-qualified URL Tavus will POST
    events to) so that deployments can override based on their environment
    rather than hard-coding a single URL in Settings.
    """

    callback_url: str


@router.post(
    "/{interview_id}/conversation",
    summary="Create a Tavus PAL video conversation for this interview",
    description=(
        "Formats the Role & Candidate Context Matrix into a tight per-candidate "
        "string for Tavus's ``conversational_context`` field; if the matrix is "
        "too large to fit inline (~2500 chars) it uploads a tagged Document "
        "instead and passes ``document_tags`` so the PAL retrieves it via RAG. "
        "Never writes candidate/JD details into the shared PAL system_prompt."
    ),
    response_model=TavusConversationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Interview id does not exist."},
        400: {
            "description": (
                "Tavus not configured (TAVUS_API_KEY / persona_id missing) or "
                "the Tavus API returned a malformed response."
            ),
        },
    },
)
async def create_conversation(
    interview_id: str,
    body: _TavusConversationRequest,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> TavusConversationResponse:
    """Public entry point for Tavus conversation creation (CHANGE 2)."""
    if not body.callback_url or not body.callback_url.startswith(("http://", "https://")):
        raise SessionStateError(
            "callback_url must be a fully-qualified http(s) URL.",
            details={"callback_url": body.callback_url},
        )
    return await create_tavus_conversation(
        interview_id, db, callback_url=body.callback_url
    )


@router.post(
    "/tavus-webhook",
    summary="Receive Tavus conversation webhook events",
    description=(
        "Verifies the ``X-Tavus-Signature`` header against the shared "
        "TAVUS_WEBHOOK_SECRET before accepting any event.  Persists "
        "status updates and transcript segments back to Mongo; the actual "
        "per-turn scoring still happens in Module 3 once a turn is complete."
    ),
    status_code=status.HTTP_200_OK,
)
async def tavus_webhook(
    request: Request,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
    x_tavus_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Verified webhook receiver for Tavus status and live transcript/utterance events."""
    settings = get_settings()
    raw_body = await request.body()
    # --- Signature verification: HMAC-SHA256(secret, raw_body) == X-Tavus-Signature
    if settings.tavus_webhook_secret and x_tavus_signature:
        expected = hmac.new(
            settings.tavus_webhook_secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_tavus_signature.lower()):
            raise HTTPException(status_code=401, detail="Tavus signature mismatch.")
    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc
    interview_id = None
    metadata = payload.get("metadata") or {}
    if isinstance(metadata, dict):
        interview_id = metadata.get("interview_id")
    if not interview_id:
        conversation_id = payload.get("conversation_id")
        if conversation_id:
            doc = await db.interview_sessions.find_one(
                {"tavus_conversation_id": str(conversation_id)},
                {"interview_id": 1},
            )
            if doc:
                interview_id = doc.get("interview_id")
    event_name = str(payload.get("event_name") or payload.get("event") or "unknown")

    # Extract utterance/text if available
    utterance_text = (
        payload.get("text")
        or payload.get("message")
        or payload.get("utterance")
        or payload.get("content")
        or (payload.get("data", {}).get("text") if isinstance(payload.get("data"), dict) else None)
    )

    if interview_id and utterance_text and str(utterance_text).strip():
        raw_speaker = str(
            payload.get("speaker")
            or payload.get("role")
            or (payload.get("data", {}).get("speaker") if isinstance(payload.get("data"), dict) else "")
            or "interviewer"
        ).lower()
        speaker = (
            "Interviewer"
            if any(r in raw_speaker for r in ["interviewer", "pal", "persona", "bot", "assistant", "replica"])
            else "You"
        )
        turn = {
            "speaker": speaker,
            "text": str(utterance_text).strip(),
            "timestamp": time.time(),
        }
        await db.interview_sessions.update_one(
            {"interview_id": interview_id},
            {"$push": {"transcript_history": turn}, "$inc": {"turn_count": 1}},
            upsert=True,
        )

    log.info(
        "tavus.webhook.received",
        event_type=event_name,
        interview_id=interview_id,
        conversation_id=payload.get("conversation_id"),
        has_text=bool(utterance_text),
    )
    # Ack: Tavus retries if it sees a non-2xx.
    return {"status": "ok", "event": event_name}


# ---------------------------------------------------------------------------
# Finalize + report (Module 4)
# ---------------------------------------------------------------------------


@router.post(
    "/{interview_id}/finalize",
    summary="End the interview and generate a structured feedback report",
    description=(
        "Aggregates turn evaluations, runs a sentiment + filler-word analysis, "
        "cross-checks resume claims against the live transcript for "
        "resume_gap_flags, and generates model-answer comparisons for the "
        "weakest 2–3 turns.  The report is cached so a subsequent GET "
        "/report call is free."
    ),
    response_model=FinalizeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Interview id does not exist."},
        500: {"description": "Report generation failed (see error details)."},
    },
)
async def finalize_interview(
    interview_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> FinalizeResponse:
    """Public entry point for feedback-report generation."""
    session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
    tavus_conv_id = session_doc.get("tavus_conversation_id") if session_doc else None
    if tavus_conv_id:
        try:
            from app.services.tavus_service import end_tavus_conversation, sync_tavus_transcript
            await end_tavus_conversation(str(tavus_conv_id))
            await sync_tavus_transcript(interview_id, str(tavus_conv_id), db)
        except Exception as exc:
            log.warning("finalize.tavus_sync_failed", error=str(exc))

    report = await generate_feedback_report(interview_id, db)
    log.info(
        "interview.finalized",
        interview_id=interview_id,
        overall=report.overall_readiness,
        resume_gap_flags=len(report.resume_gap_flags),
    )
    return FinalizeResponse(interview_id=interview_id, report=report)


@router.get(
    "/{interview_id}/report",
    summary="Retrieve a previously generated feedback report",
    description=(
        "Returns the cached report created by /finalize.  If the report has "
        "not yet been generated, this endpoint responds 404; the caller "
        "should POST /finalize first."
    ),
    response_model=FeedbackReport,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"description": "No report yet — POST /finalize first."},
    },
)
async def get_report(
    interview_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
    force_refresh: bool = False,
) -> FeedbackReport:
    """Cached-report lookup with optional re-evaluation."""
    if force_refresh:
        return await generate_feedback_report(interview_id, db)
    return await fetch_cached_report(interview_id, db)


class AddTranscriptTurnRequest(BaseModel):
    speaker: str
    text: str


async def _generate_interviewer_follow_up(
    candidate_answer: str,
    recent_history: list[dict[str, Any]],
    role_ctx: dict[str, Any] | None,
) -> str:
    """Generate a realistic, adaptive technical follow-up question from the interviewer."""
    try:
        from app.services.llm_client import get_groq_client
        client = await get_groq_client()
        job_title = (
            str(role_ctx.get("job_title", "Software Engineer"))
            if role_ctx
            else "Software Engineer"
        )
        company = str(role_ctx.get("company_name", "")) if role_ctx else ""
        raw_competencies = role_ctx.get("core_competencies", []) if role_ctx else []
        competencies = [str(c) for c in raw_competencies] if raw_competencies else ["System Design", "Problem Solving", "Core Fundamentals"]

        system_prompt = (
            f"You are a friendly, highly skilled senior technical interviewer for the {job_title} role"
            + (f" at {company}" if company else "")
            + ".\n"
            f"Key competencies to probe: {', '.join(competencies[:6])}.\n"
            "Guidelines:\n"
            "1. Give a very brief natural acknowledgement of the candidate's last answer (1 short sentence).\n"
            "2. Follow up with exactly ONE focused, engaging technical or situational question probing depth, trade-offs, or the next core competency.\n"
            "3. Keep your total reply under 45 words — conversational, direct, and realistic.\n"
            "4. Never output greetings, markdown formatting, bullet points, or roleplay labels."
        )

        history_prompts: list[dict[str, str]] = []
        for h in recent_history[-4:]:
            role = "user" if h.get("speaker") == "You" else "assistant"
            history_prompts.append({"role": role, "content": str(h.get("text", ""))})

        history_prompts.append({"role": "user", "content": candidate_answer})

        response = await client.chat_completion(
            messages=[{"role": "system", "content": system_prompt}, *history_prompts],
            temperature=0.35,
            max_tokens=120,
        )
        if isinstance(response, str) and response.strip():
            return response.strip()
        return "That's insightful. Could you walk me through how you handled the trade-offs and edge cases in that implementation?"
    except Exception as err:
        log.warning("interviewer.follow_up_failed", error=str(err))
        return "Thanks for explaining that. Could you dive a bit deeper into the design choices and trade-offs you made?"


@router.get(
    "/{interview_id}/transcript",
    summary="Get live transcript turns for an interview",
    status_code=status.HTTP_200_OK,
)
async def get_interview_transcript(
    interview_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> dict[str, Any]:
    session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
    role_ctx = await db.role_context_matrices.find_one({"interview_id": interview_id})

    tavus_conv_id = session_doc.get("tavus_conversation_id") if session_doc else None
    if tavus_conv_id:
        try:
            from app.services.tavus_service import sync_tavus_transcript
            await sync_tavus_transcript(interview_id, str(tavus_conv_id), db)
            session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
        except Exception:  # noqa: BLE001
            pass

    turns: list[dict[str, Any]] = []
    if session_doc and session_doc.get("transcript_history"):
        turns = list(session_doc.get("transcript_history", []))

    if not turns:
        job_title = (
            role_ctx.get("job_title", "Software Engineer")
            if role_ctx
            else "Software Engineer"
        )
        company = role_ctx.get("company_name", "") if role_ctx else ""
        skills = role_ctx.get("core_competencies", []) if role_ctx else []
        focus_skill = skills[0] if skills else "technical architecture"
        welcome_text = (
            f"Hello! Welcome to your AI technical interview for the {job_title} role"
            + (f" at {company}" if company else "")
            + f". I have reviewed your background in {focus_skill}. To kick things off, could you briefly introduce yourself and share a technical project you are proud of?"
        )
        initial_turn = {"speaker": "Interviewer", "text": welcome_text, "timestamp": time.time()}
        turns = [initial_turn]
        await db.interview_sessions.update_one(
            {"interview_id": interview_id},
            {
                "$set": {
                    "transcript_history": turns,
                    "turn_count": 1,
                    "last_seen_at": time.time(),
                }
            },
            upsert=True,
        )

    return {"interview_id": interview_id, "turns": turns}


@router.post(
    "/{interview_id}/transcript",
    summary="Post a new transcript turn during an interview",
    status_code=status.HTTP_201_CREATED,
)
async def add_interview_transcript_turn(
    interview_id: str,
    body: AddTranscriptTurnRequest,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> dict[str, Any]:
    candidate_text = body.text.strip()
    turn = {
        "speaker": body.speaker,
        "text": candidate_text,
        "timestamp": time.time(),
    }

    session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
    existing_history: list[dict[str, Any]] = (
        list(session_doc.get("transcript_history", [])) if session_doc else []
    )

    new_turns: list[dict[str, Any]] = [turn]

    # Only synthesize interviewer follow-up in non-Tavus (audio/text) mode
    is_tavus = bool(session_doc and session_doc.get("tavus_conversation_id"))
    if not is_tavus and body.speaker.lower() in ("you", "candidate", "user") and len(candidate_text) >= 3:
        role_ctx = await db.role_context_matrices.find_one({"interview_id": interview_id})
        interviewer_response = await _generate_interviewer_follow_up(
            candidate_answer=candidate_text,
            recent_history=existing_history,
            role_ctx=role_ctx,
        )
        interviewer_turn = {
            "speaker": "Interviewer",
            "text": interviewer_response,
            "timestamp": time.time() + 0.1,
        }
        new_turns.append(interviewer_turn)

    await db.interview_sessions.update_one(
        {"interview_id": interview_id},
        {
            "$push": {"transcript_history": {"$each": new_turns}},
            "$inc": {"turn_count": len(new_turns)},
            "$set": {"last_seen_at": time.time()},
        },
        upsert=True,
    )

    updated_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
    turns = (
        updated_doc.get("transcript_history", [])
        if updated_doc
        else (existing_history + new_turns)
    )
    return {"interview_id": interview_id, "turns": turns}

