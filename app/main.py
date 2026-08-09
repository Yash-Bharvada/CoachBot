"""FastAPI application factory for the Interview Prep Simulator.

The app is constructed inside :func:`create_app` so that tests can spin up
multiple independent instances with overridden settings/databases without
having to monkey-patch global state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from structlog import get_logger
from structlog.stdlib import BoundLogger

from app.core.config import get_settings
from app.core.database import close_mongo_connection, open_mongo_connection
from app.core.exceptions import AppException, app_exception_handler
from app.core.security import start_sweeper, stop_sweeper
from app.routers import analysis, interviews, stream
from app.websockets.connection_manager import connection_manager

log: BoundLogger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks: DB, rate-limit sweeper, websocket housekeeping."""
    settings = get_settings()
    start_sweeper()
    await open_mongo_connection()
    from app.core.database import get_mongo_client

    db = get_mongo_client()[settings.mongo_db_name]
    await connection_manager.start(db)
    log.info(
        "app.started",
        port=settings.port,
        mongo_uri_host=settings.mongo_uri.split("@")[-1],
    )
    yield
    await connection_manager.stop()
    await close_mongo_connection()
    stop_sweeper()
    log.info("app.stopped")


def create_app() -> FastAPI:
    """Wire together the FastAPI application: middleware, routers, exceptions."""
    settings = get_settings()
    app = FastAPI(
        title="Interview Prep Simulator API",
        description=(
            "Real-time voice-enabled AI mock interview platform.  Start by "
            "POST-ing a JD to /api/v1/interviews/analyze-jd to receive an "
            "interview_id, then connect to the websocket stream to begin the "
            "live conversation.  When the session ends POST /finalize to "
            "receive a structured feedback report."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- Exception handler ------------------------------------------------
    app.add_exception_handler(AppException, app_exception_handler)

    # --- Routers ----------------------------------------------------------
    app.include_router(
        analysis.router,
        prefix="/api/v1/interviews",
        tags=["JD Analysis"],
    )
    app.include_router(
        interviews.router,
        prefix="/api/v1/interviews",
        tags=["Interview Sessions & Reports"],
    )
    app.include_router(
        stream.router,
        prefix="/api/v1/interviews",
        tags=["Live Voice Stream"],
    )

    @app.get("/health", tags=["Meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Return a cheap OK payload so load balancers can verify liveness."""
        return {"status": "ok"}

    return app


app = create_app()
