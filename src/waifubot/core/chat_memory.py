"""Chat memory structures, token estimation, and compaction parsing (R64)."""

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


def normalize_text(text: str) -> str:
    """Normalize text using Unicode NFC to unify Persian/Arabic character variants."""
    return unicodedata.normalize("NFC", text.strip())


def estimate_tokens(text: str) -> int:
    """Conservative token upper-bound for Persian/mixed UTF-8 text.

    Persian characters average 1.0 to 1.4 BPE tokens per character.
    Using char_count * 1.4 or UTF-8 bytes * 0.70 ensures context limits
    are never silently exceeded.
    """
    if not text:
        return 0
    normalized = normalize_text(text)
    # Upper bound based on character count and UTF-8 bytes
    by_char = math.ceil(len(normalized) * 1.4)
    by_bytes = math.ceil(len(normalized.encode("utf-8")) * 0.70)
    return max(1, max(by_char, by_bytes))


@dataclass
class ChatTurn:
    role: str
    content: str
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": normalize_text(self.content),
            "timestamp": self.timestamp,
        }


@dataclass
class ChatMemory:
    session_id: str
    persona_card: str
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    window: list[ChatTurn] = field(default_factory=list)
    chat_memory_tokens_cap: int = 500

    def total_memory_tokens(self) -> int:
        """Token count for summary and facts."""
        text = self.summary + "\n" + "\n".join(self.facts)
        return estimate_tokens(text)

    def total_window_tokens(self) -> int:
        """Token count of raw turns currently in the active window."""
        text = "\n".join(t.content for t in self.window)
        return estimate_tokens(text)

    def should_compact(
        self,
        max_turns_before_compact: int = 10,
        window_token_budget: int = 800,
    ) -> bool:
        """Dual-condition gate: turn count OR token overflow."""
        return (
            len(self.window) >= max_turns_before_compact
            or self.total_window_tokens() >= window_token_budget
        )

    def append_turn(self, role: str, content: str, timestamp: float) -> None:
        self.window.append(ChatTurn(role=role, content=content, timestamp=timestamp))


def parse_compaction_response(raw_response: str) -> dict[str, Any]:
    """Parse strict JSON response from compaction call, with regex extraction fallback."""
    raw = raw_response.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        # Fallback: extract the outermost JSON block using regex
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise ValueError(f"No JSON found in compaction response: {raw[:100]}...") from err
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError("Compaction JSON must be a dictionary")

    return {
        "summary": str(data.get("summary", "")).strip(),
        "facts_add": [str(f).strip() for f in data.get("facts_add", []) if str(f).strip()],
        "facts_remove": [str(f).strip() for f in data.get("facts_remove", []) if str(f).strip()],
    }
