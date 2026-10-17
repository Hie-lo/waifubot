"""Tests for typed admin settings (SPEC §4: type, range, default; hard limits win)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from waifubot.core.admin_settings import (
    SETTINGS_SPECS,
    SPECS_BY_KEY,
    AdminHardLimits,
    AdminSettings,
)
from waifubot.db import models  # noqa: F401
from waifubot.db.base import Base
from waifubot.db.session import make_engine, make_session_factory
from waifubot.errors import ConfigError


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


@pytest.fixture()
def settings(tmp_path: Path) -> AdminSettings:  # noqa: ANN201
    async def setup():  # noqa: ANN202
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, make_session_factory(engine)

    engine, factory = _run(setup())
    svc = AdminSettings(factory, AdminHardLimits(max_images_per_job=8, max_jobs_per_day=30))
    yield svc
    _run(engine.dispose())


def test_defaults_match_specs(settings: AdminSettings) -> None:
    async def body() -> None:
        values = await settings.all_settings()
        assert values["default_style"] == "anime"
        assert values["style_lock"]
        for spec in SETTINGS_SPECS:
            assert values[spec.key] == spec.default

    _run(body())


def test_set_get_roundtrip(settings: AdminSettings) -> None:
    async def body() -> None:
        await settings.set("style_lock", "watercolor style, soft lines", updated_by="admin")
        assert await settings.get("style_lock") == "watercolor style, soft lines"
        await settings.set("default_style", "")
        assert await settings.get("default_style") == ""

    _run(body())


def test_unknown_key_rejected(settings: AdminSettings) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        settings.validate("mystery", 1)


def test_type_enforced(settings: AdminSettings) -> None:
    with pytest.raises(ConfigError, match="integer"):
        settings.validate("images_per_job", "four")
    with pytest.raises(ConfigError, match="string"):
        settings.validate("default_style", 7)


def test_range_enforced(settings: AdminSettings) -> None:
    with pytest.raises(ConfigError, match=">="):
        settings.validate("posting_interval_s", 5)
    with pytest.raises(ConfigError, match="at most 500"):
        settings.validate("style_lock", "x" * 501)


def test_db_value_cannot_exceed_env_hard_limit(settings: AdminSettings) -> None:
    # spec max for images_per_job is 20 but the env hard cap here is 8
    with pytest.raises(ConfigError, match="hard limit"):
        settings.validate("images_per_job", 9)
    with pytest.raises(ConfigError, match="hard limit"):
        settings.validate("daily_cap", 31)
    assert settings.validate("images_per_job", 8) == 8


def test_art_style_is_changeable(settings: AdminSettings) -> None:
    async def body() -> None:
        await settings.set("default_style", "no_style")
        assert await settings.get("default_style") == "no_style"
        assert "default_style" in SPECS_BY_KEY

    _run(body())


def test_get_falls_back_to_default_on_invalid_stored_value(settings: AdminSettings) -> None:
    # Review 2026-10-16: a stale/corrupt row must never break the bot.
    async def body() -> None:
        async with settings._sessions() as s:  # noqa: SLF001
            s.add(models.Setting(key="images_per_job", value_json='"not-an-int"'))
            await s.commit()
        assert await settings.get("images_per_job") == 4
        async with settings._sessions() as s:  # noqa: SLF001
            s.add(models.Setting(key="daily_cap", value_json="{broken json"))
            await s.commit()
        assert await settings.get("daily_cap") == 20

    _run(body())


def test_get_or_prefers_stored_value_then_fallback(settings: AdminSettings) -> None:
    async def body() -> None:
        # No row -> fallback (e.g. env default) is used and validated.
        # (fixture hard max for daily_cap is 30)
        assert await settings.get_or("daily_cap", 25) == 25
        await settings.set("daily_cap", 20)
        assert await settings.get_or("daily_cap", 25) == 20
        # Invalid fallback raises only when no row exists (hard limits still win).
        with pytest.raises(ConfigError):
            await settings.get_or("images_per_job", 9999)

    _run(body())


def test_all_settings_survives_corrupt_row(settings: AdminSettings) -> None:
    """Review fix 2026-10-16: /settings must never break on a corrupt stored row."""

    async def body() -> None:
        from waifubot.db.models import Setting

        async with settings._sessions() as s:  # noqa: SLF001
            s.add(Setting(key="daily_cap", value_json="{not json"))
            await s.commit()
        out = await settings.all_settings()
        assert out["daily_cap"] == 20  # spec default — resilient read
        assert out["images_per_job"] == 4

    _run(body())


def test_default_hard_limits_fully_changeable_by_admin(tmp_path: Path) -> None:
    """USER RULE 2026-10-16: settings are fully changeable by the admin within
    their defined range; env values are defaults only, never clamps."""

    async def body() -> None:
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'd.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = make_session_factory(engine)
        admin = AdminSettings(factory)  # defaults: code ceilings 20 / 100
        assert await admin.set("daily_cap", 50) == 50
        assert await admin.set("images_per_job", 20) == 20
        assert await admin.get("daily_cap") == 50
        # only the code safety ceiling still applies
        with pytest.raises(ConfigError, match="hard limit"):
            await admin.set("daily_cap", 101)
        await engine.dispose()

    _run(body())
