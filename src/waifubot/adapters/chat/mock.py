"""Mock chat model implementation for testing and offline scenarios (R63, R64)."""

from typing import Any

from waifubot.core.chat_memory import estimate_tokens


class MockChatModel:
    """Mock implementation of ChatModel protocol with configurable responses."""

    def __init__(
        self,
        canned_reply: str = "سلام! من همیشه در کنار تو هستم.",
        fail_with: Exception | None = None,
    ) -> None:
        self.canned_reply = canned_reply
        self.fail_with = fail_with
        self.calls: list[dict[str, Any]] = []
        self.compaction_calls: list[dict[str, Any]] = []

    async def complete(
        self,
        system_prefix: str,
        messages: list[dict[str, str]],
        max_reply_tokens: int = 300,
        timeout_seconds: float = 30.0,
    ) -> tuple[str, dict[str, int]]:
        if self.fail_with:
            raise self.fail_with

        self.calls.append(
            {
                "system_prefix": system_prefix,
                "messages": messages,
                "max_reply_tokens": max_reply_tokens,
            }
        )

        in_tokens = estimate_tokens(system_prefix) + sum(
            estimate_tokens(m.get("content", "")) for m in messages
        )
        out_tokens = estimate_tokens(self.canned_reply)

        usage = {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "cached_tokens": max(0, in_tokens - 100),
        }
        return self.canned_reply, usage

    async def compact_memory(
        self,
        old_summary: str,
        durable_facts: list[str],
        leaving_turns: list[dict[str, str]],
        max_tokens: int = 500,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        if self.fail_with:
            raise self.fail_with

        self.compaction_calls.append(
            {
                "old_summary": old_summary,
                "durable_facts": durable_facts,
                "leaving_turns": leaving_turns,
            }
        )

        # Build mock updated summary from turns
        new_summary = f"{old_summary} [Compacted {len(leaving_turns)} turns]".strip()
        new_facts = list(durable_facts)
        # Extract any mentioned fact
        for t in leaving_turns:
            if "likes" in t.get("content", "").lower() or "علاقه" in t.get("content", ""):
                new_facts.append("کاربر به انیمه علاقه دارد.")

        return {
            "summary": new_summary,
            "facts_add": new_facts,
            "facts_remove": [],
        }
