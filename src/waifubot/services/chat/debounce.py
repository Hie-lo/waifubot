"""Chat message debouncing service for merging rapid consecutive messages (R63).

Contract:
- Rapid consecutive messages from the same user are merged into one prompt.
- Default chat_debounce_ms = 1200 ms.
- Preserves message order, joining with newline.
"""

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DebounceBucket:
    messages: list[str] = field(default_factory=list)
    timer_task: asyncio.Task[Any] | None = None
    last_received_at: float = field(default_factory=time.time)


class ChatDebouncer:
    """Collects rapid consecutive chat messages per session/user and flushes them together."""

    def __init__(self, debounce_ms: int = 1200) -> None:
        self.debounce_seconds = max(0.05, debounce_ms / 1000.0)
        self._buckets: dict[str, DebounceBucket] = {}
        self._lock = asyncio.Lock()

    def merge_messages(self, messages: list[str]) -> str:
        """Joins collected messages with newline separator."""
        return "\n".join(m.strip() for m in messages if m.strip()).strip()

    async def add_message(
        self,
        key: str,
        text: str,
        on_flush: Callable[[str, str], Coroutine[Any, Any, None]],
    ) -> None:
        """Appends message to the key's bucket and schedules/resets flush timer."""
        async with self._lock:
            if key not in self._buckets:
                self._buckets[key] = DebounceBucket()

            bucket = self._buckets[key]
            bucket.messages.append(text)
            bucket.last_received_at = time.time()

            # Cancel previous timer if still ticking
            if bucket.timer_task and not bucket.timer_task.done():
                bucket.timer_task.cancel()

            bucket.timer_task = asyncio.create_task(self._wait_and_flush(key, on_flush))

    async def _wait_and_flush(
        self,
        key: str,
        on_flush: Callable[[str, str], Coroutine[Any, Any, None]],
    ) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            async with self._lock:
                bucket = self._buckets.pop(key, None)
                if not bucket or not bucket.messages:
                    return
                merged = self.merge_messages(bucket.messages)

            await on_flush(key, merged)
        except asyncio.CancelledError:
            # Replaced by a newer message in the debounce window
            pass

    async def flush_immediately(self, key: str) -> str | None:
        """Immediately flushes pending messages for a key without waiting."""
        async with self._lock:
            bucket = self._buckets.pop(key, None)
            if not bucket or not bucket.messages:
                return None
            if bucket.timer_task and not bucket.timer_task.done():
                bucket.timer_task.cancel()
            return self.merge_messages(bucket.messages)
