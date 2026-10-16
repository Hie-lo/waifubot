"""Locale loading and startup key check (SPEC §11: fa.yaml, fail on missing keys)."""
from __future__ import annotations

from pathlib import Path

import yaml

from waifubot.errors import LocaleError

# Every user-visible string must come from locales/fa.yaml (SPEC §11); these are
# ALL keys the Sprint 3 bot uses. The startup check fails on any missing key.
REQUIRED_KEYS: tuple[str, ...] = (
    "app_name",
    "welcome",
    "help",
    "status",
    "error_generic",
    "not_authorized",
    "generating",
    "generation_done",
    "generation_failed",
    "review_approve",
    "review_reject",
    "save_character",
    "published",
    "status_running",
    "status_paused",
    "status_queue",
    "status_counts",
    "status_last_errors",
    "status_none",
    "wiz_style_ask",
    "wiz_character_ask",
    "wiz_character_oneoff",
    "wiz_identity_ask",
    "wiz_count_ask",
    "wiz_confirm_ask",
    "wiz_confirm_yes",
    "wiz_cancel",
    "wiz_cancelled",
    "wiz_created",
    "wiz_random",
    "wiz_stale",
    "inbox_empty",
    "inbox_item",
    "review_approved",
    "review_rejected",
    "review_stale",
    "savec_name_ask",
    "savec_identity_ask",
    "savec_saved",
    "savec_cancelled",
    "savec_invalid",
    "pool_empty",
    "pool_item",
    "publish_pick",
    "publish_done",
    "publish_failed",
    "publish_unknown",
    "publish_unknown_sent",
    "publish_unknown_not",
    "publish_resolved_sent",
    "publish_resolved_not",
    "publish_already",
    "publish_stale",
    "publish_too_large",
    "settings_title",
    "settings_item",
    "settings_ask_value",
    "settings_saved",
    "settings_invalid",
    "settings_stale",
    "paused",
    "resumed",
    "characters_empty",
    "characters_item",
    "queue_empty",
    "queue_item",
    "post_caption",
    "delete_ask",
    "delete_yes",
    "delete_done",
    "delete_stale",
    "disk_low",
    "breaker_tripped",
    "interrupted_notice",
)


def load_locale(path: Path) -> dict[str, str]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise LocaleError(f"cannot read locale file {path}: {e}") from e
    except yaml.YAMLError as e:
        raise LocaleError(f"malformed locale YAML {path}: {e}") from e
    if not isinstance(raw, dict) or not raw:
        raise LocaleError(f"locale file {path} must be a non-empty mapping")
    data: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise LocaleError(f"locale file {path} must contain only string keys and values")
        data[key] = value
    return data


def check_required_keys(data: dict[str, str], required: tuple[str, ...] = REQUIRED_KEYS) -> None:
    missing = [key for key in required if key not in data]
    if missing:
        raise LocaleError(f"locale is missing required keys: {', '.join(missing)}")


def t(data: dict[str, str], msg_key: str, **fmt: object) -> str:
    """Format a user-visible string from the locale catalog (never hardcode)."""
    template = data.get(msg_key)
    if template is None:
        raise LocaleError(f"locale key not found: {msg_key}")
    try:
        return template.format(**fmt)
    except (KeyError, IndexError) as e:
        raise LocaleError(f"locale key {msg_key!r} has a bad placeholder: {e}") from e
