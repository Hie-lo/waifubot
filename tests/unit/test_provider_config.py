"""Provider config loading and factory tests (SPEC §7.4 config-switched providers)."""
from __future__ import annotations

from pathlib import Path

import pytest

from waifubot.core.providers import (
    create_provider,
    load_provider_config,
    validate_provider_config,
)
from waifubot.core.providers.mock import MockProvider
from waifubot.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_CFG = REPO_ROOT / "config" / "providers" / "mock.yaml"
PERCHANCE_CFG = REPO_ROOT / "config" / "providers" / "perchance.yaml"


def test_mock_config_loads() -> None:
    cfg = load_provider_config(MOCK_CFG)
    validate_provider_config(cfg)
    provider = create_provider(cfg)
    assert isinstance(provider, MockProvider)


def test_perchance_config_loads_and_has_proven_selectors() -> None:
    cfg = load_provider_config(PERCHANCE_CFG)
    validate_provider_config(cfg)
    assert cfg["output_iframe"] == "#outputIframeEl"
    assert cfg["selectors"]["prompt_textarea"] == "textarea[data-name='description']"
    assert cfg["selectors"]["generate_button"] == "button#generateButtonEl"
    assert cfg["style_values"]["anime"] == "ref:optionKeyName:Anime"
    assert cfg["style_values"]["no_style"] == ""
    assert "--disable-blink-features=AutomationControlled" in cfg["launch"]["args"]


def test_perchance_adapter_is_created_in_sprint2() -> None:
    from waifubot.core.providers.perchance import PerchanceProvider

    cfg = load_provider_config(PERCHANCE_CFG)
    provider = create_provider(cfg)
    assert isinstance(provider, PerchanceProvider)


def test_unknown_provider_name_fails(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("name: mystery\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown provider"):
        create_provider(load_provider_config(bad))


def test_perchance_missing_selector_fails(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text(
        "name: perchance_playwright\npage_url: 'x'\noutput_iframe: 'y'\n"
        "selectors: {prompt_textarea: 'a'}\nstyle_values: {}\nlaunch: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="selectors"):
        validate_provider_config(load_provider_config(bad))


def test_bad_mock_mode_fails(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("name: mock\nmode: sneaky\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mode"):
        validate_provider_config(load_provider_config(bad))


def test_malformed_yaml_fails(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("name: [broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed"):
        load_provider_config(bad)
