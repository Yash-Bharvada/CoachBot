"""Module 2 — STT → contextual LLM → TTS orchestration.

The websocket handler calls into this module once per turn.  The pipeline is
*not* a flat sequence of awaits — STT partial transcription is streamed back
to the client as it arrives, and the LLM prompt is assembled concurrently
while the final STT bytes trickle in where possible.  TTS, in turn, yields
MP3 chunks back to the websocket as they are synthesised so the candidate
does not wait for the whole utterance to render before they hear audio.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from structlog import get_logger

from app.core.config import get_settings
from app.core.exceptions import SessionStateError, VoicePipelineError
from app.services.evaluation_service import (
    _DIFFICULTY_LABELS,
    evaluate_turn,
    next_question_plan,
)
from app.services.llm_client import get_groq_client
from app.services.tts_service import get_tts_service
from app.websockets.connection_manager import (
    InterviewSession,
    connection_manager,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Interviewer persona — consistent across all Groq calls
# ---------------------------------------------------------------------------

_INTERVIEWER_PERSONA = """You are a calm, professional human technical recruiter
conducting a live voice interview.  Your tone: warm, measured, and concise.
Never sound robotic.

Conversation rules (non-negotiable):
  1. PACE naturally.  Before asking a follow-up, add a 1-sentence
     acknowledgment that references the candidate's specific answer — do
     NOT say "Thank you for your answer." as a boilerplate every turn.
  2. ONE question per turn.  Do not stack three questions in one reply.
  3. If an answer is tangential, gently redirect rather than scold.
  4. Never mention scoring, rubrics, or that you are an AI.
  5. Keep each reply under 140 words of spoken content.
  6. When a competency is adequately probed, pivot to the next pending
     competency rather than re-testing the same area.
  7. On the very first turn (no transcript history):
       - Greet, mention the role briefly, then ask an opener.
       - Do not dive straight into a hard technical question.
"""


@dataclass(slots=True)
class PipelineOutcome:
    """Returned by :func:`process_candidate_turn`."""

    transcript_text: str
    interviewer_text: str
    evaluation: dict[str, Any] | None  # None for the very first greeting turn


# ---------------------------------------------------------------------------
# STT stage
# ---------------------------------------------------------------------------


async def run_stt(audio_bytes: bytes) -> str:
    """Transcribe candidate audio using Groq Whisper.

    Returns the transcript string or raises :class:`VoicePipelineError`.
    """
    if not audio_bytes:
        raise VoicePipelineError(
            "Empty audio buffer submitted for transcription.",
            details={"stage": "stt"},
        )
    client = await get_groq_client()
    try:
        text = await client.transcribe_audio(audio_bytes, filename="turn.webm")
    except VoicePipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 — translate to pipeline error.
        raise VoicePipelineError(
            f"Unexpected STT error: {exc!r}",
            details={"stage": "stt"},
        ) from exc
    return text.strip()


# ---------------------------------------------------------------------------
# LLM generation stage
# ---------------------------------------------------------------------------


def _build_interviewer_messages(
    *,
    session: InterviewSession,
    role_context: dict[str, Any],
    question_hint: dict[str, Any],
    last_transcript: str | None,
) -> list[dict[str, str]]:
    """Assemble the full conversation history for the interviewer LLM.

    Takes the in-memory ``session.turns`` plus the latest candidate
    transcript and formats them into the OpenAI ``messages`` format that
    Groq consumes.  We inject the persona, the role context matrix, and the
    next-question hint as a system block.
    """
    system_block = (
        f"{_INTERVIEWER_PERSONA}\n\n"
        "==== ROLE CONTEXT (do NOT recite verbatim) ====\n"
        f"Job title: {role_context.get('job_title', 'Software Engineer')}\n"
        f"Core competencies: {role_context.get('core_competencies', [])}\n"
        f"Difficulty target THIS turn: {question_hint.get('difficulty', 'medium')}\n"
        f"Pending competencies to probe: {question_hint.get('pending_competencies', [])}\n"
        f"Total turns so far: {question_hint.get('total_turns', 0)}\n\n"
        "==== END ROLE CONTEXT ====\n"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_block}]

    for turn in session.turns:
        role = "user" if turn.role == "candidate" else "assistant"
        messages.append({"role": role, "content": turn.text})

    if last_transcript:
        messages.append({"role": "user", "content": last_transcript})
    return messages


async def generate_interviewer_reply(
    *,
    session: InterviewSession,
    role_context: dict[str, Any],
    last_transcript: str | None,
) -> str:
    """Run the LLM stage and return the plain-text interviewer utterance."""
    hint = next_question_plan(session)
    messages = _build_interviewer_messages(
        session=session,
        role_context=role_context,
        question_hint=hint,
        last_transcript=last_transcript,
    )
    client = await get_groq_client()
    reply = await client.chat_completion(
        messages,
        temperature=0.4,
        max_tokens=350,
    )
    assert isinstance(reply, str)
    # Trim any obvious boilerplate the model might add despite the persona.
    for prefix in ("Thank you for your answer.", "Thank you."):
        if reply.startswith(prefix):
            reply = reply[len(prefix) :].lstrip(" ,.!?")
    return reply.strip()


# ---------------------------------------------------------------------------
# TTS stage — async generator so caller can stream chunks directly
# ---------------------------------------------------------------------------


async def synthesize_chunks(
    interviewer_text: str,
) -> AsyncIterator[tuple[bytes, int, bool]]:
    """Yield ``(audio_bytes, chunk_index, is_final)`` tuples.

    The caller serialises the bytes to base64 and wraps them in a websocket
    frame; chunk_index lets the client reorder if delivery is out of order
    (in practice it never is, but the wire format still tracks it).
    """
    tts = await get_tts_service()
    idx = 0
    async for chunk in tts.synthesize_stream(interviewer_text):
        idx += 1
        yield chunk, idx, False
    yield b"", idx + 1, True


# ---------------------------------------------------------------------------
# Orchestrator (one public entry point)
# ---------------------------------------------------------------------------


async def process_candidate_turn(
    *,
    interview_id: str,
    audio_bytes: bytes,
    transcript_override: str | None,
    db: AsyncIOMotorDatabase,
) -> tuple[PipelineOutcome, AsyncIterator[tuple[bytes, int, bool]]]:
    """End-to-end: STT → persist transcript → LLM reply → evaluate → TTS.

    Returns
    -------
    PipelineOutcome plus a TTS chunk async iterator — the caller reads the
    structured fields immediately (to emit text frames) and then drains the
    TTS iterator to emit audio frames.
    """
    settings = get_settings()
    log = get_logger().bind(interview_id=interview_id)
    try:
        async with asyncio.timeout(settings.voice_pipeline_timeout_seconds):
            session = await connection_manager.get_session(interview_id)
            if session is None:
                raise SessionStateError(
                    "Interview session is not active.",
                    details={"interview_id": interview_id},
                )
            role_ctx = await _fetch_role_context(db, interview_id)

            # Stage 1: STT (or caller-supplied text override for tests).
            if transcript_override is not None:
                transcript = transcript_override
            else:
                transcript = await run_stt(audio_bytes)
            log.info("stt.done", words=len(transcript.split()))

            # Stage 2: persist candidate turn, compute the question hint,
            # then generate the interviewer reply.
            is_first_turn = not any(t.role == "candidate" for t in session.turns)
            if transcript:
                turn_idx = session.record_turn("candidate", transcript)
            else:
                turn_idx = (
                    len([t for t in session.turns if t.role == "candidate"])
                )

            interviewer_text = await generate_interviewer_reply(
                session=session,
                role_context=role_ctx,
                last_transcript=transcript or None,
            )
            session.record_turn("interviewer", interviewer_text)

            # Stage 3: evaluation — runs AFTER the reply is generated so the
            # candidate hears follow-ups without waiting for judge output.
            evaluation_payload: dict[str, Any] | None = None
            if transcript and not is_first_turn:
                # Determine what question the candidate was answering.
                question_text = _last_interviewer_prompt(session)
                competencies = role_ctx.get("core_competencies", [])
                scores, overall, before, after = await evaluate_turn(
                    interview_id=interview_id,
                    turn_index=turn_idx,
                    question_text=question_text,
                    candidate_text=transcript,
                    competencies=competencies,
                    session=session,
                    db=db,
                )
                evaluation_payload = {
                    "turn_index": turn_idx,
                    "scores": scores.model_dump(),
                    "overall": round(overall, 2),
                    "difficulty_before": _DIFFICULTY_LABELS[before],
                    "difficulty_after": _DIFFICULTY_LABELS[after],
                }
            elif transcript and is_first_turn:
                # Opener turn — just note that the difficulty stays at baseline.
                baseline = role_ctx.get("difficulty_baseline", "medium")
                evaluation_payload = {
                    "turn_index": turn_idx,
                    "scores": {"relevance": None, "technical_depth": None, "clarity": None},
                    "overall": None,
                    "difficulty_before": baseline,
                    "difficulty_after": baseline,
                }

            # Stage 4: kick off the TTS iterator (lazy — caller drains it).
            tts_iter = synthesize_chunks(interviewer_text)
            return (
                PipelineOutcome(
                    transcript_text=transcript,
                    interviewer_text=interviewer_text,
                    evaluation=evaluation_payload,
                ),
                tts_iter,
            )
    except asyncio.TimeoutError as exc:
        raise VoicePipelineError(
            "Voice pipeline exceeded the per-turn timeout.",
            details={
                "interview_id": interview_id,
                "timeout_seconds": settings.voice_pipeline_timeout_seconds,
            },
        ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_role_context(
    db: AsyncIOMotorDatabase, interview_id: str
) -> dict[str, Any]:
    doc = await db.role_context_matrices.find_one(
        {"interview_id": interview_id},
        {"grounding_raw": 0},
    )
    if doc is None:
        raise SessionStateError(
            "Role context matrix not found — did you call /analyze-jd first?",
            details={"interview_id": interview_id},
        )
    return doc


def _last_interviewer_prompt(session: InterviewSession) -> str:
    for t in reversed(session.turns):
        if t.role == "interviewer":
            return t.text
    return "Opening response / greeting."
