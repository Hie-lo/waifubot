"""Provider factory and config loading (SPEC §7.4: providers are config-switched)."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from waifubot.core.image_provider import ImageProvider
from waifubot.core.providers.mock import MockProvider
from waifubot.errors import ConfigError, ProviderError

PERCHANCE_NAME = "perchance_playwright"
MOCK_NAME = "mock"


def load_provider_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read provider config {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"malformed provider config YAML {path}: {e}") from e
    if not isinstance(raw, dict) or not raw.get("name"):
        raise ConfigError(f"provider config {path} must be a mapping with a 'name'")
    return raw


def validate_provider_config(cfg: Mapping[str, Any]) -> None:
    name = cfg.get("name")
    if name == MOCK_NAME:
        mode = cfg.get("mode", "ok")
        if mode not in ("ok", "partial", "malformed", "blocked", "timeout", "error", "empty"):
            raise ConfigError(f"mock provider mode must be one of the known modes, got {mode!r}")
        return
    if name == PERCHANCE_NAME:
        for key in ("page_url", "output_iframe", "selectors", "style_values", "launch"):
            if key not in cfg:
                raise ConfigError(f"perchance provider config is missing {key!r}")
        for sel in ("prompt_textarea", "style_select", "generate_button"):
            if sel not in cfg["selectors"]:
                raise ConfigError(f"perchance selectors are missing {sel!r}")
        return
    raise ConfigError(f"unknown provider name {name!r}")


def create_provider(cfg: Mapping[str, Any]) -> ImageProvider:
    validate_provider_config(cfg)
    name = cfg.get("name")
    if name == MOCK_NAME:
        return MockProvider(cfg)
    if name == PERCHANCE_NAME:
        from waifubot.core.providers.perchance import PerchanceProvider

        return PerchanceProvider(cfg)
    raise ProviderError(f"unknown provider {name!r}")
