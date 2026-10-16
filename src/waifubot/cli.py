"""WaifuBot CLI (Sprint 1b — dev tool; the admin UI is Telegram in Sprint 3).

Commands:
    init-db        run Alembic migrations (creates the database)
    add-character  save a character (adult identity validated)
    mock-generate  create a job and run a mock generation, store validated images

Usage (from the repo root, venv active):
    PYTHONPATH=src python3 -m waifubot.cli init-db
    PYTHONPATH=src python3 -m waifubot.cli add-character --name Aki \
        --identity "adult woman, 25 years old, blue hair"
    PYTHONPATH=src python3 -m waifubot.cli mock-generate --character Aki --count 4 --seed 42
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from waifubot.config import Settings, ensure_runtime_dirs, load_env_file
from waifubot.core.content_safety import load_blocklist, validate_adult_identity
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.core.providers import (
    create_provider,
    load_provider_config,
    validate_provider_config,
)
from waifubot.core.tags_pool import load_tags_pool
from waifubot.db.models import Character
from waifubot.db.session import make_engine, make_session_factory
from waifubot.errors import WaifuBotError
from waifubot.services.generation import run_generation_job

REPO_ROOT = Path(__file__).resolve().parents[2]
TAGS_POOL_PATH = REPO_ROOT / "config" / "tags_pool.yaml"
BLOCKLIST_PATH = REPO_ROOT / "config" / "blocklist.yaml"
MOCK_CONFIG_PATH = REPO_ROOT / "config" / "providers" / "mock.yaml"


def _init_db(settings: Settings, *, quiet: bool = False) -> None:
    from alembic.config import Config

    from alembic import command

    ensure_runtime_dirs(settings)
    os.environ["DB_URL"] = settings.db_url
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    # script_location in alembic.ini is relative; make it absolute so CLI works
    # from any working directory (editable install / services).
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")  # idempotent: safe before every command
    if not quiet:
        print(f"database ready: {settings.db_url}")


def _load_stack(settings: Settings):  # noqa: ANN202
    pool = load_tags_pool(TAGS_POOL_PATH)
    blocklist = load_blocklist(BLOCKLIST_PATH)
    builder = PromptBuilder(pool, blocklist)
    ensure_runtime_dirs(settings)
    pipeline = ImagePipeline(settings.storage_root)
    return builder, pipeline, blocklist


async def _add_character(settings: Settings, args: argparse.Namespace) -> int:
    builder, _pipeline, blocklist = _load_stack(settings)
    validate_adult_identity(args.identity, blocklist)
    style_lock = args.style_lock or builder.style_lock
    engine = make_engine(settings.db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            row = Character(
                name=args.name,
                identity_prompt=args.identity,
                style_lock=style_lock,
                version=1,
                is_active=True,
            )
            session.add(row)
            await session.commit()
            print(f"character saved: id={row.id} name={row.name} version={row.version}")
    finally:
        await engine.dispose()
    return 0


async def _mock_generate(settings: Settings, args: argparse.Namespace) -> int:
    from waifubot.core.admin_settings import AdminSettings

    builder, pipeline, blocklist = _load_stack(settings)
    provider_cfg = load_provider_config(MOCK_CONFIG_PATH)
    if args.mode:
        provider_cfg = {**provider_cfg, "mode": args.mode}
    provider = create_provider(provider_cfg)

    engine = make_engine(settings.db_url)
    try:
        factory = make_session_factory(engine)
        # Runtime-changeable caps (user rule 2026-10-16): stored bot setting >
        # env default > code default; hard limits clamp inside the service.
        admin = AdminSettings(factory)
        max_images = await admin.get_or("images_per_job", settings.max_images_per_job)
        max_jobs = await admin.get_or("daily_cap", settings.max_jobs_per_day)
        async with factory() as session:
            identity = args.identity
            style_lock = args.style_lock
            if args.character:
                result = await session.execute(
                    select(Character).where(Character.name == args.character)
                )
                row = result.scalars().first()
                if row is None:
                    print(f"error: character {args.character!r} not found", file=sys.stderr)
                    return 1
                identity = row.identity_prompt
                style_lock = style_lock or row.style_lock
            if not identity:
                print("error: provide --identity or --character", file=sys.stderr)
                return 1

            job, images, gen_result = await run_generation_job(
                session,
                provider=provider,
                pipeline=pipeline,
                builder=builder,
                identity=identity,
                style_lock=style_lock,
                count=args.count,
                seed=args.seed,
                idempotency_key=args.idempotency_key,
                requested_by="cli",
                max_images_per_job=max_images,
                max_jobs_per_day=max_jobs,
            )
            await session.commit()

            print(f"job id={job.id} status={job.status}")
            if job.error_code:
                print(f"error_code={job.error_code} summary={job.error_summary or ''}")
            if gen_result is not None:
                print(f"provider={gen_result.provider} result={gen_result.status} "
                      f"elapsed={gen_result.elapsed_s}s")
            print(f"images stored: {len(images)} (pending_review)")
            for row in images:
                print(f"  {row.file_path} {row.width}x{row.height} {row.mime_type} "
                      f"sha256={row.sha256[:12]}...")
            return 0 if job.status == "succeeded" else 1
    finally:
        await provider.close()
        await engine.dispose()


async def _live_smoke(settings: Settings, args: argparse.Namespace) -> int:
    """Sprint 2 manual live smoke (SPEC: bounded runs, prior notice, real provider)."""
    if not args.confirm_live:
        print(
            "ERROR: refusing to run without --confirm-live. "
            "This opens a real browser and sends real generation requests.",
            file=sys.stderr,
        )
        return 2

    runs = min(max(1, args.runs), 10)  # hard max
    spacing = max(0, args.spacing)

    builder, pipeline, _blocklist = _load_stack(settings)
    provider_cfg = load_provider_config(Path(args.config))
    validate_provider_config(provider_cfg)
    # Visibility override (user request 2026-10-16): test headless runs with the
    # exact same flow; a window opens only when a challenge needs the operator.
    if getattr(args, "headless", False):
        provider_cfg = {**provider_cfg, "launch": {
            **(provider_cfg.get("launch") or {}),
            "headless": True, "force_headless": True, "force_headed": False,
        }}
    elif getattr(args, "headed", False):
        provider_cfg = {**provider_cfg, "launch": {
            **(provider_cfg.get("launch") or {}),
            "headless": False, "force_headed": True, "force_headless": False,
        }}
    provider = create_provider(provider_cfg)

    # Style: admin setting -> provider style mapping (never invented).
    from waifubot.core.admin_settings import AdminSettings

    admin = AdminSettings(make_session_factory(make_engine(settings.db_url)))
    style_key = args.style or await admin.get("default_style")
    style_values = provider_cfg.get("style_values", {}) or {}
    if style_key not in style_values:
        print(f"error: unknown style {style_key!r}; known: {sorted(style_values)}", file=sys.stderr)
        await provider.close()
        return 2
    style_value = style_values[style_key]
    count = args.count or await admin.get_or("images_per_job", settings.max_images_per_job)
    max_images = await admin.get_or("images_per_job", settings.max_images_per_job)
    max_jobs = await admin.get_or("daily_cap", settings.max_jobs_per_day)

    print("[live] WARNING: a real browser will open and real generation requests will be sent.")
    print(f"[live] provider={provider.name} page={provider_cfg.get('page_url')} "
          f"style_key={style_key!r} count={count} runs={runs} spacing={spacing}s "
          f"headless={(provider_cfg.get('launch') or {}).get('headless')}")

    engine = make_engine(settings.db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            identity = args.identity
            style_lock = args.style_lock
            if args.character:
                result = await session.execute(
                    select(Character).where(Character.name == args.character)
                )
                row = result.scalars().first()
                if row is None:
                    print(f"error: character {args.character!r} not found", file=sys.stderr)
                    return 1
                identity = row.identity_prompt
                style_lock = style_lock or row.style_lock
        for i in range(1, runs + 1):
            print(f"\n[live] === run {i}/{runs} ===")
            async with factory() as session:
                job, images, result = await run_generation_job(
                    session,
                    provider=provider,
                    pipeline=pipeline,
                    builder=builder,
                    identity=identity or "adult woman, 25 years old, long blue hair, red eyes",
                    style_lock=style_lock or await admin.get("style_lock"),
                    count=count,
                    seed=args.seed,
                    style_value=style_value,
                    timeout_s=float(provider_cfg.get("limits", {}).get("timeout_s", 240)),
                    idempotency_key=f"live:{uuid.uuid4().hex}",
                    requested_by="cli-live",
                    max_images_per_job=max_images,
                    max_jobs_per_day=max_jobs,
                )
                await session.commit()
                print(f"[live] job={job.id} status={job.status} images={len(images)} "
                      f"provider_result={result.status if result else '?'} "
                      f"elapsed={result.elapsed_s if result else '?'}s")
                if job.error_code:
                    print(f"[live] error_code={job.error_code} summary={job.error_summary or ''}")
                if result and result.notes:
                    print(f"[live] notes: {', '.join(result.notes)}")
            if i < runs and spacing > 0:
                print(f"[live] spacing {spacing}s...")
                await asyncio.sleep(spacing)
    finally:
        await provider.close()
        await engine.dispose()
    print("\n[live] done. Review storage/images and the jobs table before continuing.")
    return 0


async def _set_setting(settings: Settings, args: argparse.Namespace) -> int:
    """Change a runtime setting (user rule 2026-10-16: changeable options)."""
    from waifubot.core.admin_settings import AdminSettings

    admin = AdminSettings(make_session_factory(make_engine(settings.db_url)))
    try:
        value: object = json.loads(args.value)
    except (ValueError, TypeError):
        value = args.value  # plain string, e.g. set-setting default_style anime
    stored = await admin.set(args.key, value, updated_by="cli")
    print(f"{args.key} = {stored!r}")
    print("effective immediately for new jobs (env/default used until now)")
    return 0


async def _show_settings(settings: Settings, args: argparse.Namespace) -> int:  # noqa: ARG001
    from waifubot.core.admin_settings import AdminSettings

    admin = AdminSettings(make_session_factory(make_engine(settings.db_url)))
    values = await admin.all_settings()
    for key in sorted(values):
        print(f"{key} = {values[key]!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waifubot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="run Alembic migrations")

    p_add = sub.add_parser("add-character", help="save a character")
    p_add.add_argument("--name", required=True)
    p_add.add_argument(
        "--identity", required=True, help="fixed identity; must state adult age/features"
    )
    p_add.add_argument("--style-lock", default="")

    p_gen = sub.add_parser("mock-generate", help="run a mock generation")
    p_gen.add_argument("--character", default="", help="saved character name")
    p_gen.add_argument("--identity", default="", help="one-off identity (or use --character)")
    p_gen.add_argument("--style-lock", default="")
    p_gen.add_argument("--count", type=int, default=4)
    p_gen.add_argument("--seed", type=int, default=None)
    p_gen.add_argument("--idempotency-key", default=None)
    p_gen.add_argument(
        "--mode", default="",
        help="override mock mode (ok/partial/malformed/blocked/timeout/error/empty)",
    )

    p_live = sub.add_parser(
        "live-smoke", help="LIVE provider runs — opens a real browser (Sprint 2 smoke test)"
    )
    p_live.add_argument("--confirm-live", action="store_true",
                        help="required: confirm a real browser and real generations")
    p_live.add_argument("--runs", type=int, default=3)
    p_live.add_argument("--spacing", type=int, default=30, help="seconds between runs")
    p_live.add_argument("--count", type=int, default=0, help="images per run (0 = from settings)")
    p_live.add_argument("--style", default="", help="style key (0 = default_style setting)")
    p_live.add_argument("--seed", type=int, default=None)
    p_live.add_argument(
        "--config", default=str(REPO_ROOT / "config" / "providers" / "perchance.yaml")
    )
    p_live.add_argument("--character", default="", help="saved character name")
    p_live.add_argument("--identity", default="",
                        help="one-off identity (default: an adult test identity)")
    p_live.add_argument("--style-lock", default="")
    vis = p_live.add_mutually_exclusive_group()
    vis.add_argument(
        "--headless", action="store_true",
        help="run without a browser window (test mode; a window opens only for challenges)",
    )
    vis.add_argument(
        "--headed", action="store_true",
        help="force a visible browser window (default per config)",
    )

    p_set = sub.add_parser("set-setting", help="change a runtime setting (e.g. daily_cap)")
    p_set.add_argument("key", help="setting key: default_style, style_lock, images_per_job, "
                                   "daily_cap, posting_interval_s, retention_days")
    p_set.add_argument("value", help="new value (number or text)")
    sub.add_parser("show-settings", help="print current runtime settings")
    sub.add_parser("run-bot", help="run the Telegram admin bot (needs BOT_TOKEN etc.)")

    args = parser.parse_args(argv)
    try:
        # Load .env from the repo root (secrets stay in .env — never in Git).
        load_env_file(REPO_ROOT / ".env")
        # The dev CLI does not need Telegram secrets; the Sprint 3 bot will
        # require them (require_secrets=True).
        settings = Settings.from_env(require_secrets=False)
        # Every DB-using command gets migrations applied first (idempotent) —
        # this prevents "no such table" on a fresh checkout (user bug 2026-10-16).
        _init_db(settings, quiet=(args.command != "init-db"))
        if args.command == "init-db":
            return 0
        if args.command == "add-character":
            return asyncio.run(_add_character(settings, args))
        if args.command == "mock-generate":
            return asyncio.run(_mock_generate(settings, args))
        if args.command == "live-smoke":
            return asyncio.run(_live_smoke(settings, args))
        if args.command == "set-setting":
            return asyncio.run(_set_setting(settings, args))
        if args.command == "show-settings":
            return asyncio.run(_show_settings(settings, args))
        if args.command == "run-bot":
            # The bot requires full Telegram secrets (the dev CLI above does not).
            from waifubot.bot.app import run_bot

            return run_bot(Settings.from_env(require_secrets=True))
    except WaifuBotError as e:
        print(f"error [{e.code}]: {e}", file=sys.stderr)
        return 2
    except SQLAlchemyError as e:
        print(
            f"database error: {type(e).__name__}. "
            "Try: PYTHONPATH=src python3 -m waifubot.cli init-db",
            file=sys.stderr,
        )
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
