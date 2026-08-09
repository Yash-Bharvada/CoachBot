"""Module 1 router — POST /api/v1/interviews/onboard (+ legacy /analyze-jd).

Primary business logic lives in :mod:`app.services.onboarding_service`; the
router only handles HTTP concerns: validation, dependency injection of the
database, response serialization, rate limiting, and ``multipart/form-data``
handling for the resume upload.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, File, Form, HTTPException, UploadFile, status
from fastapi.routing import APIRouter
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError
from structlog import get_logger

from app.core.database import get_database
from app.core.exceptions import JDAnalysisError, ResumeParsingError
from app.core.security import http_rate_limiter
from app.models.schemas import AnalyzeJDRequest, AnalyzeJDResponse, OnboardResponse
from app.services.onboarding_service import analyze_jd, onboard

log = get_logger(__name__)

router = APIRouter(dependencies=[Depends(http_rate_limiter())])


@router.post(
    "/onboard",
    summary="Onboard with JD + resume to initialize an interview session",
    description=(
        "Parse the supplied job description together with a PDF/DOCX resume "
        "upload, optionally ground the JD with live web research, and return "
        "an `interview_id` together with a merged Role & Candidate Context "
        "Matrix.  Resume uploads are validated for size + format; corrupt or "
        "unsupported files return 422 without crashing the request.  If the "
        "live grounding provider times out the endpoint still succeeds (201) "
        "but flags `grounding_status: \"degraded\"`."
    ),
    response_model=OnboardResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        422: {
            "description": (
                "Invalid JD content, corrupt/unsupported resume file, or "
                "resume exceeding the configured size cap — check `details`."
            ),
        },
        500: {"description": "Internal service error."},
    },
)
async def onboard_endpoint(
    job_title: Annotated[
        str,
        Form(
            min_length=2,
            max_length=160,
            description="Canonical role name, e.g. 'Senior Backend Engineer'.",
        ),
    ],
    job_description: Annotated[
        str,
        Form(
            min_length=20,
            max_length=20_000,
            description="Full job description text to parse and ground.",
        ),
    ],
    resume: Annotated[
        UploadFile,
        File(
            description="Candidate resume — PDF or DOCX only, subject to MAX_RESUME_SIZE_MB.",
        ),
    ],
    company_name: Annotated[
        str | None,
        Form(
            max_length=120,
            description="Optional company name used for live web grounding.",
        ),
    ] = None,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)] = None,  # type: ignore[assignment]
) -> OnboardResponse:
    """HTTP adapter around :func:`app.services.onboarding_service.onboard`.

    **File upload safety** (audit-integrity gate, CHANGE 5):
      * Format validated by extension whitelist inside ``parse_candidate_profile``.
      * Size enforced with a streaming chunk reader so a malicious client can't
        OOM the process by sending a 1GB file marked ``Content-Type: application/pdf``.
      * Raw file bytes are released from RAM as soon as text extraction returns;
        the resume file itself is **never persisted** to Mongo or disk unless
        re-download is an explicit product requirement later.
    """
    # Build the JD request payload from Form fields so the service layer
    # treats the input identically to the legacy /analyze-jd path.
    request = AnalyzeJDRequest(
        job_title=job_title,
        job_description=job_description,
        company_name=company_name,
    )
    try:
        result = await onboard(request, db, resume=resume)
    except (JDAnalysisError, ResumeParsingError):
        raise  # handler in main.py translates to the documented 422 shape.
    except ValidationError as exc:  # pragma: no cover — defensive
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    finally:
        # Explicitly close the UploadFile so FastAPI's underlying temp file
        # handle is released even on the exception path.
        await resume.close()
    log.info(
        "onboard.completed",
        interview_id=result.interview_id,
        competencies=len(result.core_competencies),
        grounding=result.grounding_status,
        highlights=len(result.candidate_highlights),
    )
    return result


@router.post(
    "/analyze-jd",
    summary="[DEPRECATED] Analyze a job description to initialize an interview session",
    description=(
        "Legacy alias for the pre-resume onboarding flow.  Clients should "
        "migrate to POST /onboard which accepts a resume upload alongside "
        "the JD.  This endpoint still works but returns an empty "
        "`candidate_highlights` list."
    ),
    response_model=AnalyzeJDResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        422: {
            "description": "Invalid or placeholder JD content — check `details`.",
        },
        500: {"description": "Internal service error."},
    },
    deprecated=True,
)
async def analyze_jd_endpoint(
    request: AnalyzeJDRequest,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
) -> AnalyzeJDResponse:
    """HTTP adapter around :func:`app.services.onboarding_service.analyze_jd` (legacy)."""
    try:
        result = await analyze_jd(request, db)
    except JDAnalysisError:
        raise
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
