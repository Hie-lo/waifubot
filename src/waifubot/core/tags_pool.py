"""Tag pool loading and validation (SPEC §6). Malformed YAML fails loudly at
startup and never falls back silently."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from waifubot.errors import ConfigError


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    fragment: str
    weight: float = 1.0
    incompatible_with: tuple[str, ...] = ()
    provider_mappings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TagsPool:
    version: str
    template_version: str
    style_lock: str
    quality_fragment: str
    negative_prompt: str
    categories: dict[str, tuple[Preset, ...]]

    def all_ids(self) -> set[str]:
        return {p.id for presets in self.categories.values() for p in presets}


def _require_str(raw: dict[str, Any], key: str, ctx: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{ctx}: {key!r} must be a non-empty string")
    return value.strip()


def load_tags_pool(path: Path) -> TagsPool:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read tags pool {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"malformed tags pool YAML {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"tags pool {path} must be a mapping")

    categories_raw = raw.get("categories")
    if not isinstance(categories_raw, dict) or not categories_raw:
        raise ConfigError(f"tags pool {path} must have a non-empty 'categories' mapping")

    categories: dict[str, tuple[Preset, ...]] = {}
    seen_ids: set[str] = set()
    for category, items in categories_raw.items():
        if not isinstance(items, list) or not items:
            raise ConfigError(f"tags pool category {category!r} must be a non-empty list")
        presets: list[Preset] = []
        for i, item in enumerate(items):
            ctx = f"tags pool {category}[{i}]"
            if not isinstance(item, dict):
                raise ConfigError(f"{ctx} must be a mapping")
            preset_id = _require_str(item, "id", ctx)
            if preset_id in seen_ids:
                raise ConfigError(f"{ctx}: duplicate preset id {preset_id!r}")
            seen_ids.add(preset_id)
            weight = item.get("weight", 1.0)
            if not isinstance(weight, (int, float)) or weight <= 0:
                raise ConfigError(f"{ctx}: weight must be a positive number")
            incompatible = item.get("incompatible_with", [])
            if not isinstance(incompatible, list) or not all(
                isinstance(x, str) for x in incompatible
            ):
                raise ConfigError(f"{ctx}: incompatible_with must be a list of ids")
            mappings = item.get("provider_mappings", {})
            if not isinstance(mappings, dict):
                raise ConfigError(f"{ctx}: provider_mappings must be a mapping")
            presets.append(
                Preset(
                    id=preset_id,
                    label=_require_str(item, "label", ctx),
                    fragment=_require_str(item, "fragment", ctx),
                    weight=float(weight),
                    incompatible_with=tuple(incompatible),
                    provider_mappings={str(k): str(v) for k, v in mappings.items()},
                )
            )
        categories[str(category)] = tuple(presets)

    # Compatibility lists must reference known preset ids — fail loudly.
    for presets in categories.values():
        for preset in presets:
            for other in preset.incompatible_with:
                if other not in seen_ids:
                    raise ConfigError(
                        f"preset {preset.id!r} references unknown incompatible id {other!r}"
                    )

    return TagsPool(
        version=_require_str(raw, "version", "tags pool"),
        template_version=_require_str(raw, "template_version", "tags pool"),
        style_lock=_require_str(raw, "style_lock", "tags pool"),
        quality_fragment=_require_str(raw, "quality_fragment", "tags pool"),
        negative_prompt=_require_str(raw, "negative_prompt", "tags pool"),
        categories=categories,
    )
