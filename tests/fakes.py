"""Shared test fakes (SPEC §13: no network — this gateway never touches Telegram)."""
from __future__ import annotations


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[tuple] = []
        self.photos: list[tuple] = []
        self.documents: list[tuple] = []
        self.answers: list[tuple] = []
        self.media_groups: list[tuple] = []
        self.edits: list[tuple] = []
        self.edit_media: list[tuple] = []
        self.fail_mode: str | None = None

    def _maybe_fail(self) -> None:
        if self.fail_mode == "timeout":
            raise TimeoutError()
        if self.fail_mode == "error":
            raise RuntimeError("network down")

    async def send_message(self, chat_id, text, buttons=None) -> int:  # noqa: ANN001
        self._maybe_fail()
        self.messages.append((chat_id, text, buttons))
        return len(self.messages)

    async def send_photo(self, chat_id, photo_path, caption="", spoiler=False,  # noqa: ANN001
                         buttons=None) -> int:
        self._maybe_fail()
        self.photos.append((chat_id, photo_path, caption, spoiler, buttons))
        return len(self.photos)

    async def send_document(self, chat_id, doc_path, caption="", buttons=None) -> int:  # noqa: ANN001
        self._maybe_fail()
        self.documents.append((chat_id, doc_path, caption, buttons))
        return len(self.documents)

    async def send_media_group(self, chat_id, items, caption="") -> list[int]:  # noqa: ANN001
        self._maybe_fail()
        self.media_groups.append((chat_id, list(items), caption))
        base = 1000 + len(self.media_groups)
        return [base + i for i in range(len(items))]

    async def edit_message(self, chat_id, message_id, text, buttons=None) -> None:  # noqa: ANN001
        self._maybe_fail()
        self.edits.append((chat_id, message_id, text, buttons))

    async def edit_message_media(self, chat_id, message_id, photo_path,  # noqa: ANN001
                                 caption="", buttons=None) -> None:
        self._maybe_fail()
        self.edit_media.append((chat_id, message_id, photo_path, caption, buttons))

    async def answer_callback(self, callback_id, text="") -> None:  # noqa: ANN001
        self.answers.append((callback_id, text))


class FakeEnhancer:
    """Test double for AiEnhancer — records calls, canned response."""

    def __init__(self, reply: str = "ENHANCED PROMPT (all details preserved)") -> None:
        self.reply = reply
        self.calls: list[tuple[str, bool, str]] = []
        self.fail_mode = ""

    async def enhance(self, prompt: str, *, known: bool, model: str = "") -> str:
        self.calls.append((prompt, known, model))
        if self.fail_mode == "error":
            from waifubot.errors import AiError

            raise AiError("boom")
        return self.reply
