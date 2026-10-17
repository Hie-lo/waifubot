"""Pure domain ports (Protocol definitions).

All external capabilities are declared as ports here and implemented in adapters/.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol


class Clock(Protocol):
    """Time provider for deterministic testing."""

    def now_utc(self) -> float: ...


class ProviderSlot(Protocol):
    """An exclusive execution slot for a generation provider."""

    @property
    def slot_id(self) -> str: ...


class ProviderSlotPool(Protocol):
    """Resource pool providing exclusive slots for generation jobs."""

    def acquire(self) -> AsyncIterator[ProviderSlot]: ...


class GenerationProvider(Protocol):
    """Image generation provider adapter port."""

    @property
    def name(self) -> str: ...

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        needed_count: int,
        slot: ProviderSlot,
        **kwargs: Any,
    ) -> list[bytes]: ...


class Enhancer(Protocol):
    """AI enhance port for prompt and persona enrichment."""

    async def enhance_prompt(
        self,
        scene: str,
        character_data: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> str: ...


class ChatModel(Protocol):
    """LLM completion port for character companion chat and compaction."""

    async def complete(
        self,
        system_prefix: str,
        messages: list[dict[str, str]],
        max_reply_tokens: int = 300,
        timeout_seconds: float = 30.0,
    ) -> tuple[str, dict[str, int]]:
        """Returns (reply_text, token_usage_dict)."""
        ...

    async def compact_memory(
        self,
        old_summary: str,
        durable_facts: list[str],
        leaving_turns: list[dict[str, str]],
        max_tokens: int = 500,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Returns strict dict with summary, facts_add, facts_remove."""
        ...


class ImageStore(Protocol):
    """Content-addressed local or remote image storage port."""

    async def save_image(self, data: bytes, mime_type: str) -> str:
        """Saves image atomically and returns its content hash / path."""
        ...

    async def load_image(self, file_id: str) -> bytes: ...


class ChannelProbe(Protocol):
    """Gateway probe port to inspect Telegram channels and permissions."""

    async def get_chat_info(self, chat_ref: str | int) -> dict[str, Any]: ...

    async def get_chat_member_role(
        self, chat_ref: str | int, user_id: int
    ) -> str | None: ...

    async def get_bot_rights(self, chat_ref: str | int) -> dict[str, bool]: ...


class ChallengeTimeoutError(Exception):
    """Raised when waiting for human challenge resolution exceeds timeout (R28)."""


class ChallengeGate(Protocol):
    """Port for serializing human challenge interactions (R28)."""

    def acquire_challenge(
        self, wait_timeout_seconds: float = 180.0
    ) -> AsyncIterator[None]: ...

