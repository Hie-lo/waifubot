"""Tag pool loading tests (SPEC §6: malformed YAML fails loudly)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waifubot.core.tags_pool import load_tags_pool
from waifubot.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
POOL_PATH = REPO_ROOT / "config" / "tags_pool.yaml"


def test_repo_pool_loads_and_is_complete() -> None:
    pool = load_tags_pool(POOL_PATH)
    assert pool.template_version == "prompt-v1"
    assert pool.style_lock
    assert pool.negative_prompt
    for category in ("pose", "expression", "outfit", "background", "camera", "lighting"):
        assert category in pool.categories, category
        assert pool.categories[category]
    # compatibility lists reference known ids
    assert pool.all_ids()


def test_malformed_yaml_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "pool.yaml"
    bad.write_text("categories: [broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed"):
        load_tags_pool(bad)


def test_missing_categories_fails(tmp_path: Path) -> None:
    bad = tmp_path / "pool.yaml"
    bad.write_text("version: '1'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="categories"):
        load_tags_pool(bad)


def test_duplicate_ids_fail(tmp_path: Path) -> None:
    bad = tmp_path / "pool.yaml"
    bad.write_text(
        """
version: "1"
template_version: "t"
style_lock: "s"
quality_fragment: "q"
negative_prompt: "n"
categories:
  pose:
    - {id: pose.a, label: A, fragment: a}
    - {id: pose.a, label: B, fragment: b}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_tags_pool(bad)


def test_unknown_incompatible_id_fails(tmp_path: Path) -> None:
    bad = tmp_path / "pool.yaml"
    bad.write_text(
        """
version: "1"
template_version: "t"
style_lock: "s"
quality_fragment: "q"
negative_prompt: "n"
categories:
  pose:
    - {id: pose.a, label: A, fragment: a, incompatible_with: [pose.missing]}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown incompatible"):
        load_tags_pool(bad)


def test_bad_weight_fails(tmp_path: Path) -> None:
    bad = tmp_path / "pool.yaml"
    bad.write_text(
        """
version: "1"
template_version: "t"
style_lock: "s"
quality_fragment: "q"
negative_prompt: "n"
categories:
  pose:
    - {id: pose.a, label: A, fragment: a, weight: -3}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="weight"):
        load_tags_pool(bad)
