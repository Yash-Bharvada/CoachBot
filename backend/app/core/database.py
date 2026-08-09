"""Async MongoDB client + dependency helpers used across the project.

We deliberately expose a :func:`get_database` callable so that route handlers
and service constructors can accept the database via FastAPI dependency
injection instead of importing a global ``db`` object ad hoc.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from structlog import get_logger

from app.core.config import Settings, get_settings

log = get_logger(__name__)

# Module-level client — created once at startup, closed on shutdown so that
# motor's connection pool is reused across requests instead of re-connecting
# for every handler invocation.
_client: AsyncIOMotorClient | None = None


def _build_client(settings: Settings) -> AsyncIOMotorClient:
    """Construct an AsyncIOMotorClient using the loaded settings."""
    return AsyncIOMotorClient(
        settings.mongo_uri,
        maxPoolSize=50,
        minPoolSize=5,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )


async def open_mongo_connection() -> None:
    """Connect to MongoDB on application startup.

    Idempotent: repeated calls are a no-op.  This allows tests as well as the
    ``lifespan`` handler in ``main.py`` to call this function safely.
    """
    global _client
    if _client is not None:
        return
    settings = get_settings()
    _client = _build_client(settings)
    # Issue a cheap ping to surface bad credentials early rather than on the
    # first real request.
    await _client.admin.command("ping")
    log.info(
        "mongodb.connected",
        host=settings.mongo_uri.split("@")[-1],
        database=settings.mongo_db_name,
    )


async def close_mongo_connection() -> None:
    """Disconnect from MongoDB on application shutdown."""
    global _client
    if _client is None:
        return
    _client.close()
    _client = None
    log.info("mongodb.disconnected")


def get_mongo_client() -> AsyncIOMotorClient:
    """Return the currently active MongoDB client (used by tests)."""
    if _client is None:
        raise RuntimeError(
            "MongoDB client is not initialised. Call open_mongo_connection() first."
        )
    return _client


async def get_database(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """FastAPI dependency yielding the configured database."""
    if _client is None:
        await open_mongo_connection()
    assert _client is not None
    yield _client[settings.mongo_db_name]
