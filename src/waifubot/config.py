"""Environment configuration with code-enforced hard maximums (SPEC §4, §12).

Secrets and hard limits come from environment variables. A database value can
never override a hard limit or enable a disabled provider.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from waifubot.errors import ConfigError

# Hard maximums — enforced in code, never overridable by config or DB.
HARD_MAX_IMAGES_PER_JOB = 20
HARD_MAX_JOBS_PER_DAY = 100
# NSFW switch: admin-controlled via ALLOW_NSFW (USER DECISION 2026-10-16 — the
# SFW-only MVP gate is removed per the user's instruction; user word supersedes
# the project files). The minor-protection checks in core/content_safety.py
# (adult-identity validation + minor-coded blocklist) remain mandatory.
# No Telegram override: this is an environment setting only.

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _parse_bool(raw: str, name: str) -> bool:
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _parse_int(raw: str, name: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as e:
            raise ConfigError(
                f"ADMIN_USER_IDS must be comma-separated integers, got {raw!r}"
            ) from e
    return tuple(ids)


def _require(env: Mapping[str, str], name: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def load_env_file(path: Path) -> bool:
    """Load a .env file into the process environment (existing vars win).

    Returns True if the file was loaded. Secrets live only in .env — never in Git.
    """
    if not path.is_file():
        return False
    from dotenv import load_dotenv

    load_dotenv(path, override=False)
    return True


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False, default="")
    admin_user_ids: tuple[int, ...] = ()
    destination_chat_id: str = ""
    provider_enabled: bool = True
    provider_name: str = "perchance_playwright"
    provider_page_url: str = "https://perchance.org/ai-text-to-image-generator"
    provider_headless: bool = False
    allow_nsfw: bool = False
    max_images_per_job: int = 4
    max_jobs_per_day: int = 20
    storage_root: Path = Path("./storage")
    db_url: str = "sqlite+aiosqlite:///./storage/waifubot.db"
    disk_floor_bytes: int = 2 * 1024**3
    log_level: str = "INFO"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, require_secrets: bool = True
    ) -> Settings:
        """Load settings from the environment.

        ``require_secrets=True`` (the Telegram bot): BOT_TOKEN, ADMIN_USER_IDS and
        DESTINATION_CHAT_ID must be present. ``False`` (the dev CLI): they may be
        empty — provider/mock commands do not need Telegram secrets. Any value
        that IS present is still strictly validated.
        """
        e: Mapping[str, str] = os.environ if env is None else env

        if require_secrets:
            bot_token = _require(e, "BOT_TOKEN")
            admin_user_ids = _parse_admin_ids(_require(e, "ADMIN_USER_IDS"))
            destination_chat_id = _require(e, "DESTINATION_CHAT_ID")
        else:
            bot_token = (e.get("BOT_TOKEN") or "").strip()
            raw_ids = (e.get("ADMIN_USER_IDS") or "").strip()
            admin_user_ids = _parse_admin_ids(raw_ids) if raw_ids else ()
            destination_chat_id = (e.get("DESTINATION_CHAT_ID") or "").strip()
        if require_secrets and not admin_user_ids:
            raise ConfigError("ADMIN_USER_IDS must contain at least one id")

        allow_nsfw = _parse_bool(e.get("ALLOW_NSFW", "false"), "ALLOW_NSFW")
        # USER DECISION 2026-10-16: ALLOW_NSFW may be enabled — it is an admin
        # switch, not an error. (The minor-protection content checks still run.)

        max_images_per_job = _parse_int(e.get("MAX_IMAGES_PER_JOB", "4"), "MAX_IMAGES_PER_JOB")
        if not 1 <= max_images_per_job <= HARD_MAX_IMAGES_PER_JOB:
            raise ConfigError(
                f"MAX_IMAGES_PER_JOB must be between 1 and {HARD_MAX_IMAGES_PER_JOB}"
            )
        max_jobs_per_day = _parse_int(e.get("MAX_JOBS_PER_DAY", "20"), "MAX_JOBS_PER_DAY")
        if not 1 <= max_jobs_per_day <= HARD_MAX_JOBS_PER_DAY:
            raise ConfigError(f"MAX_JOBS_PER_DAY must be between 1 and {HARD_MAX_JOBS_PER_DAY}")

        disk_floor_bytes = _parse_int(e.get("DISK_FLOOR_BYTES", "2147483648"), "DISK_FLOOR_BYTES")
        if disk_floor_bytes < 0:
            raise ConfigError("DISK_FLOOR_BYTES must be >= 0")

        log_level = e.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL must be one of {_LOG_LEVELS}, got {log_level!r}")

        return cls(
            bot_token=bot_token,
            admin_user_ids=admin_user_ids,
            destination_chat_id=destination_chat_id,
            provider_enabled=_parse_bool(e.get("PROVIDER_ENABLED", "true"), "PROVIDER_ENABLED"),
            provider_name=e.get("PROVIDER_NAME", "perchance_playwright").strip(),
            provider_page_url=e.get(
                "PROVIDER_PAGE_URL", "https://perchance.org/ai-text-to-image-generator"
            ).strip(),
            provider_headless=_parse_bool(e.get("PROVIDER_HEADLESS", "false"), "PROVIDER_HEADLESS"),
            allow_nsfw=allow_nsfw,
            max_images_per_job=max_images_per_job,
            max_jobs_per_day=max_jobs_per_day,
            storage_root=Path(e.get("STORAGE_ROOT", "./storage").strip()),
            db_url=e.get("DB_URL", "sqlite+aiosqlite:///./storage/waifubot.db").strip(),
            disk_floor_bytes=disk_floor_bytes,
            log_level=log_level,
        )


def ensure_runtime_dirs(settings: Settings) -> None:
    """Create runtime directories (SPEC §12). Never touches Git-tracked content."""
    (settings.storage_root / "images").mkdir(parents=True, exist_ok=True)
    (settings.storage_root / "temp").mkdir(parents=True, exist_ok=True)
