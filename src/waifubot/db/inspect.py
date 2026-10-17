"""Schema inspection command: prints the DDL of every table.

Usage: PYTHONPATH=src python3 -m waifubot.db.inspect
"""
from __future__ import annotations

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

from waifubot.db import models  # noqa: F401 — registers all tables
from waifubot.db.base import Base


def main() -> int:
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        ddl = str(CreateTable(table).compile(dialect=sqlite.dialect()))
        print(ddl.strip())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
