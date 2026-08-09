"""Application configuration loaded from environment variables.

All tunables are centralized here via pydantic-settings so that no module
reads ``os.environ`` directly.  Defaults are chosen for a local development
environment; production deployments override via .env or the shell.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed container for every configuration value used in the project."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- MongoDB -----------------------------------------------------------
    mongo_uri: str = Field(
        default="mongodb://localhost:27017",
        description="MongoDB connection URI used by the async motor driver.",
    )
    mongo_db_name: str = Field(
        default="interview_prep_simulator",
        description="Name of the logical database that stores interview data.",
    )

    # --- Groq --------------------------------------------------------------
    groq_api_key: str | None = Field(
        default=None,
        description="API key for the Groq Cloud inference service.",
    )
    groq_model: str = Field(
        default="llama-3.1-70b-versatile",
        description="Default LLM used for interview questions and scoring.",
    )
    groq_whisper_model: str = Field(
        default="whisper-large-v3",
        description="Whisper variant used by Groq for speech-to-text.",
    )

    # --- TTS ---------------------------------------------------------------
    tts_provider: Literal["edge-tts", "elevenlabs", "huggingface"] = Field(
        default="edge-tts",
        description="Which backend synthesises voice audio.",
    )
    elevenlabs_api_key: str | None = Field(
        default=None,
        description="ElevenLabs API key (required when tts_provider=elevenlabs).",
    )
    elevenlabs_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description="ElevenLabs voice identifier for the interviewer.",
    )

    # --- Web grounding -----------------------------------------------------
    web_grounding_provider: Literal["duckduckgo", "tavily", "huggingface"] = (
        Field(
            default="duckduckgo",
            description="Search provider that pulls live company/role info.",
        )
    )
    tavily_api_key: str | None = Field(
        default=None,
        description="Tavily API key (required when web_grounding_provider=tavily).",
    )

    # --- Tavus (video interviewer, optional) -------------------------------
    tavus_api_key: str | None = Field(
        default=None,
        description="Tavus API key for AI video interview PAL conversations.",
    )
    tavus_base_url: str = Field(
        default="https://tavusapi.com/v2",
        description="Tavus REST API base URL.",
    )
    tavus_persona_id: str | None = Field(
        default=None,
        description="Default Tavus persona_id used when creating conversations.",
    )
    tavus_face_id: str | None = Field(
        default=None,
        description="Default Tavus face_id used when creating conversations.",
    )
    tavus_webhook_secret: str | None = Field(
        default=None,
        description="Shared secret used to verify Tavus webhook payload signatures.",
    )

    # --- Resume parsing ----------------------------------------------------
    max_resume_size_mb: int = Field(
        default=10,
        description="Maximum allowed resume upload size in megabytes.",
    )
    allowed_resume_extensions: list[str] = Field(
        default_factory=lambda: ["pdf", "docx"],
        description="Case-insensitive list of allowed resume file extensions.",
    )

    # --- HuggingFace (optional fallback) -----------------------------------
    huggingface_api_key: str | None = Field(
        default=None,
        description="Optional key for HuggingFace Space based TTS/STT/analysis.",
    )

    # --- Server ------------------------------------------------------------
    host: str = Field(default="0.0.0.0", description="Bind address for uvicorn.")
    port: int = Field(default=8000, description="Listening port for uvicorn.")
    log_level: str = Field(default="info", description="structlog/stdlib log level.")

    # --- Resilience --------------------------------------------------------
    web_grounding_timeout_seconds: float = Field(
        default=8.0,
        description="Hard timeout (s) before the JD analysis degrades gracefully.",
    )
    voice_pipeline_timeout_seconds: float = Field(
        default=30.0,
        description="Per-turn timeout for STT→LLM→TTS orchestration.",
    )
    websocket_grace_period_seconds: float = Field(
        default=60.0,
        description="How long session state survives a dropped connection.",
    )
    websocket_idle_timeout_seconds: float = Field(
        default=300.0,
        description="Idle seconds before a connected websocket is closed.",
    )
    max_audio_buffer_mb: int = Field(
        default=64,
        description="Upper bound (MB) on buffered audio per websocket.",
    )

    # --- Rate limiting -----------------------------------------------------
    rate_limit_per_minute: int = Field(
        default=60,
        description="Maximum POST requests per minute per client IP.",
    )
    ws_rate_limit_per_minute: int = Field(
        default=120,
        description="Maximum websocket handshake rate per minute per client IP.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton cached settings object.

    Using ``lru_cache`` guarantees we only parse env/the .env file once
    per process, which is both faster and prevents side effects when tests
    monkey-patch individual values.
    """
    return Settings()
