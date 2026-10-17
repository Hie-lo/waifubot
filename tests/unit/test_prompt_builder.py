"""Prompt builder tests (SPEC §6): deterministic, structure, cap, safety.

Golden snapshot: `tests/goldens/prompt_seed42.json` pins the exact prompt for a
fixed identity + seed. If the pool or template changes intentionally, regenerate
the golden file and explain the change in the commit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waifubot.core.content_safety import load_blocklist
from waifubot.core.prompt_builder import MAX_PROMPT_CHARS, PromptBuilder
from waifubot.core.tags_pool import load_tags_pool
from waifubot.errors import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = Path(__file__).resolve().parents[1] / "goldens" / "prompt_seed42.json"
IDENTITY = "adult woman, 25 years old, long blue hair, red eyes"


@pytest.fixture()
def builder() -> PromptBuilder:
    pool = load_tags_pool(REPO_ROOT / "config" / "tags_pool.yaml")
    blocklist = load_blocklist(REPO_ROOT / "config" / "blocklist.yaml")
    return PromptBuilder(pool, blocklist)


def test_deterministic_for_same_seed(builder: PromptBuilder) -> None:
    a = builder.build(identity=IDENTITY, seed=42)
    b = builder.build(identity=IDENTITY, seed=42)
    assert a == b
    assert a.seed == 42


def test_different_seeds_can_differ(builder: PromptBuilder) -> None:
    prompts = {builder.build(identity=IDENTITY, seed=s).prompt for s in range(1, 8)}
    assert len(prompts) > 1


def test_identity_is_preserved_exactly(builder: PromptBuilder) -> None:
    result = builder.build(identity=IDENTITY, seed=7)
    assert result.prompt.startswith(IDENTITY + ", ")


def test_style_lock_position_is_fixed(builder: PromptBuilder) -> None:
    result = builder.build(identity=IDENTITY, seed=7)
    assert result.prompt.startswith(f"{IDENTITY}, {builder.style_lock}, ")


def test_custom_style_lock(builder: PromptBuilder) -> None:
    result = builder.build(identity=IDENTITY, seed=7, style_lock="watercolor style")
    assert result.prompt.startswith(f"{IDENTITY}, watercolor style, ")


def test_admin_text_is_appended_before_quality(builder: PromptBuilder) -> None:
    result = builder.build(identity=IDENTITY, seed=7, admin_text="holding a red umbrella")
    assert "holding a red umbrella" in result.prompt
    assert result.prompt.index("holding a red umbrella") < result.prompt.index("masterpiece")
    assert result.prompt.endswith("masterpiece, best quality, high detail")


def test_prompt_cap_enforced(builder: PromptBuilder) -> None:
    # Review 2026-10-16: the cap is deterministic — never fail the job; drop the
    # lowest-priority tail parts (quality/admin/scene) and keep identity+style.
    result = builder.build(identity=IDENTITY, seed=7, admin_text="x" * MAX_PROMPT_CHARS)
    assert len(result.prompt) <= MAX_PROMPT_CHARS
    assert result.prompt.startswith(IDENTITY)
    assert "xxx" not in result.prompt  # oversized admin text dropped, job still built


def test_long_identity_survives_the_cap(builder: PromptBuilder) -> None:
    long_identity = "adult woman, 25 years old, " + "x" * 2010  # > 2048 with style
    result = builder.build(identity=long_identity, seed=7)
    assert len(result.prompt) == MAX_PROMPT_CHARS  # hard-truncated at the cap
    assert result.prompt.startswith("adult woman, 25 years old, xxx")  # identity kept


def test_blocklist_runs_on_final_prompt(builder: PromptBuilder) -> None:
    with pytest.raises(ValidationError, match="blocked term"):
        builder.build(identity=IDENTITY, seed=7, admin_text="loli")


def test_preset_ids_recorded(builder: PromptBuilder) -> None:
    result = builder.build(identity=IDENTITY, seed=42)
    assert result.preset_ids
    assert all(pid.startswith(("pose.", "expr.", "outfit.", "background.", "camera.", "lighting."))
               for pid in result.preset_ids)


def test_golden_snapshot(builder: PromptBuilder) -> None:
    result = builder.build(identity=IDENTITY, seed=42)
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert result.prompt == golden["prompt"]
    assert list(result.preset_ids) == golden["preset_ids"]
    assert result.template_version == golden["template_version"]
    assert result.negative_prompt == golden["negative_prompt"]
