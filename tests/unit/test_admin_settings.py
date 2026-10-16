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
