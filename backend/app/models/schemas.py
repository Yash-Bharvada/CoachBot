"""Pydantic v2 request/response models for every public API surface.

Every route and websocket frame that crosses a service boundary is typed by
one of the models in this file.  Fields use ``Field(..., description=...)``
so that the generated OpenAPI docs are self-explanatory without external
documentation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

DifficultyLevel = Literal["easy", "medium", "hard"]


class BaseResponse(BaseModel):
    """Base class for JSON responses — forbid extra keys to catch typos."""

    model_config = ConfigDict(extra="forbid")


class CandidateProfile(BaseResponse):
    """Structured Candidate Profile extracted from an uploaded resume.

    Produced by :mod:`app.services.resume_parsing_service` after raw-text
    extraction from PDF/DOCX and an LLM pass.  Embedded in the persisted
    Role Context Matrix under ``candidate_profile`` and surfaced to the
    frontend as ``candidate_highlights``.
    """

    skills: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Technical and soft skills explicitly listed or implied.",
        ),
    ]
    past_roles: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Prior job titles with short company/duration context.",
        ),
    ]
    notable_projects: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="3-5 highest-signal projects with outcomes when stated.",
        ),
    ]
    education: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Degrees, certifications, and institutions where relevant.",
        ),
    ]


class ResumeGapFlag(BaseResponse):
    """Single resume-vs-transcript discrepancy surfaced in the feedback report.

    Always phrased constructively (coaching tone, not accusatory).  The list
    is empty when every stated claim is adequately substantiated in the
    live interview.
    """

    claim: Annotated[
        str,
        Field(
            ...,
            description="The specific skill / project / role statement from the resume.",
        ),
    ]
    issue: Annotated[
        str,
        Field(
            ...,
            description=(
                "Constructive one-sentence note: what was missing from the "
                "live answer and why being ready to go deeper would help."
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Module 1 — Onboarding (JD + Resume)
# ---------------------------------------------------------------------------


class AnalyzeJDRequest(BaseModel):
    """Input payload for POST /api/v1/interviews/analyze-jd (deprecated alias)."""

    job_title: Annotated[
        str,
        Field(
            ...,
            min_length=2,
            max_length=160,
            description="Canonical role name, e.g. 'Senior Backend Engineer'.",
        ),
    ]
    job_description: Annotated[
        str,
        Field(
            ...,
            min_length=20,
            max_length=20_000,
            description="Full job description text to parse and ground.",
        ),
    ]
    company_name: Annotated[
        str | None,
        Field(
            default=None,
            max_length=120,
            description="Optional company name used for live web grounding.",
        ),
    ]


class OnboardResponse(BaseResponse):
    """Response returned after onboarding: JD parse + resume parse merged."""

    interview_id: Annotated[
        str,
        Field(..., description="Opaque identifier that represents this interview."),
    ]
    core_competencies: Annotated[
        list[str],
        Field(..., description="Deduplicated, human-readable list of competencies."),
    ]
    difficulty_baseline: Annotated[
        DifficultyLevel,
        Field(..., description="Starting difficulty inferred from seniority signals."),
    ]
    grounding_summary: Annotated[
        str,
        Field(
            ...,
            description=(
                "Single-paragraph summary of live research (interview format, "
                "industry trends, etc.).  Safe to surface to the candidate."
            ),
        ),
    ]
    grounding_status: Annotated[
        Literal["ok", "degraded", "skipped"],
        Field(
            default="ok",
            description=(
                "Whether live web-grounding completed successfully ('ok'), "
                "timed out so we fell back to JD-only analysis ('degraded'), "
                "or was explicitly disabled ('skipped')."
            ),
        ),
    ]
    candidate_highlights: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                "3-5 resume-derived highlights surfaced to the frontend for "
                "the candidate confirmation preview (top skills, most "
                "impressive roles, and strongest projects)."
            ),
        ),
    ]


AnalyzeJDResponse = OnboardResponse


# ---------------------------------------------------------------------------
# Module 2 — websocket frames
# ---------------------------------------------------------------------------


class WSClientAudioFrame(BaseModel):
    """Frame sent by the client containing a chunk of opus/pcm base64 audio."""

    type: Literal["audio"] = "audio"
    audio_b64: Annotated[
        str,
        Field(
            ...,
            description="Base64-encoded PCM/Opus audio bytes appended to the turn buffer.",
        ),
    ]
    codec: Annotated[
        Literal["pcm_s16le_16k", "opus", "webm"],
        Field(default="pcm_s16le_16k", description="Codec used for the payload."),
    ]
    end_of_turn: Annotated[
        bool,
        Field(default=False, description="True when the candidate has finished speaking."),
    ]


class WSClientTextFrame(BaseModel):
    """Client-sent text frame used for tests and mid-turn corrections."""

    type: Literal["text"] = "text"
    text: Annotated[str, Field(..., min_length=1, max_length=4000)]


class WSServerTranscriptFrame(BaseModel):
    """Partial transcript of the candidate's speech streamed to the client."""

    type: Literal["transcript"] = "transcript"
    text: Annotated[str, Field(..., description="Latest transcript delta.")]
    is_final: Annotated[
        bool, Field(default=False, description="True when the STT stage is complete.")
    ]


class WSServerInterviewerFrame(BaseModel):
    """Textual version of what the TTS stage is about to speak."""

    type: Literal["interviewer_text"] = "interviewer_text"
    text: Annotated[str, Field(...)]
    turn_index: Annotated[
        int, Field(..., description="1-based candidate-answer counter.")
    ]


class WSServerAudioFrame(BaseModel):
    """Chunk of synthesized interviewer audio streamed back to the client."""

    type: Literal["audio"] = "audio"
    audio_b64: Annotated[str, Field(...)]
    chunk_index: Annotated[int, Field(...)]
    is_final: Annotated[bool, Field(default=False)]


class WSServerEvaluationFrame(BaseModel):
    """Per-turn evaluation scores — delivered just after the TTS stream ends."""

    type: Literal["evaluation"] = "evaluation"
    turn_index: Annotated[int, Field(...)]
    scores: Annotated[
        dict[str, float],
        Field(
            ...,
            description="Rubric keys ('relevance', 'technical_depth', 'clarity') -> 0..100.",
        ),
    ]
    difficulty_before: Annotated[DifficultyLevel, Field(...)]
    difficulty_after: Annotated[DifficultyLevel, Field(...)]


class WSServerErrorFrame(BaseModel):
    """Structured error emitted just before the server closes the socket."""

    type: Literal["error"] = "error"
    error: Annotated[str, Field(...)]
    message: Annotated[str, Field(...)]
    details: Annotated[dict[str, Any], Field(default_factory=dict)]


# ---------------------------------------------------------------------------
# Module 3 — turn evaluation (internal + response components)
# ---------------------------------------------------------------------------


class RubricScores(BaseModel):
    """Structured output of the per-turn LLM-as-judge call."""

    relevance: Annotated[
        float,
        Field(..., ge=0, le=100, description="Does the answer address the question?"),
    ]
    technical_depth: Annotated[
        float,
        Field(..., ge=0, le=100, description="Accuracy and detail of technical claims."),
    ]
    clarity: Annotated[
        float,
        Field(..., ge=0, le=100, description="Structure and communication quality."),
    ]


# ---------------------------------------------------------------------------
# Module 4 — feedback report
# ---------------------------------------------------------------------------


class WeakPoint(BaseResponse):
    """Single per-turn improvement item surfaced in the feedback report."""

    turn_index: Annotated[int, Field(...)]
    issue: Annotated[
        str, Field(..., description="Short sentence describing what went wrong.")
    ]
    suggested_answer: Annotated[
        str,
        Field(..., description="Constructive, model-level phrasing of a better answer."),
    ]


class SectionScores(BaseResponse):
    """Aggregated per-section 0..100 scores."""

    confidence_and_tone: Annotated[float, Field(..., ge=0, le=100)]
    fluency: Annotated[float, Field(..., ge=0, le=100)]
    technical_accuracy: Annotated[float, Field(..., ge=0, le=100)]
    relevance: Annotated[float, Field(..., ge=0, le=100)]


class FeedbackReport(BaseResponse):
    """Complete post-session report returned by /finalize and /report."""

    overall_readiness: Annotated[
        float,
        Field(..., ge=0, le=100, description="Single 0..100 readiness score."),
    ]
    section_scores: Annotated[SectionScores, Field(...)]
    narrative_summary: Annotated[
        str,
        Field(..., description="Short (3-5 sentence) qualitative summary."),
    ]
    weak_points: Annotated[
        list[WeakPoint],
        Field(..., description="2-3 weakest turns with suggested improvements."),
    ]
    competency_gaps: Annotated[
        list[str],
        Field(..., description="Core competencies never adequately demonstrated."),
    ]
    per_turn_scores: Annotated[
        dict[str, RubricScores],
        Field(
            ...,
            description="Map of stringified turn_index -> rubric score breakdown.",
        ),
    ]
    resume_gap_flags: Annotated[
        list[ResumeGapFlag],
        Field(
            default_factory=list,
            description=(
                "Constructive list of resume claims that the candidate wasn't "
                "quite ready to go deep on during the live interview.  Empty "
                "when every stated claim is adequately substantiated."
            ),
        ),
    ]
    generated_at: Annotated[
        datetime, Field(..., description="UTC timestamp of generation.")
    ]


# ---------------------------------------------------------------------------
# Module 2 (optional) — Tavus PAL conversation creation
# ---------------------------------------------------------------------------


class TavusConversationCreate(BaseResponse):
    """Internal payload we send to Tavus POST /v2/conversations.

    Most fields are populated from :class:`Settings`; the variable parts are
    ``conversational_context`` (inline string) OR ``document_tags`` (RAG
    fallback when the inline string exceeds ~2500 chars), plus the
    interview-specific ``callback_url`` and ``metadata`` blob.
    """

    pal_id: Annotated[str | None, Field(default=None)]
    persona_id: Annotated[str | None, Field(default=None)]
    replica_id: Annotated[str | None, Field(default=None)]
    face_id: Annotated[str | None, Field(default=None)]
    custom_greeting: Annotated[str | None, Field(default=None)]
    conversational_context: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Inline per-candidate context string (~≤2500 chars).  Exactly "
                "one of conversational_context / document_tags must be set."
            ),
        ),
    ]
    document_tags: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "RAG document tags uploaded via POST /v2/documents.  Used "
                "when the Role + Candidate Context Matrix is too large to "
                "fit inline."
            ),
        ),
    ]
    callback_url: Annotated[
        str,
        Field(
            ...,
            description="Webhook URL Tavus POSTs conversation events to.",
        ),
    ]
    metadata: Annotated[
        dict[str, Any],
        Field(
            default_factory=dict,
            description="Interview_id + any other structured metadata echoed back on webhooks.",
        ),
    ]


class TavusConversationResponse(BaseResponse):
    """Minimal shape we expect back from Tavus POST /v2/conversations."""

    conversation_id: Annotated[str, Field(...)]
    room_url: Annotated[
        str | None,
        Field(default=None, description="Candidate-facing join URL when provided."),
    ]
    conversation_url: Annotated[
        str | None,
        Field(default=None, description="Tavus video streaming URL for candidate iframe embedding."),
    ]
    status: Annotated[
        str | None,
        Field(default="active", description="Tavus conversation status."),
    ]


class FinalizeResponse(BaseResponse):
    """Response shape for POST /api/v1/interviews/{interview_id}/finalize."""

    interview_id: Annotated[str, Field(...)]
    report: Annotated[FeedbackReport, Field(...)]


# ---------------------------------------------------------------------------
# Misc — session create / retrieval
# ---------------------------------------------------------------------------


class InterviewSessionSummary(BaseResponse):
    """Lightweight representation returned by GET /api/v1/interviews/{interview_id}."""

    interview_id: Annotated[str, Field(...)]
    status: Annotated[
        Literal["in_progress", "reconnecting", "finalized", "abandoned"], Field(...)
    ]
    turn_count: Annotated[int, Field(..., ge=0)]
    difficulty_current: Annotated[DifficultyLevel, Field(...)]
    competencies_probed: Annotated[list[str], Field(...)]
    competencies_pending: Annotated[list[str], Field(...)]
    started_at: Annotated[datetime, Field(...)]
    ended_at: Annotated[datetime | None, Field(default=None)]
