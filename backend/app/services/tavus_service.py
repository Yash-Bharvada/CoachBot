"""Module 2b — Tavus PAL integration: conversation creation + RAG documents.

The single public surface is :func:`create_tavus_conversation` which:
  * Reads the persisted Role & Candidate Context Matrix from Mongo.
  * Formats it into a high-signal ``conversational_context`` string via
    :func:`build_conversational_context`.
  * If the string exceeds ~2500 chars, instead uploads it as a Tavus Document
    (``POST /v2/documents``) and passes ``document_tags`` on the conversation
    so the PAL retrieves it via RAG at runtime.
  * Calls ``POST /v2/conversations`` with the ``persona_id``/``face_id``/``callback_url``
    from :class:`Settings` and the interview_id embedded in ``metadata``.

The key invariant (CHANGE 2): NEVER put JD/resume details into the shared PAL
``system_prompt``.  Per-candidate context is always transported via
``conversational_context`` inline or via ``document_tags`` RAG.
"""

from __future__ import annotations

from typing import Any

import asyncio
import httpx
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import InterviewNotFoundError, SessionStateError
from app.models.schemas import (
    CandidateProfile,
    TavusConversationCreate,
    TavusConversationResponse,
)

log = get_logger(__name__)

# Practical cap for inline conversational_context strings.  Tavus docs don't
# publish a hard limit; this guard keeps us well inside typical token budgets
# and falls back to RAG Documents when the combined JD + resume matrix is large.
_INLINE_CONTEXT_CHAR_LIMIT = 2500

# One tag per interview, scoped by id so unrelated conversations can't pull
# another candidate's document via RAG leakage.
_DOC_TAG_PREFIX = "interview_ctx_"


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------


def _format_candidate_profile(profile: CandidateProfile) -> str:
    """Compact one-liner style blocks for the inline string."""
    lines: list[str] = []
    if profile.skills:
        lines.append("CANDIDATE SKILLS: " + ", ".join(profile.skills[:12]))
    if profile.past_roles:
        lines.append("CANDIDATE RECENT ROLES: " + "; ".join(profile.past_roles[:3]))
    if profile.notable_projects:
        lines.append(
            "CANDIDATE NOTABLE PROJECTS: " + "; ".join(profile.notable_projects[:3])
        )
    if profile.education:
        lines.append("CANDIDATE EDUCATION: " + "; ".join(profile.education[:3]))
    return "\n".join(lines)


def build_conversational_context(role_ctx: dict[str, Any]) -> str:
    """Format the persisted Role & Candidate Context Matrix into a tight string.

    The output is designed to be embedded as Tavus ``conversational_context``.
    Callers are responsible for measuring its length and falling back to
    :func:`_upload_document` + ``document_tags`` when it exceeds
    :data:`_INLINE_CONTEXT_CHAR_LIMIT`.
    """
    job_title = str(role_ctx.get("job_title", "the role"))
    company = role_ctx.get("company_name")
    header = f"INTERVIEW CONTEXT FOR: {job_title}" + (
        f" at {company}" if company else ""
    )
    competencies = role_ctx.get("core_competencies") or []
    comp_line = "CORE COMPETENCIES (ask questions across these, starting with what the candidate is weakest on per transcript context): " + ", ".join(competencies[:10])
    difficulty = str(role_ctx.get("difficulty_baseline", "medium"))
    diff_line = f"STARTING DIFFICULTY: {difficulty} — adapt based on answer quality per the platform difficulty state machine."
    grounding = str(role_ctx.get("grounding_summary", "")).strip()
    grounding_line = f"GROUNDING NOTES: {grounding}" if grounding else ""
    tech_stack = role_ctx.get("tech_stack") or []
    tech_line = "KEY TECH: " + ", ".join(tech_stack[:10]) if tech_stack else ""
    candidate_raw = role_ctx.get("candidate_profile") or {}
    try:
        profile = CandidateProfile.model_validate(candidate_raw)
    except Exception:  # noqa: BLE001 — legacy docs without the field
        profile = CandidateProfile()
    candidate_block = _format_candidate_profile(profile)
    jd_raw = str(role_ctx.get("job_description", "")).strip()
    jd_line = f"JOB DESCRIPTION SUMMARY: {jd_raw[:500]}" if jd_raw else ""
    sections = [
        header,
        jd_line,
        comp_line,
        diff_line,
        grounding_line,
        tech_line,
        candidate_block,
        (
            "INSTRUCTIONS: Act as a professional interviewer for this role. "
            "Reference the candidate's actual resume claims when asking follow-up questions. "
            "1 question per turn, keep replies <140 words, natural tone. "
            "Ask for concrete examples, never accuse — coach, don't interrogate."
        ),
    ]
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# HTTP client (singleton)
# ---------------------------------------------------------------------------


class _TavusHttpClient:
    """Thin httpx wrapper with tenacity retries on transient HTTP errors."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key = settings.tavus_api_key or ""
        self._headers = {
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._http = httpx.AsyncClient(
            base_url=settings.tavus_base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        self._settings = settings

    async def aclose(self) -> None:
        await self._http.aclose()

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    )
    async def post(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        api_key = settings.tavus_api_key or ""
        if not api_key:
            raise SessionStateError(
                "Tavus API key is not configured (TAVUS_API_KEY).",
                details={"stage": "tavus_auth"},
            )
        headers = {
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        resp = await self._http.post(path, json=json_body, headers=headers)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            snippet = exc.response.text[:400]
            log.error(
                "tavus.http.failed",
                path=path,
                status=exc.response.status_code,
                snippet=snippet,
            )
            if exc.response.status_code == 402 or "out of conversational credits" in snippet.lower():
                raise SessionStateError(
                    "Tavus AI conversational credits are exhausted for this account. Please top up your Tavus credits or switch to Voice Practice Mode.",
                    details={
                        "stage": "tavus_credits",
                        "path": path,
                        "snippet": snippet,
                    },
                ) from exc
            raise SessionStateError(
                f"Tavus API returned HTTP {exc.response.status_code}.",
                details={
                    "stage": "tavus_api",
                    "path": path,
                    "snippet": snippet,
                },
            ) from exc
        return resp.json()

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    )
    async def get(self, path: str) -> dict[str, Any]:
        settings = get_settings()
        api_key = settings.tavus_api_key or ""
        headers = {
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
        }
        resp = await self._http.get(path, headers=headers)
        resp.raise_for_status()
        return resp.json()


_client: _TavusHttpClient | None = None


async def _get_client() -> _TavusHttpClient:
    global _client
    if _client is None:
        _client = _TavusHttpClient()
    return _client


async def close_tavus_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Documents fallback (RAG path)
# ---------------------------------------------------------------------------


async def _upload_document(
    interview_id: str, context_text: str
) -> list[str]:
    """Upload the oversized context as a Tavus Document; return its tags.

    We apply a single per-interview tag so the PAL's RAG retrieval is
    precisely scoped — no cross-candidate leakage at retrieval time.
    """
    tag = f"{_DOC_TAG_PREFIX}{interview_id}"
    body = {
        "document_name": f"Interview Context {interview_id}",
        "content": context_text,
        "tags": [tag],
    }
    client = await _get_client()
    # Tavus's Documents endpoint lives under /v2/documents.
    result = await client.post("/documents", body)
    log.info(
        "tavus.document.uploaded",
        interview_id=interview_id,
        doc_id=result.get("document_id"),
        chars=len(context_text),
    )
    return [tag]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _resolve_persona_and_face(settings) -> tuple[str, str | None]:
    """Resolve persona_id + face_id from settings with fallback to active Tavus persona."""
    explicit_persona = settings.tavus_persona_id
    explicit_face = settings.tavus_face_id

    if explicit_persona and not explicit_persona.startswith("pec"):
        return explicit_persona, explicit_face
    return "p9a315e0", explicit_face


async def _cleanup_active_conversations(client: _TavusHttpClient) -> None:
    """End any existing active conversations on Tavus to free up concurrency slots."""
    try:
        settings = get_settings()
        headers = {
            "x-api-key": settings.tavus_api_key or "",
            "Authorization": f"Bearer {settings.tavus_api_key or ''}",
        }
        resp = await client._http.get("/conversations", headers=headers)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for conv in data:
                if conv.get("status") == "active":
                    cid = conv.get("conversation_id")
                    if cid:
                        await client._http.post(f"/conversations/{cid}/end", headers=headers, json={})
    except Exception as exc:
        log.warning("tavus.cleanup.failed", error=str(exc))


async def create_tavus_conversation(
    interview_id: str,
    db,  # AsyncIOMotorDatabase (intentionally untyped to avoid circular import)
    *,
    callback_url: str,
) -> TavusConversationResponse:
    """Create a Tavus PAL conversation scoped to a specific interview_id.

    *callback_url* is the fully-qualified URL Tavus will POST webhook events
    to (e.g. ``https://app.example.com/api/v1/interviews/tavus-webhook``).
    """
    settings = get_settings()
    persona_id, face_id = _resolve_persona_and_face(settings)

    role_ctx = await db.role_context_matrices.find_one(
        {"interview_id": interview_id},
        {
            "job_title": 1,
            "job_description": 1,
            "company_name": 1,
            "core_competencies": 1,
            "difficulty_baseline": 1,
            "grounding_summary": 1,
            "tech_stack": 1,
            "candidate_profile": 1,
        },
    )
    if role_ctx is None:
        if interview_id in ("demo_session", "demo") or interview_id.startswith("demo"):
            role_ctx = {
                "interview_id": interview_id,
                "job_title": "Software Engineer",
                "company_name": "Demo Practice",
                "core_competencies": ["System Design", "Problem Solving", "Communication"],
                "difficulty_baseline": "medium",
                "grounding_summary": "General software engineering technical interview practice session.",
                "tech_stack": ["Python", "React", "System Architecture"],
            }
        else:
            raise InterviewNotFoundError(interview_id)

    context_text = build_conversational_context(role_ctx)

    extra_metadata: dict[str, Any] = {"interview_id": interview_id}
    if settings.tavus_workspace_id:
        extra_metadata["workspace_id"] = settings.tavus_workspace_id
    if settings.tavus_pal_id:
        extra_metadata["pal_id"] = settings.tavus_pal_id

    job_title = str(role_ctx.get("job_title", "target role"))
    company = role_ctx.get("company_name")
    custom_greeting = (
        f"Hello! Welcome to your interview practice session for the {job_title} role"
        + (f" at {company}" if company else "")
        + ". I have reviewed your resume and job description. Let's begin!"
    )

    pal_id = settings.tavus_pal_id

    # Decide: inline string OR RAG document fallback.
    if len(context_text) <= _INLINE_CONTEXT_CHAR_LIMIT:
        payload = TavusConversationCreate(
            pal_id=pal_id,
            persona_id=None if pal_id else persona_id,
            face_id=face_id,
            custom_greeting=custom_greeting,
            conversational_context=context_text,
            document_tags=None,
            callback_url=callback_url,
            metadata=extra_metadata,
        )
    else:
        tags = await _upload_document(interview_id, context_text)
        payload = TavusConversationCreate(
            pal_id=pal_id,
            persona_id=None if pal_id else persona_id,
            face_id=face_id,
            custom_greeting=custom_greeting,
            conversational_context=None,
            document_tags=tags,
            callback_url=callback_url,
            metadata=extra_metadata,
        )

    client = await _get_client()
    body: dict[str, Any] = payload.model_dump(mode="json", exclude_none=True, exclude={"metadata"})
    
    result = None
    for attempt in range(3):
        try:
            result = await client.post("/conversations", body)
            break
        except SessionStateError as exc:
            snippet = exc.details.get("snippet", "") if exc.details else ""
            if "maximum concurrent conversations" in snippet and attempt < 2:
                log.info("tavus.concurrency_limit.retrying", attempt=attempt + 1)
                await _cleanup_active_conversations(client)
                await asyncio.sleep(4.0 * (attempt + 1))
            else:
                raise

    if not result:
        raise SessionStateError("Failed to create Tavus conversation after retries.")
    conv_id = result.get("conversation_id") or result.get("id")
    if not conv_id:
        raise SessionStateError(
            "Tavus conversation creation returned no conversation_id.",
            details={"stage": "tavus_create", "keys": list(result.keys())},
        )
    log.info(
        "tavus.conversation.created",
        interview_id=interview_id,
        conversation_id=conv_id,
        mode="inline" if payload.conversational_context else "document_rag",
    )
    conv_url = result.get("conversation_url") or result.get("room_url") or f"https://tavusapi.com/c/{conv_id}"
    conv_status = result.get("status", "active")
    await db.interview_sessions.update_one(
        {"interview_id": interview_id},
        {
            "$set": {
                "tavus_conversation_id": conv_id,
                "tavus_room_url": result.get("room_url"),
                "tavus_conversation_url": conv_url,
                "updated_at": __import__("time").time(),
            }
        },
    )
    return TavusConversationResponse(
        conversation_id=str(conv_id),
        room_url=result.get("room_url"),
        conversation_url=conv_url,
        status=conv_status,
    )


async def sync_tavus_transcript(
    interview_id: str,
    tavus_conversation_id: str,
    db: Any,
) -> None:
    """Query Tavus API GET /conversations/{id} and sync server-side transcript items into MongoDB."""
    try:
        client = await _get_client()
        res = await client.get(f"/conversations/{tavus_conversation_id}")
        tavus_turns = res.get("transcript") or res.get("messages") or []
        if isinstance(tavus_turns, list) and tavus_turns:
            import time
            formatted_turns = []
            for item in tavus_turns:
                if isinstance(item, dict):
                    msg = item.get("message") or item.get("text") or item.get("content")
                    role = str(item.get("role") or item.get("speaker") or "interviewer").lower()
                    speaker = "Interviewer" if any(r in role for r in ["interviewer", "bot", "replica", "persona", "pal"]) else "You"
                    if msg:
                        formatted_turns.append({"speaker": speaker, "text": str(msg).strip(), "timestamp": time.time()})
            if formatted_turns:
                session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
                existing = session_doc.get("transcript_history", []) if session_doc else []
                if len(formatted_turns) > len(existing):
                    await db.interview_sessions.update_one(
                        {"interview_id": interview_id},
                        {"$set": {"transcript_history": formatted_turns}},
                    )
    except Exception as exc:
        log.warning("tavus.transcript.sync_failed", interview_id=interview_id, error=str(exc))

