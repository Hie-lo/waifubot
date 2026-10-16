"""Locale loading and startup key check (SPEC §11: fa.yaml, fail on missing keys)."""
from __future__ import annotations

from pathlib import Path

import yaml

from waifubot.errors import LocaleError

# Every user-visible string must come from locales/fa.yaml; these keys are the
# skeleton the app needs at startup. Grow this list with each sprint's UI.
REQUIRED_KEYS: tuple[str, ...] = (
    "app_name",
    "welcome",
    "help",
    "status",
    "error_generic",
    "not_authorized",
    "generating",
    "generation_done",
    "review_approve",
    "review_reject",
    "save_character",
    "published",
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
