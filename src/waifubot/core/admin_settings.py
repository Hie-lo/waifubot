"""Admin-tunable settings (SPEC §4): typed values in the `settings` table, each
with a code-defined type, range, and default. The Sprint 3 `/settings` command
exposes ALL of these in the bot (including art style), per the user's decision
(2026-10-16): every option must be changeable from the bot itself.

A database value can NEVER override a hard limit from the environment
(SPEC §4): `images_per_job` is bounded by MAX_IMAGES_PER_JOB and `daily_cap`
by MAX_JOBS_PER_DAY.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waifubot.db.models import Setting
from waifubot.errors import ConfigError


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: str  # "int" | "str"
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    description: str = ""


SETTINGS_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("default_style", "str", "anime",
                description="provider style key (mapped per provider; '' = no style)"),
    SettingSpec("style_lock", "str", "anime style, consistent character design",
                description="in-prompt art style definition (kept separate from scene)"),
    SettingSpec("images_per_job", "int", 4, 1, 20,
                description="images requested per generation job"),
    SettingSpec("daily_cap", "int", 20, 1, 100,
                description="max automatic jobs per day"),
    SettingSpec("posting_interval_s", "int", 3600, 60, 86400,
                description="scheduler posting interval (Sprint 4)"),
    SettingSpec("retention_days", "int", 30, 1, 365,
                description="retention for final images (Sprint 4 cleanup)"),
)

SPECS_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SETTINGS_SPECS}


@dataclass(frozen=True)
class AdminHardLimits:
    """Env hard caps; DB values may never exceed these."""

    max_images_per_job: int = 20
    max_jobs_per_day: int = 100


class AdminSettings:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hard: AdminHardLimits | None = None,
    ) -> None:
        self._sessions = session_factory
        self._hard = hard or AdminHardLimits()

    def _hard_max(self, key: str) -> int | None:
        if key == "images_per_job":
            return self._hard.max_images_per_job
        if key == "daily_cap":
            return self._hard.max_jobs_per_day
        return None

    def validate(self, key: str, value: Any) -> Any:
        spec = SPECS_BY_KEY.get(key)
        if spec is None:
            raise ConfigError(f"unknown setting {key!r}; known: {sorted(SPECS_BY_KEY)}")
        if spec.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{key} must be an integer")
            if spec.minimum is not None and value < spec.minimum:
                raise ConfigError(f"{key} must be >= {spec.minimum}")
            hard_max = self._hard_max(key)
            top = min(x for x in (spec.maximum, hard_max) if x is not None)
            if value > top:
                raise ConfigError(f"{key} must be <= {top} (hard limit)")
            return value
        if not isinstance(value, str):
            raise ConfigError(f"{key} must be a string")
        if len(value) > 500:
            raise ConfigError(f"{key} must be at most 500 characters")
        return value

    async def get(self, key: str, session: AsyncSession | None = None) -> Any:
        spec = SPECS_BY_KEY.get(key)
        if spec is None:
            raise ConfigError(f"unknown setting {key!r}")

        async def _read(s: AsyncSession) -> Any:
            row = await s.get(Setting, key)
            if row is None:
                return spec.default
            # Resilient read (review 2026-10-16): a stale/invalid stored value
            # must never break the bot — fall back to the default.
            try:
                return self.validate(key, json.loads(row.value_json))
            except (ConfigError, TypeError, ValueError):
                return spec.default

        if session is not None:
            return await _read(session)
        async with self._sessions() as s:
            return await _read(s)

    async def get_or(self, key: str, fallback: Any, session: AsyncSession | None = None) -> Any:
        """Like get(), but `fallback` replaces the spec default when no row exists.
        Priority (user rule 2026-10-16, runtime-changeable options): stored bot
        setting > env/config default > code default; hard limits still clamp."""
        spec = SPECS_BY_KEY.get(key)
        if spec is None:
            raise ConfigError(f"unknown setting {key!r}")
        if session is not None:
            row = await session.get(Setting, key)
        else:
            async with self._sessions() as s:
                row = await s.get(Setting, key)
        if row is None:
            return self.validate(key, fallback)
        try:
            return self.validate(key, json.loads(row.value_json))
        except (ConfigError, TypeError, ValueError):
            return self.validate(key, fallback)

    async def set(self, key: str, value: Any, updated_by: str = "admin") -> Any:
        value = self.validate(key, value)
        async with self._sessions() as s:
            row = await s.get(Setting, key)
            if row is None:
                row = Setting(key=key, value_json=json.dumps(value), updated_by=updated_by)
                s.add(row)
            else:
                row.value_json = json.dumps(value)
                row.updated_by = updated_by
            await s.commit()
        return value

    async def all_settings(self) -> dict[str, Any]:
        out: dict[str, Any] = {spec.key: spec.default for spec in SETTINGS_SPECS}
        async with self._sessions() as s:
            for spec in SETTINGS_SPECS:
                row = await s.get(Setting, spec.key)
                if row is not None:
                    out[spec.key] = self.validate(spec.key, json.loads(row.value_json))
        return out
