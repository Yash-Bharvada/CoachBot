"""Typed exception hierarchy for the Interview Prep Simulator.

Every non-trivial failure surface in the application raises a subclass of
:class:`AppException`.  A single handler registered on the FastAPI app maps
each subclass to a specific HTTP status code and structured error body so
that clients can rely on a uniform error shape.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse


class AppException(Exception):
    """Base class for every domain-level exception in the platform.

    Parameters
    ----------
    message:
        Human readable explanation surfaced to the API consumer.
    status_code:
        HTTP status code used when translating the exception to a response.
    details:
        Arbitrary structured data that supplements the *message*, e.g. the
        interview id that failed or which external service timed out.
    """

    message: str
    status_code: int
    details: dict[str, Any]

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class JDAnalysisError(AppException):
    """Raised when the job description cannot be parsed into a role matrix."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=422, details=details)


class GroundingTimeoutError(AppException):
    """Raised when live web grounding does not return in time.

    The JD analysis endpoint explicitly catches this exception and falls
    back to JD-only extraction instead of propagating a 5xx to the client.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=504, details=details)


class VoicePipelineError(AppException):
    """Raised when any stage of the STT→LLM→TTS chain fails irrecoverably."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=502, details=details)


class EvaluationError(AppException):
    """Raised when the per-turn evaluator cannot produce structured scores."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=500, details=details)


class ReportGenerationError(AppException):
    """Raised when feedback-report aggregation produces inconsistent data."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=500, details=details)


class InterviewNotFoundError(AppException):
    """Raised when a client references an interview id that is not stored."""

    def __init__(self, interview_id: str) -> None:
        super().__init__(
            message=f"Interview '{interview_id}' does not exist.",
            status_code=404,
            details={"interview_id": interview_id},
        )


class SessionStateError(AppException):
    """Raised when websocket session state is in an inconsistent shape."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=400, details=details)


async def app_exception_handler(_request: Request, exc: AppException) -> JSONResponse:
    """Translate :class:`AppException` to a uniform JSON error payload."""
    body = {
        "error": exc.__class__.__name__,
        "message": exc.message,
        "details": exc.details,
    }
    return JSONResponse(status_code=exc.status_code, content=body)


def ws_error_frame(exc: AppException) -> dict[str, Any]:
    """Return the structured error frame sent down the websocket before close."""
    return {
        "type": "error",
        "error": exc.__class__.__name__,
        "message": exc.message,
        "details": exc.details,
    }
