"""Locale loader and startup key-check tests (SPEC §11)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waifubot.errors import LocaleError
from waifubot.locale import REQUIRED_KEYS, check_required_keys, load_locale

REPO_ROOT = Path(__file__).resolve().parents[2]
FA_YAML = REPO_ROOT / "locales" / "fa.yaml"


def test_fa_yaml_loads_and_satisfies_required_keys() -> None:
    data = load_locale(FA_YAML)
    check_required_keys(data)
    assert data["app_name"] == "WaifuBot"


def test_missing_key_fails_startup_check() -> None:
    with pytest.raises(LocaleError, match="missing required keys"):
        check_required_keys({"app_name": "x"})


def test_malformed_yaml_fails(tmp_path: Path) -> None:
    bad = tmp_path / "fa.yaml"
    bad.write_text("key: [unclosed", encoding="utf-8")
    with pytest.raises(LocaleError, match="malformed"):
        load_locale(bad)


def test_non_mapping_fails(tmp_path: Path) -> None:
    bad = tmp_path / "fa.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(LocaleError, match="mapping"):
        load_locale(bad)


def test_non_string_values_fail(tmp_path: Path) -> None:
    bad = tmp_path / "fa.yaml"
    bad.write_text("count: 3\n", encoding="utf-8")
    with pytest.raises(LocaleError, match="string"):
        load_locale(bad)


def test_required_keys_is_nonempty() -> None:
    assert len(REQUIRED_KEYS) >= 5
