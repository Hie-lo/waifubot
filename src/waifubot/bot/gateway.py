"""The outbound Telegram boundary.

Handlers and services talk only to this protocol — tests use a fake, production
uses `PTBGateway` in app.py. No logic lives here; no test ever touches the
Telegram API (SPEC §13: the test environment has no network).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

# A button row = list of (label, callback_data) pairs.
Buttons = list[list[tuple[str, str]]]


@runtime_checkable
class TelegramGateway(Protocol):
    async def send_message(
        self, chat_id: int | str, text: str, buttons: Buttons | None = None
    ) -> int:
        """Send a text message; returns its message_id."""
        ...

    async def send_photo(
        self,
        chat_id: int | str,
        photo_path: Path,
        caption: str = "",
        spoiler: bool = False,
        buttons: Buttons | None = None,
    ) -> int:
        """Send a photo; returns its message_id."""
        ...

    async def send_document(
        self,
        chat_id: int | str,
        doc_path: Path,
        caption: str = "",
        buttons: Buttons | None = None,
    ) -> int:
        """Send a file as a document (lossless); returns its message_id."""
        ...

    async def edit_message(
        self, chat_id: int | str, message_id: int, text: str, buttons: Buttons | None = None
    ) -> None:
        """Edit a previously sent message (used to remove buttons after a press)."""
        ...

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Acknowledge a callback query (always, even for stale presses)."""
        ...
