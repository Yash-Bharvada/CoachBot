"""Async Groq LLM + Whisper STT client.

Wraps Groq's OpenAI-compatible inference endpoint so that service code can
call a typed :func:`chat_completion` / :func:`transcribe_audio` instead of
sprinkling raw ``httpx`` calls across the codebase.  Every call is wrapped
in a timeout + single retry with exponential backoff via :mod:`tenacity`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import VoicePipelineError

log = get_logger(__name__)


class GroqClient:
    """Thin typed wrapper around the Groq JSON inference API."""

    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._http = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={
                "Authorization": f"Bearer {self._settings.groq_api_key or ''}",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Chat completions — used for JD parsing, interviewer turns, scoring
    # ------------------------------------------------------------------
    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    )
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: dict[str, str] | None = None,
        model: str | None = None,
        stream: bool = False,
    ) -> str | Any:
        """Request a chat completion and return the string content (or the raw stream).

        When *stream* is True the method returns the ``httpx.Response`` so the
        caller can iterate SSE tokens itself; the caller is responsible for
        closing it.  Otherwise the function returns a plain string and raises
        :class:`VoicePipelineError` on malformed payloads.
        """
        api_key = self._settings.groq_api_key
        if not api_key:
            raise VoicePipelineError(
                "Groq API key is not configured (GROQ_API_KEY).",
                details={"stage": "llm"},
            )
        model = model or self._settings.groq_model
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if response_format is not None:
            body["response_format"] = response_format

        if stream:
            return await self._http.post(
                "/chat/completions", json=body, stream=True
            )

        resp = await self._http.post("/chat/completions", json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error(
                "groq.llm.failed",
                status=exc.response.status_code,
                snippet=exc.response.text[:200],
            )
            raise VoicePipelineError(
                f"Groq LLM returned HTTP {exc.response.status_code}.",
                details={"stage": "llm", "model": model},
            ) from exc
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # pragma: no cover - defensive
            raise VoicePipelineError(
                "Groq LLM returned an unexpected payload shape.",
                details={"stage": "llm", "raw_keys": list(data.keys())},
            ) from exc

    async def chat_completion_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 1500,
    ) -> dict[str, Any]:
        """Strongly-typed helper that requests JSON and parses it for you."""
        raw = await self.chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        if not isinstance(raw, str):
            raise VoicePipelineError(
                "chat_completion_json called with streaming enabled.",
                details={"stage": "llm"},
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("groq.llm.not_json", snippet=raw[:200])
            raise VoicePipelineError(
                "Groq LLM produced invalid JSON output.",
                details={"stage": "llm", "snippet": raw[:300]},
            ) from exc

    # ------------------------------------------------------------------
    # Whisper speech-to-text
    # ------------------------------------------------------------------
    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.5, max=3),
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    )
    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.webm",
        language: str | None = None,
    ) -> str:
        """Transcribe raw audio bytes to a plain text transcript."""
        api_key = self._settings.groq_api_key
        if not api_key:
            raise VoicePipelineError(
                "Groq API key is not configured — cannot transcribe audio.",
                details={"stage": "stt"},
            )
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        data = {"model": self._settings.groq_whisper_model}
        if language is not None:
            data["language"] = language
        resp = await self._http.post(
            "/audio/transcriptions",
            data=data,
            files=files,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error(
                "groq.stt.failed",
                status=exc.response.status_code,
                snippet=exc.response.text[:200],
            )
            raise VoicePipelineError(
                f"Groq Whisper returned HTTP {exc.response.status_code}.",
                details={"stage": "stt"},
            ) from exc
        payload = resp.json()
        text = payload.get("text", "")
        if not isinstance(text, str):
            raise VoicePipelineError(
                "Groq Whisper returned an unexpected payload shape.",
                details={"stage": "stt"},
            )
        return text


# Shared module-level singleton — created lazily on first import so that
# tests can monkey-patch settings before the client is instantiated.
_groq_client: GroqClient | None = None


async def get_groq_client() -> GroqClient:
    """Return the shared :class:`GroqClient`, creating it on first use."""
    global _groq_client
    if _groq_client is None:
        _groq_client = GroqClient()
    return _groq_client


async def close_groq_client() -> None:
    """Idempotent helper used by the shutdown hook (and tests)."""
    global _groq_client
    if _groq_client is not None:
        await _groq_client.aclose()
        _groq_client = None
