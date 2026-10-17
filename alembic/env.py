"""Alembic environment: async engine, DB_URL from the environment (SPEC §12)."""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from waifubot.db import models  # noqa: E402,F401 — registers all tables
from waifubot.db.base import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DEFAULT_DB_URL = "sqlite+aiosqlite:///./storage/waifubot.db"
db_url = os.environ.get("DB_URL", DEFAULT_DB_URL).strip() or DEFAULT_DB_URL
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def _ensure_sqlite_parent() -> None:
    marker = ":///"
    if db_url.startswith("sqlite") and marker in db_url:
        db_path = Path(db_url.split(marker, 1)[1])
        if db_path.parent and not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _ensure_sqlite_parent()

    def do_run(connection) -> None:  # noqa: ANN001
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

    async def main() -> None:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        async with connectable.connect() as connection:
            await connection.run_sync(do_run)
        await connectable.dispose()

    asyncio.run(main())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
