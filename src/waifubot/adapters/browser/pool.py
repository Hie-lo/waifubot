"""Browser slot pool: one Chromium instance managing isolated browser contexts (R35, ADR-0003)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any


class BrowserSlot:
    """An exclusive execution slot containing an isolated browser context and page (R35)."""

    def __init__(
        self,
        slot_id: str,
        context: Any,
        page: Any,
        storage_state_path: Path | None = None,
    ) -> None:
        self._slot_id = slot_id
        self.context = context
        self.page = page
        self.storage_state_path = storage_state_path

    @property
    def slot_id(self) -> str:
        return self._slot_id

    async def save_storage_state(self) -> None:
        """Persists session cookies/localStorage for warm reuse across jobs (R35)."""
        if self.storage_state_path and hasattr(self.context, "storage_state"):
            try:
                self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                await self.context.storage_state(path=str(self.storage_state_path))
            except Exception:
                pass


class BrowserSlotPool:
    """Resource pool managing up to max_contexts isolated browser execution slots."""

    def __init__(
        self,
        max_contexts: int = 2,
        storage_state_dir: str | Path = "data/browser_sessions",
        headless: bool = True,
        mock_mode: bool = False,
    ) -> None:
        self.max_contexts = max_contexts
        self.storage_state_dir = Path(storage_state_dir)
        self.headless = headless
        self.mock_mode = mock_mode

        self._playwright: Any = None
        self._browser: Any = None
        self._slots: list[BrowserSlot] = []
        self._queue: asyncio.Queue[BrowserSlot] = asyncio.Queue()
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _init_pool(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return

            self.storage_state_dir.mkdir(parents=True, exist_ok=True)

            if not self.mock_mode:
                try:
                    from playwright.async_api import async_playwright

                    self._playwright = await async_playwright().start()
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.headless,
                        args=["--disable-dev-shm-usage", "--no-sandbox"],
                    )
                except Exception:
                    # Fallback to mock mode if Playwright binary / browser is unavailable in environment
                    self.mock_mode = True

            for i in range(1, self.max_contexts + 1):
                slot_id = f"browser-slot-{i}"
                state_file = self.storage_state_dir / f"session_{slot_id}.json"

                if self.mock_mode or self._browser is None:
                    # Lightweight mock context/page for zero-dependency test execution
                    context: Any = _MockBrowserContext()
                    page: Any = _MockBrowserPage()
                else:
                    kwargs: dict[str, Any] = {}
                    if state_file.exists():
                        kwargs["storage_state"] = str(state_file)
                    context = await self._browser.new_context(**kwargs)
                    page = await context.new_page()

                slot = BrowserSlot(
                    slot_id=slot_id,
                    context=context,
                    page=page,
                    storage_state_path=state_file,
                )
                self._slots.append(slot)
                self._queue.put_nowait(slot)

            self._initialized = True

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[BrowserSlot]:
        """Acquires an exclusive browser slot, yielding it and returning it on release."""
        if not self._initialized:
            await self._init_pool()

        slot = await self._queue.get()
        try:
            yield slot
        finally:
            await slot.save_storage_state()
            self._queue.put_nowait(slot)

    async def close(self) -> None:
        """Closes all browser contexts and terminates the Chromium browser process."""
        for slot in self._slots:
            try:
                await slot.save_storage_state()
                if hasattr(slot.context, "close"):
                    await slot.context.close()
            except Exception:
                pass
        self._slots.clear()

        if self._browser is not None:
            with suppress(Exception):
                await self._browser.close()
            self._browser = None

        if self._playwright is not None:
            with suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

        self._initialized = False


class _MockBrowserContext:
    def __init__(self) -> None:
        self.closed = False

    async def storage_state(self, path: str | None = None) -> dict[str, Any]:
        data = {"cookies": [], "origins": []}
        if path:
            Path(path).write_text(json.dumps(data), encoding="utf-8")
        return data

    async def close(self) -> None:
        self.closed = True


class _MockBrowserPage:
    def __init__(self) -> None:
        self.closed = False
        self.url = "about:blank"

    async def close(self) -> None:
        self.closed = True

    async def goto(self, url: str) -> None:
        self.url = url
