"""Mock provider tests: every simulated outcome (Sprint 1b exit criteria)."""
from __future__ import annotations

import asyncio

from waifubot.core.image_provider import (
    ERROR_BLOCKED,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_TIMEOUT,
    GenerationRequest,
)
from waifubot.core.providers.mock import MockProvider

REQ = GenerationRequest(prompt="adult woman, 25 years old", count=4, metadata={"seed": "42"})


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_ok_mode_returns_full_count() -> None:
    result = _run(MockProvider({"mode": "ok"}).generate(REQ))
    assert result.status == STATUS_OK
    assert len(result.candidates) == 4
    assert result.provider == "mock"
    assert all(c.seed for c in result.candidates)  # mock genuinely returns seeds


def test_partial_mode() -> None:
    result = _run(MockProvider({"mode": "partial", "partial_count": 2}).generate(REQ))
    assert result.status == STATUS_PARTIAL
    assert len(result.candidates) == 2


def test_malformed_mode_mixes_bad_bytes() -> None:
    result = _run(MockProvider({"mode": "malformed"}).generate(REQ))
    assert len(result.candidates) == 4
    assert any(c.data.startswith(b"\x89PNG-corrupt") for c in result.candidates)


def test_blocked_mode_is_permanent() -> None:
    result = _run(MockProvider({"mode": "blocked"}).generate(REQ))
    assert result.status == STATUS_BLOCKED
    assert result.error_code == ERROR_BLOCKED
    assert result.candidates == []


def test_timeout_mode() -> None:
    result = _run(MockProvider({"mode": "timeout"}).generate(REQ))
    assert result.status == STATUS_TIMEOUT
    assert result.candidates == []


def test_error_mode() -> None:
    result = _run(MockProvider({"mode": "error"}).generate(REQ))
    assert result.status == STATUS_ERROR


def test_empty_mode() -> None:
    result = _run(MockProvider({"mode": "empty"}).generate(REQ))
    assert result.status == STATUS_OK
    assert result.candidates == []


def test_deterministic_pixels_for_same_seed() -> None:
    a = _run(MockProvider({"mode": "ok"}).generate(REQ))
    b = _run(MockProvider({"mode": "ok"}).generate(REQ))
    assert [c.data for c in a.candidates] == [c.data for c in b.candidates]


def test_progressive_delay_is_bounded() -> None:
    cfg = {"mode": "ok", "delay_s": 0.01}
    result = _run(MockProvider(cfg).generate(REQ))
    assert len(result.candidates) == 4
    assert result.elapsed_s >= 0.03


def test_health_check() -> None:
    assert _run(MockProvider({"mode": "ok"}).health_check()) is True
    assert _run(MockProvider({"mode": "error"}).health_check()) is False
