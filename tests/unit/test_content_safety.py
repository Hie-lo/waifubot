"""Content safety tests (SPEC §8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waifubot.core.content_safety import (
    check_blocklist,
    load_blocklist,
    validate_adult_identity,
)
from waifubot.errors import ConfigError, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
BLOCKLIST_PATH = REPO_ROOT / "config" / "blocklist.yaml"

ADULT = "adult woman, 25 years old, blue hair, red eyes"


def test_repo_blocklist_loads() -> None:
    terms = load_blocklist(BLOCKLIST_PATH)
    assert "loli" in terms
    assert len(terms) >= 5


def test_blocklist_must_have_terms(tmp_path: Path) -> None:
    bad = tmp_path / "b.yaml"
    bad.write_text("terms: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="at least one term"):
        load_blocklist(bad)


def test_adult_identity_accepted() -> None:
    validate_adult_identity(ADULT, load_blocklist(BLOCKLIST_PATH))


def test_identity_without_adult_words_rejected() -> None:
    with pytest.raises(ValidationError, match="adult"):
        validate_adult_identity("blue hair, red eyes", ("loli",))


def test_identity_with_minor_age_rejected() -> None:
    with pytest.raises(ValidationError, match="minor age"):
        validate_adult_identity("girl, 16 years old, blue hair", ("loli",))


def test_identity_with_blocked_term_rejected() -> None:
    with pytest.raises(ValidationError, match="blocked term"):
        validate_adult_identity("adult woman, 25 years old, loli style", ("loli",))


def test_empty_identity_rejected() -> None:
    with pytest.raises(ValidationError, match="empty"):
        validate_adult_identity("   ", ("loli",))


def test_final_prompt_blocklist_includes_admin_text() -> None:
    with pytest.raises(ValidationError, match="blocked term"):
        check_blocklist("adult woman, 25 years old, standing, little girl", ("little girl",))


def test_final_prompt_minor_age_rejected() -> None:
    with pytest.raises(ValidationError, match="minor age"):
        check_blocklist("adult woman, 17 years old, standing", ("loli",))


def test_clean_prompt_passes() -> None:
    check_blocklist("adult woman, 25 years old, standing, smiling", ("loli", "child"))
