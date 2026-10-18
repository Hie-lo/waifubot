"""Command Line Interface for WaifuBot (R46).

Provides the `waifubot health` and management CLI commands.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from waifubot.core.logging import get_recent_errors
from waifubot.services.status import get_free_ram_mb


async def run_health_check(
    db_url: str = "sqlite+aiosqlite:///data/waifubot.db",
    storage_dir: str | Path = "data/images",
) -> tuple[bool, list[str]]:
    """Executes system health checks across database, storage, RAM, and error log."""
    checks: list[str] = []
    healthy = True

    # 1. Database connection check
    try:
        engine = create_async_engine(db_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        checks.append(" [OK] Database connectivity (SQLite WAL)")
    except Exception as e:
        healthy = False
        checks.append(f" [FAIL] Database connectivity: {e}")

    # 2. Storage directory check
    try:
        path = Path(storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".health_test"
        test_file.write_text("health_ok", encoding="utf-8")
        test_file.unlink()
        checks.append(f" [OK] Storage directory writable ({path})")
    except Exception as e:
        healthy = False
        checks.append(f" [FAIL] Storage directory unwritable: {e}")

    # 3. Memory check
    free_ram = get_free_ram_mb()
    if free_ram >= 256:
        checks.append(f" [OK] System free memory: {free_ram} MB (healthy)")
    else:
        healthy = False
        checks.append(f" [FAIL] System free memory critically low: {free_ram} MB (< 256 MB)")

    # 4. Error log buffer check
    recent_errors = get_recent_errors()
    if not recent_errors:
        checks.append(" [OK] Error buffer: 0 recent errors")
    else:
        checks.append(f" [WARN] Error buffer: {len(recent_errors)} recent error(s) logged")

    return healthy, checks


def cli_health(args: argparse.Namespace) -> int:
    """CLI subcommand handler for `waifubot health`."""
    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///data/waifubot.db")
    storage_dir = os.environ.get("STORAGE_DIR", "data/images")

    healthy, checks = asyncio.run(run_health_check(db_url=db_url, storage_dir=storage_dir))

    print("WaifuBot Health Check:")
    print("----------------------")
    for line in checks:
        print(line)
    print("----------------------")

    if healthy:
        print("Overall Status: HEALTHY")
        return 0
    else:
        print("Overall Status: UNHEALTHY")
        return 1


def cli_run(args: argparse.Namespace) -> int:
    """CLI subcommand handler for `waifubot run`."""
    from waifubot.bot.app import run_bot

    run_bot()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="waifubot", description="WaifuBot management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # health subcommand
    health_parser = subparsers.add_parser(
        "health", help="Runs system diagnostics and health checks"
    )
    health_parser.set_defaults(func=cli_health)

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Starts the WaifuBot Telegram bot")
    run_parser.set_defaults(func=cli_run)

    args = parser.parse_args(argv)

    if hasattr(args, "func"):
        return args.func(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
