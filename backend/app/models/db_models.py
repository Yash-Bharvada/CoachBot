"""Typed mirror of the MongoDB document shapes.

Because ``motor`` returns plain ``dict`` objects at runtime, we define
TypedDict subclasses for each collection so that IDEs and type checkers can
reason about the schema without importing Pydantic on the hot path.
"""

from __future__ import annotations

from typing import Literal, TypedDict

DifficultyBaseline = Literal["easy", "medium", "hard"]


class RoleContextMatrixDoc(TypedDict, total=False):
    """Shape of a single row in the ``role_context_matrices`` collection.

    Additive to the v0.1 schema (CHANGE 4): the ``candidate_profile`` embedded
    document carries the structured output of Module 1's resume parser.  Existing indexes on
    ``(interview_id, created_at) remain valid since this is an embedded field.
    """

    interview_id: str
    job_title: str
    job_description: str
    company_name: str | None
    core_competencies: list[str]
    difficulty_baseline: DifficultyBaseline
    difficulty_index: int  # 0/1/2 equivalent of difficulty_baseline
    grounding_summary: str
    grounding_status: Literal["ok", "degraded", "skipped"]
    grounding_raw: dict | None  # never surfaced to the candidate
    tech_stack: list[str]
    seniority_indicators: list[str]
    candidate_profile: dict  # matches CandidateProfile Pydantic model shape
    created_at: float
    updated_at: float


class InterviewSessionDoc(TypedDict, total=False):
    """Shape of a single row in the ``interview_sessions`` collection."""

    interview_id: str
    candidate_id: str | None
    status: Literal["in_progress", "reconnecting", "finalized", "abandoned"]
    difficulty_index: int
    competencies_probed: list[str]
    competencies_pending: list[str]
    last_seen_at: float
    started_at: float
    ended_at: float | None
    turn_count: int


class TurnEvaluationDoc(TypedDict, total=False):
    """Shape of a single per-turn evaluation record (``turn_evaluations``)."""

    interview_id: str
    turn_index: int
    question_text: str
    candidate_text: str
    scores: dict  # {"relevance": float, "technical_depth": float, "clarity": float}
    overall_score: float
    difficulty_before: int
    difficulty_after: int
    timestamp: float


class FeedbackReportDoc(TypedDict, total=False):
    """Shape of a single cached feedback report (``feedback_reports``)."""

    interview_id: str
    report: dict  # matches the FeedbackReport Pydantic model
    generated_at: float
