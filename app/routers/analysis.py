"""Module 1 router — POST /api/v1/interviews/analyze-jd.

All business logic lives in :mod:`app.services.jd_analysis_service`; the
router only handles HTTP concerns: validation, dependency injection of the
database, response serialization, and rate limiting.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.routing import APIRouter
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError
from structlog import get_logger

from app.core.database import get_database
from app.core.exceptions import JDAnalysisError
from app.core.security import http_rate_limiter
from app.models.schemas import AnalyzeJDRequest, AnalyzeJDResponse
from app.services.jd_analysis_service import analyze_jd

log = get_logger(__name__)

router = APIRouter(dependencies=[Depends(http_rate_limiter())])


@router.post(
    "/analyze-jd",
    summary="Analyze a job description and initialize an interview session",
    description=(
        "Parse the supplied JD, optionally ground it with live web research, "
        "and return an `interview_id` together with a summarized Role Context "
        "Matrix.  If the live grounding provider times out the endpoint still "
        "succeeds (200) but flags `grounding_status: \"degraded\"`."
    ),
    response_model=AnalyzeJDResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        422: {
            "description": "Invalid or placeholder JD content — check `details`.",
        },
        500: {"description": "Internal service error."},
    },
)
async def analyze_jd_endpoint(
    request: AnalyzeJDRequest,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> AnalyzeJDResponse:
    """HTTP adapter around :func:`app.services.jd_analysis_service.analyze_jd`."""
    try:
        # Pydantic v2 already validated field-level constraints on ingest;
        # service-level validation (placeholder text, signal density) happens
        # inside analyze_jd and raises a JDAnalysisError (422) on failure.
        result = await analyze_jd(request, db)
    except JDAnalysisError:
        raise  # handler in main.py translates this to the documented 422 shape.
    except ValidationError as exc:  # pragma: no cover — defensive
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    log.info(
        "jd_analysis.completed",
        interview_id=result.interview_id,
        competencies=len(result.core_competencies),
        grounding=result.grounding_status,
    )
    return result
