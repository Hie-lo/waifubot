"""Async database session management with SQLite WAL pragmas and busy-retry backoff (ADR-0005)."""

import asyncio
import random
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from waifubot.db.base import Base


def configure_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Enforces SQLite performance and durability PRAGMAs (WAL, busy_timeout, foreign_keys)."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA wal_autocheckpoint=1000")
            cursor.close()
        except Exception:
            pass


def create_engine_and_sessionmaker(
    database_url: str = "sqlite+aiosqlite:///data/waifubot.db",
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Builds the AsyncEngine and sessionmaker with configured pragmas."""
    engine = create_async_engine(database_url, echo=echo)
    if "sqlite" in database_url:
        configure_sqlite_pragmas(engine)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, session_factory


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
    max_retries: int = 5,
    base_backoff_seconds: float = 0.05,
) -> AsyncIterator[AsyncSession]:
    """Provide a transactional async session with exponential backoff on SQLITE_BUSY."""
    attempt = 0
    while True:
        try:
            async with session_factory() as session, session.begin():
                yield session
            break
        except (OperationalError, sqlite3.OperationalError) as exc:
            error_msg = str(exc).lower()
            is_busy = "database is locked" in error_msg or "busy" in error_msg
            if not is_busy or attempt >= max_retries:
                raise
            attempt += 1
            # Randomized exponential backoff: 50ms * 2^attempt * (0.8 + 0.4 * random)
            jitter = 0.8 + 0.4 * random.random()
            sleep_duration = base_backoff_seconds * (2 ** (attempt - 1)) * jitter
            await asyncio.sleep(sleep_duration)


async def execute_passive_checkpoint(session: AsyncSession) -> None:
    """Executes a passive WAL checkpoint (PRAGMA wal_checkpoint(PASSIVE))."""
    await session.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))


async def init_db(engine: AsyncEngine) -> None:
    """Creates tables if they do not exist (useful for testing or initial setup)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
