"""Mock generation provider and mock slot pool for deterministic testing."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

# A valid 1x1 transparent PNG byte string
TINY_PNG_BYTES: bytes = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class MockSlot:
    """Mock execution slot."""

    def __init__(self, slot_id: str = "mock-slot-0") -> None:
        self._slot_id = slot_id

    @property
    def slot_id(self) -> str:
        return self._slot_id


class MockSlotPool:
    """Mock pool providing exclusive slots for generation jobs."""

    def __init__(self, size: int = 4) -> None:
        self._slots = [MockSlot(f"mock-slot-{i}") for i in range(1, size + 1)]
        self._queue: asyncio.Queue[MockSlot] = asyncio.Queue()
        for s in self._slots:
            self._queue.put_nowait(s)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[MockSlot]:
        slot = await self._queue.get()
        try:
            yield slot
        finally:
            await self._queue.put(slot)


class MockGenerationProvider:
    """Mock generation provider implementing core.ports.GenerationProvider."""

    def __init__(
        self,
        name: str = "mock",
        fixed_images_per_call: int | None = None,
        fail_with_error: Exception | None = None,
    ) -> None:
        self._name = name
        self.fixed_images_per_call = fixed_images_per_call
        self.fail_with_error = fail_with_error
        self.call_history: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        needed_count: int,
        slot: MockSlot,
        **kwargs: Any,
    ) -> list[bytes]:
        self.call_history.append(
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "needed_count": needed_count,
                "slot_id": slot.slot_id,
                "kwargs": kwargs,
            }
        )
        if self.fail_with_error is not None:
            raise self.fail_with_error

        count = (
            self.fixed_images_per_call if self.fixed_images_per_call is not None else needed_count
        )
        # Produce distinct bytes per image index to ensure unique content hashes
        return [TINY_PNG_BYTES + f"//img_{i}".encode() for i in range(count)]
