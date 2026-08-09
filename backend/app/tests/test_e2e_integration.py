"""End-to-End (E2E) Integration Tests for Interview Prep Simulator.

Tests full user journeys:
1. Health probe & CORS headers.
2. Candidate Onboarding / Seeded Session -> Tavus PAL Video Conversation -> Webhook Processing -> Finalize & Report.
3. Edge cases & error handling (invalid callback URLs, unknown interview IDs, Tavus webhook HMAC security).
"""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import get_settings


pytestmark = pytest.mark.asyncio


async def test_health_check_and_cors_headers(api_client: AsyncClient) -> None:
    """Validate /health endpoint and CORS configuration."""
    # GET /health
    res = await api_client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    # OPTIONS preflight request
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type, Authorization",
    }
    res_options = await api_client.options("/api/v1/interviews/analyze-jd", headers=headers)
    assert res_options.status_code in (200, 204)
    assert "access-control-allow-origin" in res_options.headers


async def test_full_interview_lifecycle_e2e(
    api_client: AsyncClient,
    seeded_interview: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute complete candidate journey from seeded interview to Tavus session and report generation."""
    settings = get_settings()
    monkeypatch.setattr(settings, "tavus_api_key", "d9bd11ce3c804fa6a75a73e6b2b81378")
    monkeypatch.setattr(settings, "tavus_pal_id", "pec4f150ef27")
    monkeypatch.setattr(settings, "tavus_workspace_id", "b6dc2f567e")

    interview_id = seeded_interview

    # Mock Tavus API client HTTP post call
    with patch(
        "app.services.tavus_service._TavusHttpClient.post",
        new=AsyncMock(
            return_value={
                "conversation_id": "c_tavus_mock_12345",
                "conversation_name": "Interview Session",
                "conversation_url": "https://tavusapi.com/c/c_tavus_mock_12345",
                "status": "active",
                "created_at": "2026-08-09T22:00:00Z",
            }
        ),
    ):
        # 1. Get Session Summary
        summary_res = await api_client.get(f"/api/v1/interviews/{interview_id}")
        assert summary_res.status_code == 200
        summary_data = summary_res.json()
        assert summary_data["interview_id"] == interview_id
        assert summary_data["status"] in ("in_progress", "created")

        # 2. Create Tavus Conversation
        tavus_payload = {
            "callback_url": "http://testserver/api/v1/interviews/tavus-webhook"
        }
        tavus_res = await api_client.post(
            f"/api/v1/interviews/{interview_id}/conversation", json=tavus_payload
        )
        assert tavus_res.status_code == 201, tavus_res.text
        tavus_data = tavus_res.json()
        assert tavus_data["conversation_id"] == "c_tavus_mock_12345"
        assert "conversation_url" in tavus_data

        # 3. Finalize Interview & Generate Report
        finalize_res = await api_client.post(
            f"/api/v1/interviews/{interview_id}/finalize"
        )
        assert finalize_res.status_code == 201, finalize_res.text
        finalize_data = finalize_res.json()
        assert finalize_data["interview_id"] == interview_id
        assert "report" in finalize_data

        # 4. Fetch Cached Report
        report_res = await api_client.get(f"/api/v1/interviews/{interview_id}/report")
        report_data = report_res.json()
        assert "overall_readiness" in report_data


async def test_tavus_conversation_invalid_input_and_not_found(api_client: AsyncClient) -> None:
    """Test edge cases for Tavus conversation creation."""
    # Non-existent interview ID
    res_404 = await api_client.post(
        "/api/v1/interviews/non_existent_12345/conversation",
        json={"callback_url": "http://localhost:8000/api/v1/interviews/tavus-webhook"},
    )
    assert res_404.status_code == 404

    # Invalid callback URL format
    res_400 = await api_client.post(
        "/api/v1/interviews/non_existent_12345/conversation",
        json={"callback_url": "invalid-url-without-scheme"},
    )
    assert res_400.status_code == 400


async def test_tavus_webhook_verification(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test HMAC signature validation on the Tavus webhook endpoint."""
    secret = "test_tavus_secret_99"
    settings = get_settings()
    monkeypatch.setattr(settings, "tavus_webhook_secret", secret)

    payload_bytes = b'{"event_name": "conversation.started", "conversation_id": "c_123"}'

    # Valid HMAC signature
    valid_sig = hmac.new(
        secret.encode("utf-8"), msg=payload_bytes, digestmod=hashlib.sha256
    ).hexdigest()

    res_ok = await api_client.post(
        "/api/v1/interviews/tavus-webhook",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Tavus-Signature": valid_sig,
        },
    )
    assert res_ok.status_code == 200
    assert res_ok.json() == {"status": "ok", "event": "conversation.started"}

    # Invalid HMAC signature
    res_unauthorized = await api_client.post(
        "/api/v1/interviews/tavus-webhook",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Tavus-Signature": "invalid_signature_hash",
        },
    )
    assert res_unauthorized.status_code == 401
