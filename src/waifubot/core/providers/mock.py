"""Mock provider (Sprint 1b). No network, no browser. Shapes mirror the real
behavior seen in the Sprint 0 spike: progressive arrival of image candidates
within a bounded window, and a permanent `blocked` outcome on challenge pages.

Modes (config/providers/mock.yaml):
- ok        : full target count of valid PNG candidates
- partial   : fewer than requested, then STATUS_PARTIAL
- malformed : some candidates carry corrupt bytes (pipeline must reject them)
- blocked   : challenge page simulation (STATUS_BLOCKED, never retried)
- timeout   : produces nothing, ends as STATUS_TIMEOUT
- error     : generic provider failure
- empty     : succeeds with zero candidates
"""
from __future__ import annotations

import asyncio
import io
import random
import time
from collections.abc import Mapping
from typing import Any

from PIL import Image

from waifubot.core.image_provider import (
    ERROR_BLOCKED,
    ERROR_PROVIDER,
    ERROR_TIMEOUT,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_TIMEOUT,
    GenerationRequest,
    GenerationResult,
    ImageCandidate,
)


class MockProvider:
    name = "mock"

    def __init__(self, cfg: Mapping[str, Any], *, clock=time.monotonic) -> None:
        self._mode = str(cfg.get("mode", "ok"))
        self._width = int(cfg.get("width", 512))
        self._height = int(cfg.get("height", 512))
        self._delay_s = float(cfg.get("delay_s", 0.0))
        self._partial_count = int(cfg.get("partial_count", 2))
        self._clock = clock

    async def health_check(self) -> bool:
        return self._mode != "error"

    def _make_png(self, seed: int, index: int) -> bytes:
        rng = random.Random((seed << 8) + index)
        pixels = bytes(rng.randrange(256) for _ in range(self._width * self._height * 3))
        img = Image.frombytes("RGB", (self._width, self._height), pixels)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        t0 = self._clock()
        if self._mode == "blocked":
            return GenerationResult(
                status=STATUS_BLOCKED,
                candidates=[],
                elapsed_s=round(self._clock() - t0, 3),
                provider=self.name,
                error_code=ERROR_BLOCKED,
                error_summary="mock: challenge page simulated",
            )
        if self._mode == "crash":  # raw exception (not a normalized result)
            raise RuntimeError("mock: provider crashed hard")
        if self._mode == "error":
            return GenerationResult(
                status=STATUS_ERROR,
                candidates=[],
                elapsed_s=round(self._clock() - t0, 3),
                provider=self.name,
                error_code=ERROR_PROVIDER,
                error_summary="mock: generic provider failure",
            )

        seed = int(request.metadata.get("seed", "0")) if request.metadata else 0
        target = request.count
        if self._mode == "empty":
            target = 0
        elif self._mode == "timeout":
            target = 0
        elif self._mode == "partial":
            target = min(self._partial_count, request.count)

        candidates: list[ImageCandidate] = []
        for i in range(target):
            if self._delay_s > 0:
                await asyncio.sleep(self._delay_s)  # progressive arrival
            if self._mode == "malformed" and i % 2 == 1:
                candidates.append(
                    ImageCandidate(data=b"\x89PNG-corrupt", claimed_mime="image/png", source="mock")
                )
            else:
                candidates.append(
                    ImageCandidate(
                        data=self._make_png(seed, i),
                        claimed_mime="image/png",
                        source="mock",
                        seed=f"mock-{seed}-{i}",  # the mock genuinely returns seeds
                    )
                )

        if self._mode == "timeout":
            return GenerationResult(
                status=STATUS_TIMEOUT,
                candidates=candidates,
                elapsed_s=round(self._clock() - t0, 3),
                provider=self.name,
                error_code=ERROR_TIMEOUT,
                error_summary="mock: timed out waiting for images",
            )
        if self._mode == "partial" and len(candidates) < request.count:
            return GenerationResult(
                status=STATUS_PARTIAL,
                candidates=candidates,
                elapsed_s=round(self._clock() - t0, 3),
                provider=self.name,
            )
        return GenerationResult(
            status=STATUS_OK,
            candidates=candidates,
            elapsed_s=round(self._clock() - t0, 3),
            provider=self.name,
        )

    async def close(self) -> None:
        return None
