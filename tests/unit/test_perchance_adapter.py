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
    out = clamp_limits({"timeout_s": 99999, "poll_interval_s": 0.1, "challenge_wait_s": 99999})
    assert out["timeout_s"] <= 300
    assert out["poll_interval_s"] >= 1.0
    assert out["challenge_wait_s"] <= 600


def test_pacing_is_clamped() -> None:
    out = clamp_pacing({"human_pacing": {"type_delay_ms_min": 1, "type_delay_ms_max": 99999,
                                         "pause_chance": 0.9}})
    assert out["type_delay_ms_min"] >= 5
    assert out["type_delay_ms_max"] <= 500 and out["type_delay_ms_max"] >= out["type_delay_ms_min"]
    assert out["pause_chance"] <= 0.5
    assert out["pause_ms_min"] >= 50
    assert out["pause_ms_max"] >= out["pause_ms_min"]


def test_factory_creates_real_adapter() -> None:
    cfg = load_provider_config(REPO_ROOT / "config" / "providers" / "perchance.yaml")
    provider = create_provider(cfg)
    assert isinstance(provider, PerchanceProvider)
    assert provider.name == "perchance_playwright"


def test_adapter_config_keeps_user_approved_profile() -> None:
    cfg = load_provider_config(REPO_ROOT / "config" / "providers" / "perchance.yaml")
    provider = PerchanceProvider(cfg)
    p = provider._pacing  # noqa: SLF001
    assert 5 <= p["type_delay_ms_min"] <= p["type_delay_ms_max"] <= 500
    assert provider._limits["timeout_s"] <= 300  # noqa: SLF001
    assert provider._limits["challenge_wait_s"] > 0  # noqa: SLF001


def test_detect_block_catches_cloudflare_interstitial() -> None:
    # Headless test 2026-10-16: this exact wording slipped past the detector.
    body = (
        "perchance.org Performing security verification This website uses a "
        "security service to protect itself from online attacks."
    )
    assert detect_block(body, "https://perchance.org/ai-text-to-image-generator") == (
        "performing security verification"
    )
    assert detect_block("Just a moment...", "https://x") == "just a moment"
    assert detect_block("Checking your browser", "https://x") == "checking your browser"
    assert detect_block("Attention Required", "https://x") == "attention required"


def test_detect_block_no_false_positive_on_normal_page() -> None:
    body = (
        "AI text to image generator description art style generate button "
        "examples settings quality masterpiece anime girl blue hair smiling"
    )
    assert detect_block(body, "https://perchance.org/ai-text-to-image-generator") is None


def test_pick_launch_mode_ladder() -> None:
    from waifubot.core.providers.perchance import pick_launch_mode

    # Explicit flags win.
    assert pick_launch_mode({}, xvfb_available=True, force_headed=True) == "window"
    assert pick_launch_mode({"headless": False}, xvfb_available=True, force_headless=True) == (
        "headless"
    )
    # Auto: virtual (invisible real browser) when Xvfb exists.
    assert pick_launch_mode({"virtual_display": "auto"}, xvfb_available=True) == "virtual"
    # No Xvfb -> headless if configured, else window.
    assert pick_launch_mode({"headless": True}, xvfb_available=False) == "headless"
    assert pick_launch_mode({"headless": False}, xvfb_available=False) == "window"
    # virtual_display can be disabled.
    assert (
        pick_launch_mode({"headless": True, "virtual_display": "false"}, xvfb_available=True)
        == "headless"
    )
