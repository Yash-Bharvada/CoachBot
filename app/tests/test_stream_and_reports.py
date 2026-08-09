"""Module 2 + 4 integration tests — text-mode websocket stream + /finalize + /report.

The websocket is driven with ``{"type": "text", "..."}`` frames rather than
encoded audio so the entire STT→LLM→TTS chain is exercised deterministically
via the mocks supplied by ``conftest.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.websockets.connection_manager import connection_manager

pytestmark = pytest.mark.asyncio


def _read_ws_frames_until(ws: TestClient, *, frame_types: set[str], limit: int = 60) -> list[dict[str, Any]]:
    """Drain frames from *ws* until we've seen at least one of each frame_type in frame_types."""
    frames: list[dict[str, Any]] = []
    seen: set[str] = set()
    remaining = limit
    while remaining > 0:
        remaining -= 1
        try:
            raw = ws.receive_text()
        except Exception:  # noqa: BLE001
            break
        try:
            import json as _json

            frame = _json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        frames.append(frame)
        seen.add(frame.get("type", "?"))
        if frame_types.issubset(seen) and any(
            f.get("type") == "audio" and f.get("is_final") for f in frames
        ):
            break
    return frames


async def test_websocket_rejects_unknown_interview(app_with_mocks: FastAPI) -> None:
    with TestClient(app_with_mocks) as http:
        with http.websocket_connect(
            "/api/v1/interviews/intv_doesnotexist000000000/stream"
        ) as ws:
            # Server should close the socket without sending frames.
            got = []
            try:
                while True:
                    got.append(ws.receive_text())
            except Exception:  # noqa: BLE001
                pass
            # The server must not accept interviews without a role context matrix.
            assert ws.client is None or len(got) == 0


async def test_websocket_text_stream_happy_path(
    seeded_interview: str,
    app_with_mocks: FastAPI,
    session_db: Any,
) -> None:
    """Exchange text frames over the websocket, assert we see the expected
    transcript + interviewer_text + audio + evaluation frames, and confirm
    the turn was persisted to turn_evaluations."""
    # Reset the manager so housekeeping loop runs fresh.
    await connection_manager.stop()

    with TestClient(app_with_mocks) as http:
        with http.websocket_connect(
            f"/api/v1/interviews/{seeded_interview}/stream"
        ) as ws:
            # Drain the first-turn greeting frames.
            greeting_frames = _read_ws_frames_until(
                ws, frame_types={"interviewer_text", "audio"}, limit=40
            )
            types = {f.get("type") for f in greeting_frames}
            assert "interviewer_text" in types
            assert "audio" in types

            # Now send the first candidate answer as a text frame.
            import json as _json

            ws.send_text(
                _json.dumps(
                    {
                        "type": "text",
                        "text": (
                            "For the async pipeline I would use an asyncio.Queue "
                            "backed by a worker pool, apply backpressure via a "
                            "bounded queue, and protect every DB call with a "
                            "timeout so a slow downstream can't stall the pipeline."
                        ),
                    }
                )
            )
            turn_frames = _read_ws_frames_until(
                ws,
                frame_types={"transcript", "interviewer_text", "evaluation"},
                limit=80,
            )
            types = {f.get("type") for f in turn_frames}
            # Transcript + interviewer text + evaluation MUST all be present.
            assert "transcript" in types
            assert "interviewer_text" in types
            assert "evaluation" in types
            ev = next(f for f in turn_frames if f.get("type") == "evaluation")
            assert set(ev["scores"].keys()) == {
                "relevance",
                "technical_depth",
                "clarity",
            }
            assert ev["difficulty_before"] in {"easy", "medium", "hard"}
            assert ev["difficulty_after"] in {"easy", "medium", "hard"}

    # Turn evaluation must be persisted to mongo.
    rows = await session_db.turn_evaluations.find(
        {"interview_id": seeded_interview}
    ).to_list(length=10)
    assert len(rows) >= 1
    first = rows[0]
    assert first["turn_index"] >= 0
    assert "overall_score" in first


async def test_finalize_and_report_endpoints(
    seeded_interview: str,
    api_client: Any,
    session_db: Any,
) -> None:
    """Manually seed turn evaluations, call /finalize, then GET /report and
    confirm the structure and overall readiness score are valid."""
    import time as _time

    base = {
        "interview_id": seeded_interview,
        "question_text": "Describe async pipelining in Python.",
        "candidate_text": "An answer of medium quality.",
        "timestamp": _time.time(),
    }
    turns = [
        {
            **base,
            "turn_index": 1,
            "scores": {"relevance": 80, "technical_depth": 75, "clarity": 70},
            "overall_score": 76.0,
            "difficulty_before": 1,
            "difficulty_after": 1,
        },
        {
            **base,
            "turn_index": 2,
            "scores": {"relevance": 60, "technical_depth": 55, "clarity": 65},
            "overall_score": 59.5,
            "difficulty_before": 1,
            "difficulty_after": 0,
        },
    ]
    await session_db.turn_evaluations.insert_many(turns)
    await session_db.interview_sessions.update_one(
        {"interview_id": seeded_interview},
        {"$set": {"turn_count": 2, "competencies_probed": ["Python", "SQL"]}},
    )

    resp = await api_client.post(
        f"/api/v1/interviews/{seeded_interview}/finalize"
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["interview_id"] == seeded_interview
    report = body["report"]
    assert 0 <= report["overall_readiness"] <= 100
    section = report["section_scores"]
    for key in ("confidence_and_tone", "fluency", "technical_accuracy", "relevance"):
        assert 0 <= section[key] <= 100
    assert isinstance(report["weak_points"], list)
    assert isinstance(report["competency_gaps"], list)
    assert "per_turn_scores" in report
    assert isinstance(report["narrative_summary"], str)
    assert len(report["narrative_summary"]) > 20

    # GET /report returns the *same* cached report (no regeneration).
    second = await api_client.get(f"/api/v1/interviews/{seeded_interview}/report")
    assert second.status_code == 200
    cached = second.json()
    assert cached["overall_readiness"] == report["overall_readiness"]
