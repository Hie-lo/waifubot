"""Telegram rate limiter with flood-wait handling and file_id cache (R42)."""

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class FileIdCache:
    """Caches Telegram file_id keyed by content SHA256 to prevent redundant uploads (R42)."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def get(self, sha256_hash: str) -> str | None:
        """Retrieves cached Telegram file_id if present."""
        return self._cache.get(sha256_hash)

    def put(self, sha256_hash: str, file_id: str) -> None:
        """Stores Telegram file_id for content hash."""
        self._cache[sha256_hash] = file_id

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


class TelegramRateLimiter:
    """Rate limiter governing outbound Telegram API calls with flood-wait backoff (R42)."""

    def __init__(
        self,
        min_interval_seconds: float = 0.05,
        album_gap_seconds: float = 0.5,
    ) -> None:
        self.min_interval = min_interval_seconds
        self.album_gap = album_gap_seconds
        self._lock = asyncio.Lock()
        self._last_call_at = 0.0

    async def execute(
        self,
        call_fn: Callable[[], Coroutine[Any, Any, T]],
        is_album_send: bool = False,
    ) -> T:
        """Executes an async Telegram call under rate-limiting and flood-wait backoff."""
        async with self._lock:
            gap = self.album_gap if is_album_send else self.min_interval
            elapsed = time.time() - self._last_call_at
            if elapsed < gap:
                await asyncio.sleep(gap - elapsed)

            while True:
                try:
                    res = await call_fn()
                    self._last_call_at = time.time()
                    return res
                except Exception as exc:
                    # Check for Telegram RetryAfter / flood-wait attribute
                    retry_after = getattr(exc, "retry_after", None)
                    if retry_after is not None:
                        # Flood-wait received -> sleep requested duration + small safety margin
                        await asyncio.sleep(float(retry_after) + 0.5)
                        continue
                    raise
