"""Wizard state: versioned Pydantic models with TTL expiration and prompt composition (R15, R16B)."""

import time
from typing import Any

from pydantic import BaseModel, Field


class WizardState(BaseModel):
    """Versioned wizard state tracking character studio selections and options (R16B)."""

    user_id: int
    chat_id: int
    mode: str = "special_studio"  # "special_studio" | "custom"
    step: str = "character_select"
    version: int = 1

    selected_character: str | None = None
    character_prompt: str = ""
    selected_options: dict[str, str] = Field(default_factory=dict)

    custom_prompt: str = ""
    negative_prompt: str = ""
    target_count: int = 4
    nsfw: bool = False

    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def compose_prompt(self) -> str:
        """Composes final generation prompt.

        For Special Character Studio (R16B):
        Strictly joins the character definition prompt and non-empty selected option tags
        with ', ' matching Vision Lab's sync() javascript contract.
        """
        if self.mode == "special_studio":
            parts: list[str] = []
            if self.character_prompt.strip():
                parts.append(self.character_prompt.strip())
            for key in sorted(self.selected_options.keys()):
                val = self.selected_options[key].strip()
                if val:
                    parts.append(val)
            return ", ".join(parts)

        # Custom wizard mode (R15)
        parts = []
        if self.character_prompt.strip():
            parts.append(self.character_prompt.strip())
        if self.custom_prompt.strip():
            parts.append(self.custom_prompt.strip())
        return ", ".join(parts)


class WizardStore:
    """In-memory wizard state store with TTL eviction keyed by (user_id, chat_id)."""

    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        self.ttl = ttl_seconds
        self._states: dict[tuple[int, int], WizardState] = {}

    def get(self, user_id: int, chat_id: int) -> WizardState | None:
        state = self._states.get((user_id, chat_id))
        if state is None:
            return None
        if time.time() - state.updated_at > self.ttl:
            self._states.pop((user_id, chat_id), None)
            return None
        return state

    def set(self, state: WizardState) -> None:
        state.updated_at = time.time()
        state.version += 1
        self._states[(state.user_id, state.chat_id)] = state

    def clear(self, user_id: int, chat_id: int) -> None:
        self._states.pop((user_id, chat_id), None)
