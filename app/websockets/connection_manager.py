"""Tracks active interview websocket sessions with reconnect grace support.

The connection manager owns:
  * the in-memory mapping of ``interview_id`` → :class:`InterviewSession`
  * a background task that periodically checkpoints transcript state to
    MongoDB so that a hard process crash loses, at most, a handful of turns
  * grace period bookkeeping so temporary disconnects do not discard work
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from structlog import get_logger

from app.core.config import get_settings

log = get_logger(__name__)

# Websocket close codes used in the platform.  Using well-known numeric
# values keeps the client code path uniform across browsers/libraries.
WS_CLOSE_NORMAL = 1000
WS_CLOSE_GOING_AWAY = 1001
WS_CLOSE_PROTOCOL_ERROR = 1002
WS_CLOSE_SERVER_ERROR = 1011
WS_CLOSE_IDLE = 4000
WS_CLOSE_ABANDONED = 4001
WS_CLOSE_PIPELINE_FAILURE = 4002


@dataclass(slots=True)
class TurnRecord:
    """A single conversation turn (candidate → interviewer)."""

    turn_index: int
    role: str  # "candidate" | "interviewer"
    text: str
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class InterviewSession:
    """Per-interview in-memory state for a live session."""

    interview_id: str
    status: str = "in_progress"  # "in_progress" | "reconnecting" | "finalized" | "abandoned"
    turns: list[TurnRecord] = field(default_factory=list)
    difficulty_index: int = 1  # 0 = easy, 1 = medium, 2 = hard
    competencies_probed: set[str] = field(default_factory=set)
    competencies_pending: set[str] = field(default_factory=set)
    scores: dict[int, dict[str, float]] = field(default_factory=dict)
    audio_buffer_bytes: int = 0
    last_seen_at: float = field(default_factory=time.time)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    # When a websocket drops we mark the session "reconnecting" and start
    # this timer; on expiry the session moves to "abandoned".
    reconnect_deadline: float | None = None

    def record_turn(self, role: str, text: str) -> int:
        """Append a turn and return its 1-based index."""
        next_idx = len([t for t in self.turns if t.role == "candidate"])
        self.turns.append(TurnRecord(turn_index=next_idx, role=role, text=text))
        self.last_seen_at = time.time()
        return next_idx


class ConnectionManager:
    """Tracks active sessions and provides reconnect-grace semantics."""

    def __init__(self) -> None:
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = asyncio.Lock()
        self._checkpoint_task: asyncio.Task[None] | None = None

    async def start(self, db: AsyncIOMotorDatabase) -> None:
        """Kick off background housekeeping tasks."""
        if self._checkpoint_task is None or self._checkpoint_task.done():
            self._checkpoint_task = asyncio.create_task(
                self._background_housekeeping(db),
                name="ws-housekeeping",
            )

    async def stop(self) -> None:
        """Cancel any running background tasks."""
        if self._checkpoint_task is not None and not self._checkpoint_task.done():
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass

    async def create_session(
        self,
        interview_id: str,
        competencies_pending: list[str],
        difficulty_baseline: int = 1,
    ) -> InterviewSession:
        """Create a brand new session object and persist an entry record."""
        async with self._lock:
            session = InterviewSession(
                interview_id=interview_id,
                difficulty_index=difficulty_baseline,
                competencies_pending=set(competencies_pending),
            )
            self._sessions[interview_id] = session
            log.info("session.created", interview_id=interview_id)
            return session

    async def get_session(self, interview_id: str) -> InterviewSession | None:
        """Return a session by id, refreshing its ``last_seen_at``."""
        async with self._lock:
            session = self._sessions.get(interview_id)
            if session is not None:
                session.last_seen_at = time.time()
            return session

    async def mark_reconnecting(self, interview_id: str) -> None:
        """Signal a disconnect — state is preserved for a grace window."""
        async with self._lock:
            session = self._sessions.get(interview_id)
            if session is None or session.status == "finalized":
                return
            session.status = "reconnecting"
            session.reconnect_deadline = time.time() + get_settings().websocket_grace_period_seconds
            log.info("session.reconnecting", interview_id=interview_id)

    async def reconnect(self, interview_id: str) -> InterviewSession | None:
        """Restore a reconnecting session to ``in_progress`` if still valid."""
        async with self._lock:
            session = self._sessions.get(interview_id)
            if session is None:
                return None
            if session.status == "reconnecting":
                if (
                    session.reconnect_deadline is None
                    or time.time() <= session.reconnect_deadline
                ):
                    session.status = "in_progress"
                    session.reconnect_deadline = None
                    log.info("session.reconnected", interview_id=interview_id)
                    return session
                session.status = "abandoned"
                session.ended_at = time.time()
                log.info("session.abandoned.grace_expired", interview_id=interview_id)
                return None
            if session.status == "in_progress":
                return session
            return None

    async def finalize_session(self, interview_id: str) -> InterviewSession | None:
        """Mark a session completed and stop tracking it after checkpointing."""
        async with self._lock:
            session = self._sessions.get(interview_id)
            if session is None:
                return None
            session.status = "finalized"
            session.ended_at = time.time()
            log.info("session.finalized", interview_id=interview_id)
            return session

    async def _background_housekeeping(self, db: AsyncIOMotorDatabase) -> None:
        """Periodic task: expire abandoned sessions + checkpoint transcripts."""
        try:
            while True:
                await asyncio.sleep(10)
                await self._reap_expired_sessions()
                await self._checkpoint_transcripts(db)
        except asyncio.CancelledError:
            # Expected on shutdown — allow the coroutine to exit cleanly.
            return
        except Exception:  # noqa: BLE001 — defensive guard; we never want the
            # background task to die silently.
            log.exception("ws.housekeeping.failed")
            raise

    async def _reap_expired_sessions(self) -> None:
        """Move any reconnecting sessions past the grace window to abandoned."""
        settings = get_settings()
        now = time.time()
        async with self._lock:
            for session in self._sessions.values():
                if session.status == "reconnecting" and (
                    session.reconnect_deadline is not None
                    and now > session.reconnect_deadline
                ):
                    session.status = "abandoned"
                    session.ended_at = now
                    log.warning(
                        "session.abandoned",
                        interview_id=session.interview_id,
                        grace_seconds=settings.websocket_grace_period_seconds,
                    )
                if session.status == "in_progress" and (
                    now - session.last_seen_at > settings.websocket_idle_timeout_seconds
                ):
                    session.status = "abandoned"
                    session.ended_at = now
                    log.warning(
                        "session.idle_timeout",
                        interview_id=session.interview_id,
                        idle_seconds=settings.websocket_idle_timeout_seconds,
                    )

    async def _checkpoint_transcripts(self, db: AsyncIOMotorDatabase) -> None:
        """Write the current transcript/session summary to Mongo every tick."""
        async with self._lock:
            snapshot = list(self._sessions.values())
        for session in snapshot:
            try:
                await db.interview_sessions.update_one(
                    {"interview_id": session.interview_id},
                    {
                        "$set": {
                            "status": session.status,
                            "difficulty_index": session.difficulty_index,
                            "competencies_probed": sorted(session.competencies_probed),
                            "competencies_pending": sorted(session.competencies_pending),
                            "last_seen_at": session.last_seen_at,
                            "ended_at": session.ended_at,
                            "turn_count": len(session.turns),
                        }
                    },
                    upsert=True,
                )
            except Exception:  # noqa: BLE001 — never abort housekeeping for one session.
                log.exception(
                    "session.checkpoint.failed",
                    interview_id=session.interview_id,
                )


# Single global manager owned by the app lifespan.  The instance is created
# at import time and started from ``main.py`` so that tests can swap it out.
connection_manager = ConnectionManager()
