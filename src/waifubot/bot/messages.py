"""Locale catalog for the bot (SPEC §11): every user-visible string comes from
locales/fa.yaml; the startup check fails on missing keys. Logs stay in English.
"""
from __future__ import annotations

from pathlib import Path

from waifubot.errors import LocaleError
from waifubot.locale import check_required_keys, load_locale, t


class Messages:
    def __init__(self, locales_dir: Path, lang: str = "fa") -> None:
        self._lang = lang if lang in ("fa", "en") else "fa"
        self._fa = load_locale(locales_dir / "fa.yaml")
        self._en = load_locale(locales_dir / "en.yaml") if (
            locales_dir / "en.yaml"
        ).is_file() else {}
        # Startup check (SPEC §11): fail loudly on missing keys.
        check_required_keys(self._fa)
        if self._en:
            check_required_keys(self._en)

    @property
    def lang(self) -> str:
        return self._lang

    def t(self, msg_key: str, **fmt: object) -> str:
        data = self._en if (self._lang == "en" and self._en) else self._fa
        try:
            return t(data, msg_key, **fmt)
        except LocaleError:
            if data is not self._fa:
                return t(self._fa, msg_key, **fmt)
            raise
