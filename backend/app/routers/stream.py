"""Module 2 websocket handler — ``WS /api/v1/interviews/{interview_id}/stream``.

This single file owns the *full* websocket lifecycle for a live interview:

  1. Handshake + rate limit + interview-id validation + role-context check
  2. Client audio buffer accumulation (bounded by ``MAX_AUDIO_BUFFER_MB``)
  3. End-of-turn dispatch to :mod:`voice_pipeline_service`
  4. Streamed transcript → interviewer text → evaluation → audio chunks to client
  5. Graceful disconnects (grace period) and explicit close codes

Wire frames
----------

Client → Server frames (JSON):
  * ``{"type": "audio", "audio_b64": "...", "codec": "pcm_s16le_16k", "end_of_turn": bool}``
  * ``{"type": "text", "text": "..."}`` — for tests / manual typing

Server → Client frames (JSON):
  * ``{"type": "transcript", "text": "...", "is_final": bool}``
  * ``{"type": "interviewer_text", "text": "...", "turn_index": int}``
  * ``{"type": "audio", "audio_b64": "...", "chunk_index": int, "is_final": bool}``
  * ``{"type": "evaluation", "turn_index": int, "scores": {...}, difficulty_before/after: str}``
  * ``{"type": "error", "error": str, "message": str, "details": {...}}`` — followed by close
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError
from structlog import get_logger

from app.core.config import get_settings
from app.core.database import get_mongo_client
from app.core.exceptions import (
    AppException,
    SessionStateError,
    VoicePipelineError,
    ws_error_frame,
)
from app.core.security import ws_rate_limiter
from app.models.schemas import (
    WSServerAudioFrame,
    WSServerErrorFrame,
    WSServerEvaluationFrame,
    WSServerInterviewerFrame,
    WSServerTranscriptFrame,
)
from app.services.voice_pipeline_service import (
    generate_interviewer_reply,
    process_candidate_turn,
    synthesize_chunks,
)
from app.websockets.connection_manager import (
    WS_CLOSE_ABANDONED,
    WS_CLOSE_NORMAL,
    WS_CLOSE_PIPELINE_FAILURE,
    WS_CLOSE_SERVER_ERROR,
    InterviewSession,
    connection_manager,
)

log = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _emit_json(ws: WebSocket, frame: Any) -> None:
    """Send a Pydantic model as JSON down a websocket.

    Send errors are swallowed because the socket may already be in a closing
    state and there is nothing meaningful we can do to recover.
    """
    try:
        await ws.send_text(json.dumps(frame.model_dump(mode="json")))
    except RuntimeError:
        pass


async def _emit_error_and_close(
    ws: WebSocket, exc: AppException, code: int
) -> None:
    """Unrecoverable error path: emit structured error frame, then close."""
    err = WSServerErrorFrame(**ws_error_frame(exc))
    await _emit_json(ws, err)
    try:
        await ws.close(code=code)
    except RuntimeError:
        pass


def _audio_bytes_from_frame(frame: dict[str, Any]) -> bytes:
    """Decode base64 audio payload, returning b"" on malformed input."""
    raw = frame.get("audio_b64", "")
    try:
        return base64.b64decode(raw or "")
    except Exception:  # noqa: BLE001
        return b""


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@router.websocket("/{interview_id}/stream")
async def interview_stream(
    websocket: WebSocket,
    interview_id: str,
) -> None:
    """Live voice stream for an initialized interview session."""
    settings = get_settings()
    log = get_logger().bind(interview_id=interview_id)

    # --- 0. Acquire DB handle for this connection.
    mongo = get_mongo_client()
    db: AsyncIOMotorDatabase = mongo[settings.mongo_db_name]

    # --- 1. Pre-accept validation + rate limit.  If we fail here we close
    #        immediately *without* accepting the socket so the client can see a 4xx.
    try:
        await ws_rate_limiter(websocket)
    except Exception:  # noqa: BLE001 — ws_rate_limiter already closed the socket.
        return

    role_ctx = await db.role_context_matrices.find_one(
        {"interview_id": interview_id},
        {
            "_id": 0,
            "core_competencies": 1,
            "difficulty_baseline": 1,
            "difficulty_index": 1,
        },
    )
    if role_ctx is None:
        await websocket.close(code=WS_CLOSE_ABANDONED)
        log.warning("ws.rejected.no_role_context", interview_id=interview_id)
        return
    await websocket.accept()

    # --- 2. Find or create the in-memory session (supports reconnect).
    session: InterviewSession | None = await connection_manager.reconnect(
        interview_id
    )
    if session is None:
        session = await connection_manager.get_session(interview_id)
    if session is None:
        competencies = role_ctx.get("core_competencies", [])
        difficulty = int(role_ctx.get("difficulty_index", 1))
        session = await connection_manager.create_session(
            interview_id,
            competencies_pending=competencies,
            difficulty_baseline=difficulty,
        )
    log.info("ws.connected", status=session.status)

    # --- 3. Main message loop.
    audio_buffer = bytearray()
    audio_buffer_bytes = 0
    max_bytes = settings.max_audio_buffer_mb * 1024 * 1024
    try:
        is_first_turn = not any(t.role == "interviewer" for t in session.turns)
        if is_first_turn:
            await _run_first_turn_greeting(
                websocket, interview_id, session, db
            )

        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                log.info("ws.client_disconnect")
                await connection_manager.mark_reconnecting(interview_id)
                return
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await _emit_error_and_close(
                    websocket,
                    SessionStateError(
                        "Invalid JSON frame.",
                        details={"bytes": len(raw)},
                    ),
                    code=WS_CLOSE_SERVER_ERROR,
                )
                return

            frame_type = frame.get("type")

            # ---------- Client text frame (tests / manual typing).
            if frame_type == "text":
                text = (frame.get("text") or "").strip()
                if not text:
                    continue
                await _dispatch_turn(
                    websocket,
                    interview_id=interview_id,
                    audio_bytes=b"",
                    transcript_override=text,
                    db=db,
                )
                continue

            # ---------- Audio chunk accumulation (main code path).
            if frame_type == "audio":
                chunk = _audio_bytes_from_frame(frame)
                if chunk:
                    if audio_buffer_bytes + len(chunk) > max_bytes:
                        await _emit_error_and_close(
                            websocket,
                            VoicePipelineError(
                                "Audio buffer exceeded configured memory limit.",
                                details={
                                    "max_mb": settings.max_audio_buffer_mb,
                                },
                            ),
                            code=WS_CLOSE_PIPELINE_FAILURE,
                        )
                        return
                    audio_buffer.extend(chunk)
                    audio_buffer_bytes += len(chunk)
                    # Tiny UX hint: send an empty transcript delta so the
                    # client knows the server is still receiving bytes.
                    await _emit_json(
                        websocket,
                        WSServerTranscriptFrame(text="", is_final=False),
                    )
                if frame.get("end_of_turn", False):
                    audio_bytes = bytes(audio_buffer)
                    audio_buffer.clear()
                    audio_buffer_bytes = 0
                    await _dispatch_turn(
                        websocket,
                        interview_id=interview_id,
                        audio_bytes=audio_bytes,
                        transcript_override=None,
                        db=db,
                    )
                continue

            # ---------- Unknown frame type.
            await _emit_error_and_close(
                websocket,
                SessionStateError(
                    f"Unknown frame type '{frame_type}'.",
                    details={"frame_type": frame_type},
                ),
                code=WS_CLOSE_SERVER_ERROR,
            )
            return
    except WebSocketDisconnect:
        log.info("ws.disconnect")
        await connection_manager.mark_reconnecting(interview_id)
        return
    except VoicePipelineError as exc:
        log.warning("ws.pipeline_error", error=str(exc))
        await _emit_error_and_close(
            websocket, exc, code=WS_CLOSE_PIPELINE_FAILURE
        )
    except SessionStateError as exc:
        log.warning("ws.session_error", error=str(exc))
        await _emit_error_and_close(websocket, exc, code=WS_CLOSE_NORMAL)
    except AppException as exc:
        log.exception("ws.app_exception")
        await _emit_error_and_close(
            websocket, exc, code=WS_CLOSE_SERVER_ERROR
        )
    except Exception as exc:  # noqa: BLE001 — NEVER silent death.
        log.exception("ws.unhandled_exception")
        await _emit_error_and_close(
            websocket,
            VoicePipelineError(
                "Unexpected server error.",
                details={"error_type": type(exc).__name__},
            ),
            code=WS_CLOSE_SERVER_ERROR,
        )


# ---------------------------------------------------------------------------
# First-turn greeting + turn dispatcher
# ---------------------------------------------------------------------------


async def _run_first_turn_greeting(
    ws: WebSocket,
    interview_id: str,
    session: InterviewSession,
    db: AsyncIOMotorDatabase,
) -> None:
    """Kick off the interview with a warm opener.

    We deliberately route this through the same LLM persona as regular turns
    so the greeting matches the interviewer's established voice.
    """
    role_ctx = await db.role_context_matrices.find_one(
        {"interview_id": interview_id}, {"_id": 0}
    ) or {}
    greeting = await generate_interviewer_reply(
        session=session,
        role_context=role_ctx,
        last_transcript=None,
    )
    session.record_turn("interviewer", greeting)
    await _emit_json(
        ws, WSServerInterviewerFrame(text=greeting, turn_index=0)
    )
    chunk_idx = 0
    async for audio_bytes, _ci, is_final in synthesize_chunks(greeting):
        chunk_idx += 1
        if audio_bytes:
            await _emit_json(
                ws,
                WSServerAudioFrame(
                    audio_b64=base64.b64encode(audio_bytes).decode(),
                    chunk_index=chunk_idx,
                    is_final=is_final,
                ),
            )
        if is_final and not audio_bytes:
            await _emit_json(
                ws,
                WSServerAudioFrame(
                    audio_b64="", chunk_index=chunk_idx, is_final=True
                ),
            )


async def _dispatch_turn(
    ws: WebSocket,
    *,
    interview_id: str,
    audio_bytes: bytes,
    transcript_override: str | None,
    db: AsyncIOMotorDatabase,
) -> None:
    """Run one full pipeline and stream all server frames back to the client."""
    outcome, tts_iter = await process_candidate_turn(
        interview_id=interview_id,
        audio_bytes=audio_bytes,
        transcript_override=transcript_override,
        db=db,
    )
    # 1. Final transcript frame.
    if outcome.transcript_text:
        await _emit_json(
            ws,
            WSServerTranscriptFrame(
                text=outcome.transcript_text, is_final=True
            ),
        )
    # 2. Interviewer text + turn_index.
    session = await connection_manager.get_session(interview_id)
    turn_count = (
        len([t for t in session.turns if t.role == "interviewer"])
        if session is not None
        else 1
    )
    await _emit_json(
        ws,
        WSServerInterviewerFrame(
            text=outcome.interviewer_text, turn_index=turn_count
        ),
    )
    # 3. Audio chunks.
    async for abytes, idx, is_final in tts_iter:
        if abytes:
            await _emit_json(
                ws,
                WSServerAudioFrame(
                    audio_b64=base64.b64encode(abytes).decode(),
                    chunk_index=idx,
                    is_final=is_final,
                ),
            )
        if is_final and not abytes:
            await _emit_json(
                ws,
                WSServerAudioFrame(
                    audio_b64="", chunk_index=idx, is_final=True
                ),
            )
    # 4. Evaluation frame (if any).
    ev = outcome.evaluation
    if ev and ev.get("scores"):
        scores = ev.get("scores") or {}
        numeric = {
            k: float(v)
            for k, v in scores.items()
            if isinstance(v, (int, float))
        }
        if numeric:
            try:
                await _emit_json(
                    ws,
                    WSServerEvaluationFrame(
                        turn_index=ev["turn_index"],
                        scores=numeric,
                        difficulty_before=ev.get("difficulty_before", "medium"),
                        difficulty_after=ev.get("difficulty_after", "medium"),
                    ),
                )
            except (ValidationError, KeyError):  # pragma: no cover
                logger = get_logger().bind(interview_id=interview_id)
                logger.warning("evaluation.frame_skipped", evaluation=ev)
