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


def test_identity_without_adult_words_accepted() -> None:
    """USER RULE 2026-10-16: no 'adult' framing is required anymore; only the
    minor-protection checks (blocked terms, stated minor age) remain."""
    validate_adult_identity("elf girl, silver hair, green eyes", ("loli",))
    validate_adult_identity("25 years old, black ponytail hair", ())


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


def test_minor_age_hyphen_form_rejected() -> None:
    # Review 2026-10-16: "17-year-old" used to slip past the regex.
    with pytest.raises(ValidationError, match="minor"):
        validate_adult_identity("adult woman, 17-year-old, blue hair", ())


def test_adult_age_hyphen_form_accepted() -> None:
    validate_adult_identity("adult woman, 25-year-old, blue hair", ())
