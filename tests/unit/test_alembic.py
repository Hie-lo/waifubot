"""Alembic baseline migration: upgrade / downgrade really work (Sprint 1a exit)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"characters", "images", "jobs", "posts", "settings"}


def _table_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name != 'alembic_version'"
            )
        }
    finally:
        con.close()


def test_upgrade_downgrade_cycle(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    db_path = tmp_path / "mig.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))

    command.upgrade(cfg, "head")
    assert _table_names(db_path) == EXPECTED_TABLES

    command.downgrade(cfg, "base")
    assert _table_names(db_path) == set()

    command.upgrade(cfg, "head")
    assert _table_names(db_path) == EXPECTED_TABLES


def test_env_var_is_used_not_file(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    db_path = tmp_path / "from_env.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    assert db_path.exists()
    assert "DB_URL" in os.environ
