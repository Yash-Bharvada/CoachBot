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
    """Thin httpx wrapper with automatic backup key failover on quota/credits exhausted."""

    def __init__(self) -> None:
        settings = get_settings()
        self._http = httpx.AsyncClient(
            base_url=settings.tavus_base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        self._settings = settings

    async def aclose(self) -> None:
        await self._http.aclose()

    def _get_headers(self, api_key: str | None = None) -> dict[str, str]:
        key = api_key or self._settings.tavus_api_key or ""
        return {
            "x-api-key": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    async def post(self, path: str, json_body: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
        settings = self._settings
        primary_key = api_key or settings.tavus_api_key or ""
        if not primary_key and not settings.tavus_backup_api_key:
            raise SessionStateError(
                "Tavus API key is not configured (TAVUS_API_KEY).",
                details={"stage": "tavus_auth"},
            )
        headers = self._get_headers(primary_key)
        resp = await self._http.post(path, json=json_body, headers=headers)
        
        # If primary key hits credit limits (402), auth errors (401), or rate limits (429), attempt backup key
        if (
            resp.status_code in (401, 402, 429)
            or "out of conversational credits" in resp.text.lower()
            or "credit" in resp.text.lower()
        ) and settings.tavus_backup_api_key and primary_key != settings.tavus_backup_api_key:
            log.warning(
                "tavus.primary_limit_hit.switching_to_backup_key",
                status=resp.status_code,
                snippet=resp.text[:300],
            )
            backup_headers = self._get_headers(settings.tavus_backup_api_key)
            resp = await self._http.post(path, json=json_body, headers=backup_headers)

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

    async def get(self, path: str, api_key: str | None = None) -> dict[str, Any]:
        settings = self._settings
        primary_key = api_key or settings.tavus_api_key or ""
        headers = self._get_headers(primary_key)
        resp = await self._http.get(path, headers=headers)
        if resp.status_code in (401, 402, 429) and settings.tavus_backup_api_key and primary_key != settings.tavus_backup_api_key:
            backup_headers = self._get_headers(settings.tavus_backup_api_key)
            resp = await self._http.get(path, headers=backup_headers)
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
    """Upload the oversized context as a Tavus Document; return its tags."""
    tag = f"{_DOC_TAG_PREFIX}{interview_id}"
    body = {
        "document_name": f"context_{interview_id}",
        "document_tags": [tag],
        "content": context_text,
    }
    client = await _get_client()
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
        for key in [settings.tavus_api_key, settings.tavus_backup_api_key]:
            if not key:
                continue
            headers = {
                "x-api-key": key,
                "Authorization": f"Bearer {key}",
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
    """Create a Tavus PAL conversation scoped to a specific interview_id with automatic backup failover."""
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

    job_title = str(role_ctx.get("job_title", "target role"))
    company = role_ctx.get("company_name")
    custom_greeting = (
        f"Hello! Welcome to your interview practice session for the {job_title} role"
        + (f" at {company}" if company else "")
        + ". I have reviewed your resume and job description. Let's begin!"
    )

    client = await _get_client()

    # List of PAL + API Key candidates (Primary first, Backup failover second)
    candidates: list[dict[str, str | None]] = [
        {"pal_id": settings.tavus_pal_id, "api_key": settings.tavus_api_key, "label": "primary"},
    ]
    if settings.tavus_backup_pal_id or settings.tavus_backup_api_key:
        candidates.append({
            "pal_id": settings.tavus_backup_pal_id or settings.tavus_pal_id,
            "api_key": settings.tavus_backup_api_key or settings.tavus_api_key,
            "label": "backup",
        })

    result = None
    used_candidate = candidates[0]
    last_error: Exception | None = None

    for cand in candidates:
        active_pal = cand["pal_id"]
        active_key = cand["api_key"]
        extra_metadata: dict[str, Any] = {"interview_id": interview_id}
        if settings.tavus_workspace_id:
            extra_metadata["workspace_id"] = settings.tavus_workspace_id
        if active_pal:
            extra_metadata["pal_id"] = active_pal

        # Decide inline string vs RAG document
        if len(context_text) <= _INLINE_CONTEXT_CHAR_LIMIT:
            payload = TavusConversationCreate(
                pal_id=active_pal,
                persona_id=None if active_pal else persona_id,
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
                pal_id=active_pal,
                persona_id=None if active_pal else persona_id,
                face_id=face_id,
                custom_greeting=custom_greeting,
                conversational_context=None,
                document_tags=tags,
                callback_url=callback_url,
                metadata=extra_metadata,
            )

        body: dict[str, Any] = payload.model_dump(mode="json", exclude_none=True, exclude={"metadata"})

        for attempt in range(2):
            try:
                result = await client.post("/conversations", body, api_key=active_key)
                used_candidate = cand
                break
            except Exception as exc:
                last_error = exc
                snippet = ""
                if isinstance(exc, SessionStateError) and exc.details:
                    snippet = str(exc.details.get("snippet", "")).lower()
                
                if "maximum concurrent conversations" in snippet and attempt < 1:
                    log.info("tavus.concurrency_limit.cleaning_up", candidate=cand["label"])
                    await _cleanup_active_conversations(client)
                    await asyncio.sleep(3.0)
                else:
                    log.warning(
                        "tavus.candidate_failed.trying_next",
                        candidate=cand["label"],
                        pal_id=active_pal,
                        error=str(exc),
                    )
                    break
        
        if result:
            break

    if not result:
        raise SessionStateError(
            "Failed to create Tavus video conversation on both primary and backup credentials.",
            details={"last_error": str(last_error)},
        )

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
        pal_used=used_candidate["pal_id"],
        tier=used_candidate["label"],
    )

    conv_url = result.get("conversation_url") or result.get("room_url") or f"https://tavusapi.com/c/{conv_id}"
    conv_status = result.get("status", "active")
    await db.interview_sessions.update_one(
        {"interview_id": interview_id},
        {
            "$set": {
                "tavus_conversation_id": conv_id,
                "tavus_pal_id": used_candidate["pal_id"],
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


async def end_tavus_conversation(tavus_conversation_id: str) -> None:
    """Explicitly end a Tavus conversation to flush transcripts and free resources."""
    if not tavus_conversation_id:
        return
    try:
        client = await _get_client()
        settings = get_settings()
        headers = {
            "x-api-key": settings.tavus_api_key or "",
            "Authorization": f"Bearer {settings.tavus_api_key or ''}",
        }
        await client._http.post(
            f"/conversations/{tavus_conversation_id}/end",
            headers=headers,
            json={},
        )
        log.info("tavus.conversation.ended", conversation_id=tavus_conversation_id)
    except Exception as exc:
        log.warning("tavus.end_conversation.failed", conversation_id=tavus_conversation_id, error=str(exc))


async def sync_tavus_transcript(
    interview_id: str,
    tavus_conversation_id: str,
    db: Any,
) -> None:
    """Query Tavus API GET /conversations/{id} and sync server-side transcript items into MongoDB."""
    if not tavus_conversation_id:
        return
    try:
        client = await _get_client()
        res = await client.get(f"/conversations/{tavus_conversation_id}")
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        
        # Check transcript in various possible fields in Tavus API responses
        raw_transcript = (
            data.get("transcript")
            or data.get("messages")
            or data.get("events")
            or res.get("transcript")
            or res.get("messages")
            or []
        )

        import time
        formatted_turns: list[dict[str, Any]] = []

        if isinstance(raw_transcript, str) and raw_transcript.strip():
            # If transcript is a single multi-line string
            lines = raw_transcript.strip().split("\n")
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue
                if ":" in line_str:
                    speaker_part, text_part = line_str.split(":", 1)
                    speaker_clean = speaker_part.strip().lower()
                    speaker = (
                        "Interviewer"
                        if any(r in speaker_clean for r in ["interviewer", "bot", "replica", "persona", "pal", "assistant", "ai"])
                        else "You"
                    )
                    formatted_turns.append({
                        "speaker": speaker,
                        "text": text_part.strip(),
                        "timestamp": time.time(),
                    })
                else:
                    formatted_turns.append({
                        "speaker": "Interviewer",
                        "text": line_str,
                        "timestamp": time.time(),
                    })

        elif isinstance(raw_transcript, list) and raw_transcript:
            for item in raw_transcript:
                if isinstance(item, dict):
                    msg = (
                        item.get("message")
                        or item.get("text")
                        or item.get("content")
                        or (item.get("data", {}).get("text") if isinstance(item.get("data"), dict) else None)
                    )
                    role = str(
                        item.get("role")
                        or item.get("speaker")
                        or (item.get("data", {}).get("speaker") if isinstance(item.get("data"), dict) else "")
                        or "interviewer"
                    ).lower()
                    speaker = (
                        "Interviewer"
                        if any(r in role for r in ["interviewer", "bot", "replica", "persona", "pal", "assistant", "ai"])
                        else "You"
                    )
                    if msg and str(msg).strip():
                        formatted_turns.append({
                            "speaker": speaker,
                            "text": str(msg).strip(),
                            "timestamp": time.time(),
                        })

        if formatted_turns:
            session_doc = await db.interview_sessions.find_one({"interview_id": interview_id})
            existing = session_doc.get("transcript_history", []) if session_doc else []
            
            # If formatted turns has interviewer turns or is more complete, update MongoDB
            has_interviewer_turns = any(t.get("speaker") == "Interviewer" for t in formatted_turns)
            if len(formatted_turns) >= len(existing) or has_interviewer_turns:
                await db.interview_sessions.update_one(
                    {"interview_id": interview_id},
                    {
                        "$set": {
                            "transcript_history": formatted_turns,
                            "turn_count": len(formatted_turns),
                            "updated_at": time.time(),
                        }
                    },
                    upsert=True,
                )
                log.info(
                    "tavus.transcript.synced",
                    interview_id=interview_id,
                    turns_synced=len(formatted_turns),
                )
    except Exception as exc:
        log.warning("tavus.transcript.sync_failed", interview_id=interview_id, error=str(exc))


