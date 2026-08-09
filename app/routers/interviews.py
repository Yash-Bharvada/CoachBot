"""Module 4 router — session metadata, /finalize, and cached /report.

Business logic lives in :mod:`app.services.feedback_service`; this module is
the HTTP glue layer: dependency injection, rate limiting, status codes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, status
from fastapi.routing import APIRouter
from motor.motor_asyncio import AsyncIOMotorDatabase
from structlog import get_logger

from app.core.database import get_database
from app.core.exceptions import InterviewNotFoundError
from app.core.security import http_rate_limiter
from app.models.schemas import (
    DifficultyLevel,
    FeedbackReport,
    FinalizeResponse,
    InterviewSessionSummary,
)
from app.services.feedback_service import (
    fetch_cached_report,
    generate_feedback_report,
)
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


@router.post(
    "/{interview_id}/finalize",
    summary="End the interview and generate a structured feedback report",
    description=(
        "Aggregates turn evaluations, runs a sentiment + filler-word analysis, "
        "and generates model-answer comparisons for the weakest 2–3 turns.  "
        "The report is cached so a subsequent GET /report call is free."
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
    report = await generate_feedback_report(interview_id, db)
    log.info(
        "interview.finalized",
        interview_id=interview_id,
        overall=report.overall_readiness,
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
) -> FeedbackReport:
    """Cached-report lookup."""
    return await fetch_cached_report(interview_id, db)
