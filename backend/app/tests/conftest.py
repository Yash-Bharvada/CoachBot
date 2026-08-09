"""Shared pytest fixtures used by every test module.

Creates a throwaway ``TestClient`` backed by an in-memory MongoDB-style stub
that still exercises the full FastAPI routing, validation and service glue.
Where the LLM/TTS/STT clients would hit real network APIs we monkey-patch
them via the ``mock_services`` fixture so the suite is deterministic and
needs no API keys.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from structlog import get_logger

from app.core.config import Settings, get_settings
from app.core.database import (
    close_mongo_connection,
    open_mongo_connection,
)
from app.main import create_app
from app.services import llm_client, tts_service, web_grounding_service
from app.services.evaluation_service import _JUDGE_SYSTEM_PROMPT  # noqa: F401
from app.websockets.connection_manager import connection_manager

log = get_logger(__name__)


def _test_settings() -> Settings:
    """Return a Settings object tuned for the local test environment."""
    # For tests we default to a running local MongoDB on the standard port;
    # if none is available the tests that *require* a real DB will be skipped
    # automatically by a small pytest helper below.
    return Settings(
        mongo_uri="mongodb://localhost:27017",
        mongo_db_name="interview_prep_simulator_test",
        rate_limit_per_minute=10_000,
        ws_rate_limit_per_minute=10_000,
        web_grounding_timeout_seconds=2,
        voice_pipeline_timeout_seconds=15,
    )


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Ensure asyncio.EventLoopPolicy is set for the whole test session."""
    return asyncio.WindowsSelectorEventLoopPolicy()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return _test_settings()


@pytest.fixture
def _override_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    """Install the test settings so every module-level `get_settings()` call sees them."""
    get_settings.cache_clear()

    def _cached() -> Settings:
        return test_settings

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: test_settings,
    )
    # Patch all call sites that import get_settings directly to use the same override.
    for mod in (
        "app.core.database",
        "app.core.security",
        "app.routers.stream",
        "app.services.onboarding_service",
        "app.services.resume_parsing_service",
        "app.services.web_grounding_service",
        "app.services.llm_client",
        "app.services.tts_service",
        "app.services.voice_pipeline_service",
        "app.services.evaluation_service",
        "app.services.feedback_service",
        "app.services.tavus_service",
        "app.websockets.connection_manager",
    ):
        try:
            monkeypatch.setattr(f"{mod}.get_settings", _cached)
        except Exception:  # noqa: BLE001
            pass


@pytest_asyncio.fixture
async def _real_mongo_client(
    test_settings: Settings,
) -> AsyncIterator[AsyncIOMotorClient | None]:
    """Try to connect to a real MongoDB; yield None if none is running locally."""
    client: AsyncIOMotorClient | None = None
    try:
        client = AsyncIOMotorClient(
            test_settings.mongo_uri,
            serverSelectionTimeoutMS=1500,
            connectTimeoutMS=1500,
        )
        await client.admin.command("ping")
        yield client
    except Exception:  # noqa: BLE001
        yield None
    finally:
        if client is not None:
            client.close()


# @pytest.fixture
def requires_mongo() -> None:
    """Marker: skip the test if MongoDB is not available locally."""
    # Implemented via a helper below; see `session_db`.
    return None


@pytest_asyncio.fixture
async def session_db(
    test_settings: Settings,
    _override_settings: None,
    _real_mongo_client: AsyncIOMotorClient | None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncIOMotorDatabase]:
    """Provide a test-scoped database (real Mongo when available, skip otherwise)."""
    if _real_mongo_client is None:
        pytest.skip("MongoDB not reachable on localhost:27017")
    db = _real_mongo_client[test_settings.mongo_db_name]
    # Ensure every test starts with a clean slate for the 4 collections.
    for coll in (
        "role_context_matrices",
        "interview_sessions",
        "turn_evaluations",
        "feedback_reports",
    ):
        await db[coll].delete_many({})
    # Re-point the open_mongo_connection helper so it reuses our test client.
    import app.core.database as db_mod

    monkeypatch.setattr(db_mod, "_client", _real_mongo_client)
    yield db
    # Cleanup
    try:
        for coll in (
            "role_context_matrices",
            "interview_sessions",
            "turn_evaluations",
            "feedback_reports",
        ):
            await db[coll].delete_many({})
    except Exception:  # noqa: BLE001
        pass


@pytest_asyncio.fixture
async def mock_services(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Deterministic in-memory replacements for every external-network call site.

    Populates three history lists: ``llm_calls``, ``stt_calls``, ``tts_calls``,
    ``grounding_calls`` so each test can assert against exactly what was sent.
    """
    history: dict[str, list[dict[str, Any]]] = {
        "llm_calls": [],
        "stt_calls": [],
        "tts_calls": [],
        "grounding_calls": [],
        "resume_parse_calls": [],
    }
    import json as _json

    # --- Groq LLM ----------------------------------------------------
    from app.services import llm_client as llm_mod

    async def _fake_chat(messages, *, temperature=0.2, max_tokens=1024, response_format=None, model=None, stream=False):
        call = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        history["llm_calls"].append(call)
        sys_text = (messages[0].get("content", "") if messages else "").lower()
        if response_format and "json" in str(response_format).lower():
            # Judge-style call (rubric) or JD parse or resume or sentiment or weak_point or resume_gap.
            if "evaluate the candidate" in sys_text or "independent judge" in sys_text:
                out = {
                    "scores": {"relevance": 85.0, "technical_depth": 80.0, "clarity": 75.0},
                    "covered_competencies": ["Python", "SQL"],
                    "short_rationale": "Good answer with solid example.",
                }
            elif "sentiment" in sys_text or "speech coach" in sys_text:
                out = {
                    "confidence_score": 78,
                    "tone_score": 82,
                    "pacing_comment": "Slightly rushed but clear.",
                    "sentiment_summary": "Constructive, calm delivery.",
                }
            elif "resume claims" in sys_text or "career coach" in sys_text:
                # CHANGE 3 — resume gap judge: return an empty flags list by default.
                # Tests that want gaps can monkeypatch this fixture inside their test.
                out = {"flags": []}
            elif "weak point" in sys_text or "coaching" in sys_text or "suggested_answer" in sys_text:
                out = {
                    "issue": "Answer was missing a concrete example of the trade-off.",
                    "suggested_answer": (
                        "A strong answer would walk through the two primary "
                        "alternatives — approach A and B — compare their latency, "
                        "then anchor the final choice to the user's SLA."
                    ),
                }
            elif "recruiter parsing a candidate resume" in sys_text:
                # CHANGE 1 — resume candidate profile parser.
                out = {
                    "skills": ["Python", "FastAPI", "PostgreSQL", "Async I/O", "Docker", "System Design"],
                    "past_roles": [
                        "Acme Corp — Senior Backend Engineer, 2022–present",
                        "Beta Inc — Backend Engineer, 2019–2022",
                    ],
                    "notable_projects": [
                        "Led migration of monolith to microservices (40% latency reduction)",
                        "Built async event pipeline processing 1M events/day",
                    ],
                    "education": ["B.Sc. Computer Science, State University, 2019"],
                }
            else:
                # JD parse call.
                out = {
                    "core_competencies": [
                        "Python", "Async I/O", "PostgreSQL", "System Design",
                        "CI/CD", "REST APIs",
                    ],
                    "difficulty_baseline": "medium",
                    "tech_stack": ["Python", "FastAPI", "PostgreSQL"],
                    "seniority_indicators": ["senior", "5+ years"],
                }
            return _json.dumps(out)
        # Non-JSON: interviewer reply.
        return "Good start. Can you walk me through a time you designed an async service?"

    async def _fake_chat_json(messages, *, temperature=0.1, max_tokens=1500):
        return _json.loads(
            await _fake_chat(messages, temperature=temperature, max_tokens=max_tokens, response_format={"type": "json_object"})
        )

    monkeypatch.setattr(llm_mod.GroqClient, "chat_completion", staticmethod(_fake_chat))
    monkeypatch.setattr(llm_mod.GroqClient, "chat_completion_json", staticmethod(_fake_chat_json))

    async def _fake_transcribe(audio_bytes, *, filename="a.webm", language=None):
        history["stt_calls"].append({"bytes": len(audio_bytes), "filename": filename})
        return "I'd use asyncio.gather to run the two tasks concurrently and then aggregate their results."

    monkeypatch.setattr(llm_mod.GroqClient, "transcribe_audio", staticmethod(_fake_transcribe))

    # --- TTS ---------------------------------------------------------
    from app.services import tts_service as tts_mod

    async def _fake_synth_stream(self, text, *, voice_id=None):  # noqa: D401
        history["tts_calls"].append({"text": text[:80]})
        # Yield two small fake MP3 frames followed by a sentinel.
        yield b"\xff\xe3\x18\xc4"
        yield b"\x00" * 128
        yield b""  # triggers is_final in the wrapper

    monkeypatch.setattr(tts_mod.TTSService, "synthesize_stream", _fake_synth_stream)

    # --- Web grounding ----------------------------------------------
    from app.services import web_grounding_service as ground_mod

    async def _fake_research(self, job_title, company_name):
        history["grounding_calls"].append({"job_title": job_title, "company_name": company_name})
        return ground_mod.GroundingResult(
            hits=[
                ground_mod.GroundingSearchHit(
                    title=f"{job_title} Interview Guide",
                    url="https://example.com/guide",
                    snippet="Expect deep dives into async patterns, caching and SQL indexing.",
                ),
            ],
            provider="duckduckgo",
            query=job_title,
            latency_ms=0,
        )

    monkeypatch.setattr(ground_mod.WebGroundingService, "research_role", _fake_research)

    # --- CHANGE 1 — Resume parsing (file extraction + candidate profile)
    # We bypass the raw PDF/DOCX extraction path entirely (no real parsers in
    # tests) and instead stub the single public entry that the onboarding
    # service calls: parse_candidate_profile → returns a deterministic profile.
    from app.services import resume_parsing_service as resume_mod
    from app.models.schemas import CandidateProfile

    async def _fake_parse_candidate_profile(upload):
        history["resume_parse_calls"].append(
            {"filename": upload.filename, "content_type": upload.content_type}
        )
        return CandidateProfile(
            skills=["Python", "FastAPI", "PostgreSQL", "Async I/O", "Docker", "System Design"],
            past_roles=[
                "Acme Corp — Senior Backend Engineer, 2022–present",
                "Beta Inc — Backend Engineer, 2019–2022",
            ],
            notable_projects=[
                "Led migration of monolith to microservices (40% latency reduction)",
                "Built async event pipeline processing 1M events/day",
            ],
            education=["B.Sc. Computer Science, State University, 2019"],
        )

    monkeypatch.setattr(resume_mod, "parse_candidate_profile", _fake_parse_candidate_profile)

    yield history


@pytest_asyncio.fixture
async def app_with_mocks(
    mock_services: dict[str, list[dict[str, Any]]],
    session_db: AsyncIOMotorDatabase,
) -> AsyncIterator[FastAPI]:
    """Yield a fully-wired FastAPI app with all external I/O stubbed out."""
    app = create_app()
    async with LifespanManager(app):
        yield app
    # Tear down the connection manager so housekeeping tasks stop between tests.
    await connection_manager.stop()
    try:
        await close_mongo_connection()
    except Exception:  # noqa: BLE001
        pass


@pytest_asyncio.fixture
async def api_client(
    app_with_mocks: FastAPI,
) -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient wired directly into the test app."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mocks),
        base_url="http://testserver",
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Sample interview fixture: creates a seeded interview so tests can skip the
# JD-analyze HTTP call and focus on the flow they're testing.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_interview(
    api_client: AsyncClient,
    session_db: AsyncIOMotorDatabase,
) -> str:
    """Return an interview_id whose role context matrix + session are seeded."""
    payload = {
        "job_title": "Senior Backend Engineer",
        "job_description": (
            "We are looking for a Senior Backend Engineer with 5+ years of "
            "Python experience, deep knowledge of async I/O, PostgreSQL "
            "tuning and designing RESTful APIs at scale.  You will lead "
            "design reviews, mentor mid-level engineers, and own services "
            "from planning through production.  Familiarity with FastAPI, "
            "Docker, and CI/CD pipelines a plus."
        ),
        "company_name": "Acme Cloud",
    }
    resp = await api_client.post("/api/v1/interviews/analyze-jd", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    interview_id = body["interview_id"]
    assert len(interview_id) > 0
    return interview_id
