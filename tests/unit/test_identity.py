"""Identity pool tests (user request 2026-10-16): diverse random characters
and precise face prompts for saved characters."""
from __future__ import annotations

import random
from pathlib import Path

from waifubot.core.identity import (
    TRAIT_ORDER,
    assemble_identity,
    extract_traits,
    load_identity_pool,
    random_identity,
)
from waifubot.core.providers.perchance import snap_to_site_count

REPO_ROOT = Path(__file__).resolve().parents[2]
POOL = load_identity_pool(REPO_ROOT / "config" / "identity_pool.yaml")


def test_random_identity_is_diverse() -> None:
    ids = {random_identity(POOL, random.Random(i)) for i in range(12)}
    assert len(ids) >= 10  # 12 draws must not collapse to one "blue hair" look
    for identity in ids:
        assert "hair" in identity and "eyes" in identity


def test_assemble_identity_canonical_order_and_face_anchors() -> None:
    traits = {
        "age": "25",
        "hair_color": "black",
        "hair_style": "ponytail",
        "eye_color": "green",
        "skin": "fair",
        "face": "oval",
    }
    out = assemble_identity(POOL, traits, extras="red dress", precise=True)
    # Stage-2 professional order: age, height, face, eyes, skin, hair, body
    assert out == (
        "25 years old, oval face, green eyes, fair skin, "
        "black ponytail hair, red dress, detailed face, consistent facial features"
    )
    # with a name + body traits the order is name, age, height, face ... body
    named = assemble_identity(
        POOL,
        {"age": "25", "height": "165", "face": "oval", "eye_color": "green",
         "skin": "fair", "hair_color": "black", "hair_style": "ponytail",
         "body_type": "slim", "body_proportions": "average"},
        extras="", precise=False, name="Sakura",
    )
    assert named.startswith("Sakura, 25 years old, 165cm tall, oval face")
    assert "slim body" in named and "average" in named
    # free-hand identities never gain invented anchors (exact preservation)
    assert "consistent facial features" not in assemble_identity(POOL, traits)


def test_extract_traits_reads_only_exact_pool_values() -> None:
    found = extract_traits(
        POOL, "28 years old, long wavy blonde hair, hazel eyes, tan skin"
    )
    assert found["age"] == "28"
    assert found["hair_style"] == "long wavy"
    assert found["hair_color"] == "blonde"
    assert found["eye_color"] == "hazel"
    assert found["skin"] == "tan"
    assert "face" not in found  # not stated -> never invented


def test_snap_to_site_count_matches_site_options() -> None:
    # the site offers 2/4/6/8/16/32 (user report 2026-10-16) — round UP
    assert snap_to_site_count(1) == 2
    assert snap_to_site_count(3) == 4
    assert snap_to_site_count(4) == 4
    assert snap_to_site_count(5) == 6
    assert snap_to_site_count(16) == 16
    assert snap_to_site_count(20) == 32
    assert snap_to_site_count(32) == 32


def test_all_trait_labels_present() -> None:
    for trait in TRAIT_ORDER:
        for tv in POOL.values(trait):
            assert tv.fa and tv.en
