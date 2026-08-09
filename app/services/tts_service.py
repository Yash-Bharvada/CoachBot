"""Text-to-speech synthesis with chunked, async-iterable output.

Default provider is :mod:`edge_tts` because it is free, works without API
keys, and exposes an async stream already.  If the operator sets
``TTS_PROVIDER=elevenlabs`` or ``huggingface`` the service routes to those
instead; both require a configured API key.

Every public surface returns an *async generator* of ``bytes`` chunks so
the websocket handler can forward audio as it arrives instead of waiting
for a single rendered WAV/MP3 blob.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

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

Provider = Literal["edge-tts", "elevenlabs", "huggingface"]

# Edge-TTS voice — neutral, professional female US English with low variance.
EDGE_VOICE = "en-US-AriaNeural"
EDGE_RATE = "+0%"   # "natural pacing" — see persona requirement
EDGE_VOLUME = "+0%"


class TTSService:
    """Async TTS wrapper supporting multiple providers."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def provider(self) -> Provider:
        return self._settings.tts_provider  # type: ignore[return-value]

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield MP3 audio chunks for *text* as they become available.

        The default chunk size is driven by the provider; the websocket
        layer forwards whatever arrives so backpressure propagates to the
        source of synthesis.
        """
        if not text.strip():
            return
        provider: Provider = self.provider
        if provider == "edge-tts":
            async for chunk in self._edge_tts_stream(text):
                yield chunk
        elif provider == "elevenlabs":
            async for chunk in self._elevenlabs_stream(text, voice_id=voice_id):
                yield chunk
        else:  # huggingface
            async for chunk in self._huggingface_stream(text):
                yield chunk

    # ------------------------------------------------------------------
    # Edge-TTS (default)
    # ------------------------------------------------------------------
    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.3, max=3),
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    )
    async def _edge_tts_stream(self, text: str) -> AsyncIterator[bytes]:
        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - env guard
            raise VoicePipelineError(
                "edge-tts is not installed — run `pip install edge-tts`.",
                details={"stage": "tts", "provider": "edge-tts"},
            ) from exc
        communicate = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE, volume=EDGE_VOLUME)
        try:
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    data = chunk.get("data")
                    if isinstance(data, bytes):
                        yield data
        except Exception as exc:  # noqa: BLE001
            log.warning("tts.edge.failed", error=str(exc))
            raise VoicePipelineError(
                "Edge-TTS synthesis failed.",
                details={"stage": "tts", "provider": "edge-tts"},
            ) from exc

    # ------------------------------------------------------------------
    # ElevenLabs
    # ------------------------------------------------------------------
    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.3, max=3),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def _elevenlabs_stream(
        self, text: str, *, voice_id: str | None
    ) -> AsyncIterator[bytes]:
        key = self._settings.elevenlabs_api_key
        if not key:
            raise VoicePipelineError(
                "ELEVENLABS_API_KEY is not configured.",
                details={"stage": "tts", "provider": "elevenlabs"},
            )
        vid = voice_id or self._settings.elevenlabs_voice_id
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
            "?output_format=mp3_44100_128"
        )
        try:
            resp = await self._http.post(
                url,
                headers={
                    "xi-api-key": key,
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {
                        "stability": 0.55,
                        "similarity_boost": 0.75,
                    },
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise VoicePipelineError(
                "ElevenLabs returned an error.",
                details={"stage": "tts", "provider": "elevenlabs"},
            ) from exc
        async for chunk in resp.aiter_bytes(chunk_size=8192):
            if chunk:
                yield chunk

    # ------------------------------------------------------------------
    # HuggingFace Spaces fallback
    # ------------------------------------------------------------------
    async def _huggingface_stream(self, text: str) -> AsyncIterator[bytes]:
        """Minimal HF fallback — blocks into one blob because the inference
        API is stateless.  For true streaming, prefer ElevenLabs/Edge."""
        key = self._settings.huggingface_api_key
        if not key:
            raise VoicePipelineError(
                "HUGGINGFACE_API_KEY is not configured.",
                details={"stage": "tts", "provider": "huggingface"},
            )
        try:
            resp = await self._http.post(
                "https://api-inference.huggingface.co/models/facebook/fastspeech2-en-ljspeech",
                headers={"Authorization": f"Bearer {key}"},
                content=text.encode("utf-8"),
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise VoicePipelineError(
                "HuggingFace TTS returned an error.",
                details={"stage": "tts", "provider": "huggingface"},
            ) from exc
        yield resp.content


_singleton: TTSService | None = None


async def get_tts_service() -> TTSService:
    global _singleton
    if _singleton is None:
        _singleton = TTSService()
    return _singleton


async def close_tts_service() -> None:
    global _singleton
    if _singleton is not None:
        await _singleton.aclose()
        _singleton = None
