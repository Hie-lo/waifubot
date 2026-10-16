"""Async engine/session helpers. Foreign keys are enabled for SQLite (SPEC §5)."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(db_url: str, *, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(db_url, echo=echo)
    if db_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fks(dbapi_connection, _record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
