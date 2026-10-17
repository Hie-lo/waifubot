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

    async def send_reply_keyboard(
        self,
        chat_id: int | str,
        text: str,
        rows: list[list[str]],
        placeholder: str = "",
    ) -> int:
        """Show/update the persistent bottom reply keyboard (Stage 4).

        `rows` are plain label rows (no callbacks — Telegram sends the label
        back as a text message, which BotApp routes like the inline menu).
        Returns the message_id of the message carrying the keyboard."""
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

    async def send_media_group(
        self,
        chat_id: int | str,
        items: list[tuple[Path, bool]],
        caption: str = "",
    ) -> list[int]:
        """Send 2-10 photos as one album (media group). `items` is a list of
        (photo_path, spoiler) — spoiler is PER PHOTO (a group may mix NSFW and
        normal images, user rule 2026-10-16). Caption goes on the first item.
        Returns the message ids in order."""
        ...

    async def edit_message(
        self, chat_id: int | str, message_id: int, text: str, buttons: Buttons | None = None
    ) -> None:
        """Edit a previously sent message (used to remove buttons after a press)."""
        ...

    async def edit_message_media(
        self,
        chat_id: int | str,
        message_id: int,
        photo_path: Path,
        caption: str = "",
        buttons: Buttons | None = None,
    ) -> None:
        """Replace the photo INSIDE an existing message (the viewer must edit
        one message in place, never send a new photo on prev/next)."""
        ...

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Acknowledge a callback query (always, even for stale presses)."""
        ...
