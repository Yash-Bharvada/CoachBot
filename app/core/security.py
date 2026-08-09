"""Security helpers — in-memory sliding-window rate limiting + (optional) API-key.

Rate limiting is implemented with a straightforward in-memory sliding-window
counter (per ``(client_key, window_seconds)``).  The design is intentionally
simple and dependency-free: no slowapi internals, no Redis, no private
attribute access — it is easy to audit and trivially correct for a single
process.  A single background task expires buckets older than the window.

All public POST endpoints and the websocket handshake go through the
``http_rate_limiter`` / ``ws_rate_limiter`` dependencies below so a noisy
client cannot starve LLM / STT / TTS provider quotas.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, WebSocket, status
from structlog import get_logger

from app.core.config import get_settings

log = get_logger(__name__)

# In-memory rate-limit bookkeeping.  ``_buckets[key]`` is an ordered list of
# timestamps (float) for each hit *within* the current window.  A background
# task sweeps the structure periodically so old entries do not leak memory.
_buckets: dict[tuple[str, int], list[float]] = defaultdict(list)
_buckets_lock = asyncio.Lock()
_sweeper_task: asyncio.Task[None] | None = None

WINDOW_SECONDS = 60


def _ip_from_request(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _ip_from_websocket(websocket: WebSocket) -> str:
    forwarded = websocket.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = websocket.client
    if client is None:
        return "unknown"
    return f"{client.host}:{client.port}"


async def _hit(key: str, limit: int) -> tuple[bool, int]:
    """Record a hit and return (allowed, remaining).

    Pure sliding window: count entries in ``[now - WINDOW_SECONDS, now]`` and
    append a new timestamp if under limit.  Entries older than the window
    are pruned on every hit to keep memory bounded per-key.
    """
    now = time.monotonic()
    window_start = now - WINDOW_SECONDS
    async with _buckets_lock:
        bucket = _buckets[(key, WINDOW_SECONDS)]
        # Prune: drop anything before window_start
        i = 0
        n = len(bucket)
        while i < n and bucket[i] < window_start:
            i += 1
        if i:
            del bucket[:i]
        remaining = limit - len(bucket)
        allowed = remaining > 0
        if allowed:
            bucket.append(now)
        return allowed, max(0, remaining - 1)


async def _sweeper() -> None:
    """Periodically drop fully-expired buckets to keep memory bounded.

    Runs every 2 * WINDOW_SECONDS.  Because the hot path prunes per-key on
    every hit, this task is purely a backstop for keys that have gone
    completely cold (no hits in >> window).
    """
    try:
        while True:
            await asyncio.sleep(2 * WINDOW_SECONDS)
            cutoff = time.monotonic() - WINDOW_SECONDS
            async with _buckets_lock:
                drop = [k for k, v in _buckets.items() if v and v[-1] < cutoff]
                for k in drop:
                    del _buckets[k]
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 — never let the background task die silently.
        log.exception("rate_limit.sweeper.failed")
        raise


def start_sweeper() -> None:
    """Idempotently start the backstop sweeper task.  Called by app lifespan."""
    global _sweeper_task
    if _sweeper_task is None or _sweeper_task.done():
        _sweeper_task = asyncio.create_task(_sweeper(), name="rate-limit-sweeper")


def stop_sweeper() -> None:
    """Cancel the sweeper task (used in tests)."""
    global _sweeper_task
    if _sweeper_task is not None and not _sweeper_task.done():
        _sweeper_task.cancel()


def http_rate_limiter() -> Callable[[Request], Awaitable[None]]:
    """Return a FastAPI dependency enforcing the HTTP POST rate limit.

    The returned coroutine is injected via ``Depends()`` on every public
    POST router so a 429 (with Retry-After header) is returned *before*
    the handler runs any expensive work.
    """

    async def _dep(request: Request) -> None:
        settings = get_settings()
        limit = settings.rate_limit_per_minute
        key = f"http:{_ip_from_request(request)}"
        allowed, _remaining = await _hit(key, limit)
        if not allowed:
            log.warning("rate_limit.exceeded.http", client=key, limit=limit)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please slow down.",
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )

    return _dep


async def ws_rate_limiter(websocket: WebSocket) -> None:
    """Inline handshake check: enforce the WS rate limit BEFORE ``accept()``.

    If the client has exceeded their budget we close the socket with
    ``1008 Policy Violation`` and raise a 429-equivalent HTTPException so
    the client sees a clean rejection *without* the server allocating any
    interview state on their behalf.
    """
    settings = get_settings()
    limit = settings.ws_rate_limit_per_minute
    key = f"ws:{_ip_from_websocket(websocket)}"
    allowed, _remaining = await _hit(key, limit)
    if not allowed:
        log.warning("rate_limit.exceeded.ws", client=key, limit=limit)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Websocket handshake rate limit exceeded.",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
