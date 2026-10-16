"""Configuration validation and hard-limit tests (SPEC §4 settings rules)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waifubot.config import (
    HARD_MAX_IMAGES_PER_JOB,
    HARD_MAX_JOBS_PER_DAY,
    Settings,
    ensure_runtime_dirs,
    load_env_file,
)
from waifubot.errors import ConfigError

BASE_ENV = {
    "BOT_TOKEN": "123:dummy-token-for-tests",
    "ADMIN_USER_IDS": "111,222",
    "DESTINATION_CHAT_ID": "-1001234567890",
}


def test_valid_env_loads() -> None:
    s = Settings.from_env(BASE_ENV)
    assert s.admin_user_ids == (111, 222)
    assert s.max_images_per_job == 4
    assert s.max_jobs_per_day == 20
    assert s.allow_nsfw is False
    assert s.bot_token == "123:dummy-token-for-tests"
    assert "dummy" not in repr(s)  # secrets never in repr


@pytest.mark.parametrize("missing", ["BOT_TOKEN", "ADMIN_USER_IDS", "DESTINATION_CHAT_ID"])
def test_missing_required_fails(missing: str) -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_cli_mode_tolerates_missing_secrets() -> None:
    s = Settings.from_env({}, require_secrets=False)
    assert s.bot_token == ""
    assert s.admin_user_ids == ()
    assert s.destination_chat_id == ""
    # ...but present values are still validated
    with pytest.raises(ConfigError, match="boolean"):
        Settings.from_env({"ALLOW_NSFW": "maybe"}, require_secrets=False)


def test_load_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_TOKEN=123:from-dotenv\nADMIN_USER_IDS=42\nDESTINATION_CHAT_ID=-1001\n"
        "MAX_IMAGES_PER_JOB=3\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAX_IMAGES_PER_JOB", raising=False)
    assert load_env_file(env_file) is True
    s = Settings.from_env()
    assert s.bot_token == "123:from-dotenv"
    assert s.max_images_per_job == 3
    assert load_env_file(tmp_path / "missing.env") is False


def test_allow_nsfw_is_an_admin_switch() -> None:
    # USER DECISION 2026-10-16: NSFW may be enabled — it is a switch, not an error.
    s = Settings.from_env({**BASE_ENV, "ALLOW_NSFW": "true"})
    assert s.allow_nsfw is True
    s2 = Settings.from_env({**BASE_ENV, "ALLOW_NSFW": "false"})
    assert s2.allow_nsfw is False


def test_images_per_job_hard_max() -> None:
    with pytest.raises(ConfigError, match="MAX_IMAGES_PER_JOB"):
        Settings.from_env({**BASE_ENV, "MAX_IMAGES_PER_JOB": str(HARD_MAX_IMAGES_PER_JOB + 1)})


def test_jobs_per_day_hard_max() -> None:
    with pytest.raises(ConfigError, match="MAX_JOBS_PER_DAY"):
        Settings.from_env({**BASE_ENV, "MAX_JOBS_PER_DAY": str(HARD_MAX_JOBS_PER_DAY + 1)})


def test_bad_int_fails() -> None:
    with pytest.raises(ConfigError, match="integer"):
        Settings.from_env({**BASE_ENV, "MAX_IMAGES_PER_JOB": "many"})


def test_bad_bool_fails() -> None:
    with pytest.raises(ConfigError, match="boolean"):
        Settings.from_env({**BASE_ENV, "PROVIDER_ENABLED": "maybe"})


def test_bad_admin_ids_fails() -> None:
    with pytest.raises(ConfigError, match="ADMIN_USER_IDS"):
        Settings.from_env({**BASE_ENV, "ADMIN_USER_IDS": "12,ab"})


def test_bad_log_level_fails() -> None:
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        Settings.from_env({**BASE_ENV, "LOG_LEVEL": "LOUD"})


def test_ensure_runtime_dirs(tmp_path: Path) -> None:
    s = Settings.from_env({**BASE_ENV, "STORAGE_ROOT": str(tmp_path / "st")})
    ensure_runtime_dirs(s)
    assert (tmp_path / "st" / "images").is_dir()
    assert (tmp_path / "st" / "temp").is_dir()
