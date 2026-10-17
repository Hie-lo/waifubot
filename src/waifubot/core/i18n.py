"""Internationalization (i18n) translation helper (R33).

Provides `t(key, locale="fa", **kwargs)` for localized text rendering with parity enforcement.
"""

from pathlib import Path
from typing import Any
import yaml

_TRANSLATIONS: dict[str, dict[str, str]] = {}


def _load_locales() -> None:
    global _TRANSLATIONS
    if _TRANSLATIONS:
        return

    # Find locales directory relative to project root
    current = Path(__file__).resolve()
    # Go up from src/waifubot/core to root
    project_root = current.parents[3]
    locales_dir = project_root / "locales"

    for lang in ("fa", "en"):
        file_path = locales_dir / f"{lang}.yaml"
        if file_path.is_file():
            with open(file_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
                _TRANSLATIONS[lang] = {str(k): str(v) for k, v in loaded.items()}
        else:
            _TRANSLATIONS[lang] = {}


def reload_locales() -> None:
    """Forces reloading of locale YAML files."""
    global _TRANSLATIONS
    _TRANSLATIONS.clear()
    _load_locales()


def t(key: str, locale: str = "fa", **kwargs: Any) -> str:
    """Translates a key into the target locale, formatting any placeholders."""
    _load_locales()
    lang_dict = _TRANSLATIONS.get(locale, _TRANSLATIONS.get("fa", {}))
    template = lang_dict.get(key)
    if template is None:
        # Fallback to English
        template = _TRANSLATIONS.get("en", {}).get(key, key)

    try:
        return template.format(**kwargs)
    except Exception:
        return template
