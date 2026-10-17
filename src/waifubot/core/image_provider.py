"""Provider interface (SPEC §9.1) — shaped by the Sprint 0 spike findings:

- Images arrive progressively during a bounded polling window (spike runs took
  ~1.5–2 min to collect 4 images) → `generate()` collects until the target
  count or timeout and reports `partial` when it saved some but not all.
- A challenge/CAPTCHA/rate-limit page is a permanent outcome (`blocked`,
  error_code `blocked_by_provider`) — never retried (SPEC §10).
- Formats are NOT assumed: candidates carry claimed MIME only; real format,
  dimensions, and size come from Pillow validation in the pipeline. (Spike:
  outputs observed as JPEG.)
- A seed is carried only when the provider genuinely returns one (never
  observed on Perchance; the mock genuinely returns seeds).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"

ERROR_BLOCKED = "blocked_by_provider"
ERROR_TIMEOUT = "provider_timeout"
ERROR_PROVIDER = "provider_error"


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    count: int
    style_value: str = ""
    timeout_s: float = 240.0
    job_id: int | None = None
    # Safe metadata only — never tokens, cookies, or Base64.
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageCandidate:
    data: bytes
    claimed_mime: str = "image/jpeg"
    source: str = ""  # short label (e.g. "img[data-url]") — never a full data URL
    seed: str | None = None  # only if genuinely returned by the provider


@dataclass(frozen=True)
class GenerationResult:
    status: str
    candidates: list[ImageCandidate]
    elapsed_s: float
    provider: str
    error_code: str | None = None
    error_summary: str | None = None  # bounded, sanitized — never secrets/Base64
    notes: tuple[str, ...] = ()  # non-fatal observations (e.g. style_select_failed)


@runtime_checkable
class ImageProvider(Protocol):
    name: str

    async def health_check(self) -> bool: ...

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    async def close(self) -> None: ...
