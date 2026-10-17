"""PanelEngine: per-admin locking, versioned panels, instant callback answers, and edit fallback (R2–R4, R40)."""

import asyncio
from dataclasses import dataclass
import time
from typing import Any


@dataclass
class PanelState:
    """Active panel state keyed by (admin_id, chat_id) (ADR-0004)."""

    admin_id: int
    chat_id: int
    message_id: int
    version: int
    screen: str
    updated_at: float


class PanelMemory:
    """Thread-safe in-memory cache of active panel coordinates keyed by (admin_id, chat_id)."""

    def __init__(self) -> None:
        self._states: dict[tuple[int, int], PanelState] = {}
        self._lock = asyncio.Lock()

    async def get(self, admin_id: int, chat_id: int) -> PanelState | None:
        async with self._lock:
            return self._states.get((admin_id, chat_id))

    async def set(self, state: PanelState) -> None:
        async with self._lock:
            self._states[(state.admin_id, state.chat_id)] = state

    async def clear(self, admin_id: int, chat_id: int) -> None:
        async with self._lock:
            self._states.pop((admin_id, chat_id), None)


class PanelLockManager:
    """Provides exclusive per-admin locks to eliminate concurrent panel race conditions (R40)."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, admin_id: int) -> asyncio.Lock:
        if admin_id not in self._locks:
            self._locks[admin_id] = asyncio.Lock()
        return self._locks[admin_id]


class PanelEngine:
    """Coordinates panel rendering, version verification, and Telegram API interaction."""

    def __init__(
        self,
        panel_memory: PanelMemory | None = None,
        lock_manager: PanelLockManager | None = None,
        telegram_gateway: Any = None,
    ) -> None:
        self.memory = panel_memory or PanelMemory()
        self.locks = lock_manager or PanelLockManager()
        self.gateway = telegram_gateway

    async def answer_callback_instant(
        self,
        query: Any,
        text: str = "",
        show_alert: bool = False,
    ) -> None:
        """Answers callback query immediately to dismiss client loading spinner (R40)."""
        if query and hasattr(query, "answer"):
            try:
                await query.answer(text=text, show_alert=show_alert)
            except Exception:
                pass

    async def is_valid_version(
        self,
        admin_id: int,
        chat_id: int,
        callback_version: int,
    ) -> bool:
        """Checks if callback matches current panel version; stale presses are ignored idempotently."""
        state = await self.memory.get(admin_id, chat_id)
        if state is None:
            return True
        return callback_version >= state.version

    async def render_panel(
        self,
        admin_id: int,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
        screen: str = "main",
        force_new: bool = False,
    ) -> PanelState:
        """Edits existing panel in place (R3) or creates new panel on message loss (R40)."""
        lock = self.locks.get_lock(admin_id)
        async with lock:
            state = await self.memory.get(admin_id, chat_id)
            current_ver = state.version if state else 0
            new_version = current_ver + 1

            if not force_new and state is not None and self.gateway:
                try:
                    # Attempt edit in place
                    await self.gateway.edit_message_text(
                        chat_id=chat_id,
                        message_id=state.message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    state.version = new_version
                    state.screen = screen
                    state.updated_at = time.time()
                    await self.memory.set(state)
                    return state
                except Exception as exc:
                    err_str = str(exc).lower()
                    if "message is not modified" in err_str:
                        # Benign duplicate press, state remains current
                        return state
                    # Message deleted or not found -> fall through to send a new message (R40)

            # Send a fresh panel message
            message_id = 1
            if self.gateway and hasattr(self.gateway, "send_message"):
                msg = await self.gateway.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                message_id = getattr(msg, "message_id", 1)

            new_state = PanelState(
                admin_id=admin_id,
                chat_id=chat_id,
                message_id=message_id,
                version=new_version,
                screen=screen,
                updated_at=time.time(),
            )
            await self.memory.set(new_state)
            return new_state

    async def close_panel(self, admin_id: int, chat_id: int) -> None:
        """Deletes panel message and clears active panel state (R4)."""
        lock = self.locks.get_lock(admin_id)
        async with lock:
            state = await self.memory.get(admin_id, chat_id)
            if state and self.gateway and hasattr(self.gateway, "delete_message"):
                try:
                    await self.gateway.delete_message(
                        chat_id=chat_id,
                        message_id=state.message_id,
                    )
                except Exception:
                    pass
            await self.memory.clear(admin_id, chat_id)
