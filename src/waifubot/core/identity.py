"""Identity building (user request 2026-10-16).

Two jobs:
1. `random_identity` — a DIVERSE random adult identity per generation (the old
   fallback was one hardcoded "long blue hair" woman, so every "random"
   character looked the same).
2. `assemble_identity` / `extract_traits` — the save-character flow builds a
   PRECISE, ordered identity prompt (hair, eyes, skin, face shape, age) so the
   same character keeps its face and details across later generations.

Rules that never change (SPEC §2.3 / AGENT.md):
- No forced adult framing (user 2026-10-16); the minor blocklist still applies
  at validation time (standing user rule: minor protection is mandatory).
- `extract_traits` only READS text; assembling a precise identity is an explicit
  user action in the savec wizard (never silently rewritten).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from waifubot.errors import ConfigError, ValidationError

# Wizard order; rendering below uses its own professional (canonical) order.
TRAIT_ORDER: tuple[str, ...] = (
    "age",
    "height",
    "hair_color",
    "hair_style",
    "eye_color",
    "skin",
    "face",
    "body_type",
    "body_proportions",
)

_TRAIT_KEYS: dict[str, str] = {
    "age": "ages",
    "height": "heights",
    "hair_color": "hair_colors",
    "hair_style": "hair_styles",
    "eye_color": "eye_colors",
    "skin": "skin_tones",
    "face": "face_shapes",
    "body_type": "body_types",
    "body_proportions": "body_proportions",
}

# numeric traits the user may type freely (validated, never invented)
NUMERIC_TRAITS: dict[str, tuple[int, int, str]] = {
    "age": (18, 100, "years old"),      # minor protection stays (18+)
    "height": (100, 220, "cm"),
}


def validate_trait_value(trait: str, value: str) -> str:
    """Validate one custom trait value. Numeric traits must be a whole number
    inside their range; everything else must be short non-empty text."""
    text = value.strip()
    if not text:
        raise ValidationError(f"{trait}: value must not be empty")
    if trait in NUMERIC_TRAITS:
        low, high, _unit = NUMERIC_TRAITS[trait]
        if not text.isdigit():
            raise ValidationError(f"{trait}: enter a whole number ({low}-{high})")
        n = int(text)
        if not low <= n <= high:
            raise ValidationError(f"{trait}: number must be between {low} and {high}")
        return str(n)
    if len(text) > 200:
        raise ValidationError(f"{trait}: value must be at most 200 characters")
    return text


@dataclass(frozen=True)
class TraitValue:
    value: str
    fa: str
    en: str


class IdentityPool:
    def __init__(self, data: dict[str, Any]) -> None:
        self.prefix: str = str(data.get("prefix") or "")
        self.face_anchors: str = str(data.get("face_anchors") or "")
        self.traits: dict[str, list[TraitValue]] = {}
        for trait, key in _TRAIT_KEYS.items():
            raw = data.get(key)
            if not isinstance(raw, list) or not raw:
                raise ConfigError(f"identity pool: {key} must be a non-empty list")
            values: list[TraitValue] = []
            for item in raw:
                if not isinstance(item, dict) or "value" not in item:
                    raise ConfigError(f"identity pool: bad entry in {key}")
                values.append(
                    TraitValue(
                        value=str(item["value"]),
                        fa=str(item.get("fa", item["value"])),
                        en=str(item.get("en", item["value"])),
                    )
                )
            self.traits[trait] = values

    def values(self, trait: str) -> list[TraitValue]:
        return self.traits[trait]

    def label(self, trait: str, value: str, lang: str) -> str:
        for tv in self.traits[trait]:
            if tv.value == value:
                return tv.fa if lang == "fa" else tv.en
        return value


def load_identity_pool(path: Path) -> IdentityPool:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"identity pool: cannot load {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"identity pool: {path} must be a mapping")
    return IdentityPool(data)


def assemble_identity(
    pool: IdentityPool,
    traits: dict[str, str],
    *,
    extras: str = "",
    precise: bool = False,
    name: str = "",
) -> str:
    """Build the identity prompt in PROFESSIONAL canonical order (user 2026-10-16:
    character records hold character details only, in a clean structure):
    name, age, height, face, eyes, skin, hair, body, proportions, extras.
    `traits` maps trait name -> final English prompt fragments."""
    parts: list[str] = [pool.prefix] if pool.prefix else []
    if name.strip():
        parts.append(name.strip())
    age = traits.get("age", "")
    if age:
        parts.append(f"{age} years old" if age.isdigit() else age)
    height = traits.get("height", "")
    if height:
        parts.append(f"{height}cm tall" if height.isdigit() else height)
    if traits.get("face"):
        parts.append(f"{traits['face']} face")
    if traits.get("eye_color"):
        parts.append(f"{traits['eye_color']} eyes")
    if traits.get("skin"):
        parts.append(f"{traits['skin']} skin")
    hair_color = traits.get("hair_color", "")
    hair_style = traits.get("hair_style", "")
    if hair_color or hair_style:
        parts.append(f"{hair_color} {hair_style} hair".strip())
    if traits.get("body_type"):
        parts.append(f"{traits['body_type']} body")
    if traits.get("body_proportions"):
        parts.append(traits["body_proportions"])
    if extras.strip():
        parts.append(extras.strip().strip(","))
    if precise and pool.face_anchors:
        parts.append(pool.face_anchors)
    return ", ".join(p for p in parts if p)


def known_character_prompt(name: str, anime: str, pool: IdentityPool) -> str:
    """A recognized character (from an anime/game): name + source anchor the
    model to the real design; Stage 3's AI button adds fine details."""
    parts = [f"{name.strip()} (from {anime.strip()})",
             "accurate original character design"]
    if pool.face_anchors:
        parts.append(pool.face_anchors)
    return ", ".join(parts)


def random_identity(
    pool: IdentityPool, rng: random.Random | None = None
) -> str:
    """A fresh, diverse adult identity for a 'random character' generation."""
    r = rng or random.Random()
    traits = {
        trait: r.choice(pool.traits[trait]).value
        for trait in TRAIT_ORDER
    }
    return assemble_identity(pool, traits)


def extract_traits(pool: IdentityPool, text: str) -> dict[str, str]:
    """Best-effort read of trait values out of an existing identity sentence.
    Only exact pool values match; anything unrecognized stays for the user."""
    low = f" {text.lower()} "
    found: dict[str, str] = {}
    for trait in TRAIT_ORDER:
        for tv in pool.traits[trait]:
            needle = f" {tv.value.lower()} "
            if needle in low:
                found[trait] = tv.value
                break
    return found
