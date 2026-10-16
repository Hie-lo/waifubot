"""Perchance adapter unit tests (helpers + limits; the live path is manual-only).

No browser, no network in tests (AGENT.md). The real adapter is exercised by
the user via `python -m waifubot.cli live-smoke --confirm-live` (Sprint 2).
"""
from __future__ import annotations

from pathlib import Path

from waifubot.core.providers import create_provider, load_provider_config
from waifubot.core.providers.perchance import (
    BLOCKED_MARKERS,
    PerchanceProvider,
    clamp_limits,
    clamp_pacing,
    decode_data_url,
    detect_block,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_detect_block_markers() -> None:
    assert detect_block("please complete this captcha", "https://x") == "captcha"
    assert detect_block("Access Denied", "https://x") == "access denied"
    assert detect_block("429 Too Many Requests", "https://x") == "too many requests"
    assert detect_block("verify you are human", "https://x") == "verify you are human"
    assert detect_block("nice anime images here", "https://x/page") is None
    assert detect_block("", "https://x/rate-limit-page") == "rate limit"


def test_blocked_markers_cover_required_cases() -> None:
    for marker in ("captcha", "challenge", "access denied", "rate limit"):
        assert marker in BLOCKED_MARKERS


def test_decode_data_url_valid() -> None:
    mime, raw = decode_data_url("data:image/png;base64,aGVsbG8=")
    assert mime == "image/png"
    assert raw == b"hello"


def test_decode_data_url_rejects_malformed() -> None:
    assert decode_data_url("data:image/png;base64,!!!not-base64!!!") is None
    assert decode_data_url("https://example.com/a.png") is None
    assert decode_data_url("data:,") is None


def test_limits_are_clamped_to_hard_caps() -> None:
    out = clamp_limits({"timeout_s": 99999, "poll_interval_s": 0.1})
    assert out["timeout_s"] <= 300
    assert out["poll_interval_s"] >= 1.0


def test_pacing_is_clamped() -> None:
    lo, hi = clamp_pacing({"human_pacing": {"type_delay_ms_min": 1, "type_delay_ms_max": 99999}})
    assert lo >= 10
    assert hi <= 500 and hi >= lo


def test_factory_creates_real_adapter() -> None:
    cfg = load_provider_config(REPO_ROOT / "config" / "providers" / "perchance.yaml")
    provider = create_provider(cfg)
    assert isinstance(provider, PerchanceProvider)
    assert provider.name == "perchance_playwright"


def test_adapter_config_keeps_user_approved_profile() -> None:
    cfg = load_provider_config(REPO_ROOT / "config" / "providers" / "perchance.yaml")
    provider = PerchanceProvider(cfg)
    lo, hi = provider._pacing  # noqa: SLF001
    assert 10 <= lo <= hi <= 500
    assert provider._limits["timeout_s"] <= 300  # noqa: SLF001
