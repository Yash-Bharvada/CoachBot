"""Module 1 tests — /analyze-jd endpoint behaviour.

Covers:
  * Happy path: valid JD → 201 with interview_id + competencies + grounding.
  * Rejects placeholder JD text (lorem ipsum / generic filler).
  * Rejects too-short JD content.
  * Missing required fields → 422.
  * Grounding timeout degrades gracefully rather than failing the request.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from app.services import web_grounding_service as ground_mod


pytestmark = pytest.mark.asyncio


async def test_analyze_jd_happy_path(
    api_client: AsyncClient,
) -> None:
    payload = {
        "job_title": "Senior Backend Engineer",
        "job_description": (
            "Looking for a Senior Backend Engineer with 5+ years Python, "
            "async I/O, PostgreSQL, and distributed systems. You will own "
            "service design, lead code reviews, and mentor engineers."
        ),
        "company_name": "Acme Cloud",
    }
    resp = await api_client.post("/api/v1/interviews/analyze-jd", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert "interview_id" in body and body["interview_id"].startswith("intv_")
    assert isinstance(body["core_competencies"], list)
    assert len(body["core_competencies"]) >= 3
    assert body["difficulty_baseline"] in {"easy", "medium", "hard"}
    assert isinstance(body["grounding_summary"], str)
    assert len(body["grounding_summary"]) > 0
    assert body["grounding_status"] == "ok"


async def test_analyze_jd_rejects_placeholder(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/interviews/analyze-jd",
        json={
            "job_title": "TBD",
            "job_description": "Lorem ipsum dolor sit amet consectetur adipiscing elit.",
        },
    )
    assert resp.status_code == 422
    assert "placeholder" in resp.json()["message"].lower()


async def test_analyze_jd_rejects_too_short(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/interviews/analyze-jd",
        json={"job_title": "SE", "job_description": "Need a dev. Ping me."},
    )
    assert resp.status_code == 422


async def test_analyze_jd_missing_fields(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/interviews/analyze-jd", json={"job_title": "SE"}
    )
    assert resp.status_code == 422


async def test_analyze_jd_grounding_timeout_degrades(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the grounding provider hangs the endpoint still succeeds 201 and
    flags grounding_status = 'degraded'."""

    async def _slow(self: Any, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(10)
        raise RuntimeError("should have timed out")

    monkeypatch.setattr(ground_mod.WebGroundingService, "research_role", _slow)
    resp = await api_client.post(
        "/api/v1/interviews/analyze-jd",
        json={
            "job_title": "Backend Engineer",
            "job_description": (
                "Full time backend engineer role with 3+ years Python, "
                "FastAPI, Postgres and microservices design.  We run on AWS "
                "and value strong written communication and ownership."
            ),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["grounding_status"] == "degraded"
    assert body["interview_id"].startswith("intv_")
