"""Identity building (user request 2026-10-16).

Two jobs:
1. `random_identity` — a DIVERSE random adult identity per generation (the old
   fallback was one hardcoded "long blue hair" woman, so every "random"
   character looked the same).
2. `assemble_identity` / `extract_traits` — the save-character flow builds a
   PRECISE, ordered identity prompt (hair, eyes, skin, face shape, age) so the
   same character keeps its face and details across later generations.

Rules that never change (SPEC §2.3 / AGENT.md):
- Every assembled identity is adult-only: `prefix` + an adult age, always first.
- `extract_traits` only READS text; assembling a precise identity is an explicit
  user action in the savec wizard (never silently rewritten).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from waifubot.errors import ConfigError

TRAIT_ORDER: tuple[str, ...] = (
    "age",
    "hair_color",
    "hair_style",
    "eye_color",
    "skin",
    "face",
)

_TRAIT_KEYS: dict[str, str] = {
    "age": "ages",
    "hair_color": "hair_colors",
    "hair_style": "hair_styles",
    "eye_color": "eye_colors",
    "skin": "skin_tones",
    "face": "face_shapes",
}


@dataclass(frozen=True)
class TraitValue:
    value: str
    fa: str
    en: str


class IdentityPool:
    def __init__(self, data: dict[str, Any]) -> None:
        self.prefix: str = str(data.get("prefix") or "adult woman")
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
) -> str:
    """Build the identity prompt in canonical order. `traits` maps trait name ->
    chosen value (already final English prompt fragments)."""
    parts: list[str] = [pool.prefix]
    age = traits.get("age", "")
    parts.append(f"{age} years old" if age.isdigit() else age or "25 years old")
    hair_color = traits.get("hair_color", "")
    hair_style = traits.get("hair_style", "")
    if hair_color or hair_style:
        parts.append(f"{hair_color} {hair_style} hair".strip())
    if traits.get("eye_color"):
        parts.append(f"{traits['eye_color']} eyes")
    if traits.get("skin"):
        parts.append(f"{traits['skin']} skin")
    if traits.get("face"):
        parts.append(f"{traits['face']} face")
    if extras.strip():
        parts.append(extras.strip().strip(","))
    if precise and pool.face_anchors:
        parts.append(pool.face_anchors)
    return ", ".join(p for p in parts if p)


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
