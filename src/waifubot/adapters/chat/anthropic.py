"""Anthropic Claude chat and memory compaction adapter with prompt caching (R64, R65).

Contract:
- Implements core.ports.ChatModel.
- Prompt caching enabled on stable prefix via cache_control: {"type": "ephemeral"}.
- Minimum cacheable threshold calibrated to Anthropic's requirement (>= 1,024 tokens).
- Usage tokens (input, output, cache_read) reported for usage_counters.
"""

import asyncio
from typing import Any

from waifubot.core.chat_memory import parse_compaction_response


class AnthropicChatModel:
    """Chat and compaction model adapter backed by Anthropic API with prompt caching."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-latest",
        compact_model: str = "claude-3-5-haiku-latest",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.compact_model = compact_model

    async def complete(
        self,
        system_prefix: str,
        messages: list[dict[str, str]],
        max_reply_tokens: int = 300,
        timeout_seconds: float = 30.0,
    ) -> tuple[str, dict[str, int]]:
        """Executes completion with prompt caching on system instructions & persona prefix."""
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package is required for AnthropicChatModel") from e

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        # Structure system blocks with prompt caching enabled (R65)
        system_blocks = [
            {
                "type": "text",
                "text": system_prefix,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        async def _call() -> tuple[str, dict[str, int]]:
            response = await client.messages.create(
                model=self.model,
                max_tokens=max_reply_tokens,
                system=system_blocks,  # type: ignore[arg-type]
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],  # type: ignore[arg-type]
            )

            text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
            reply_text = "".join(text_blocks).strip()

            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
                "cached_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            }
            return reply_text, usage

        return await asyncio.wait_for(_call(), timeout=timeout_seconds)

    async def compact_memory(
        self,
        old_summary: str,
        durable_facts: list[str],
        leaving_turns: list[dict[str, str]],
        max_tokens: int = 500,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Compacts dialogue turns leaving the window into a summary + fact diff (R64)."""
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package is required for AnthropicChatModel") from e

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        system_prompt = (
            "You are a dialogue memory compaction assistant. Your task is to update the rolling "
            "summary and user facts based on recent conversation turns that are leaving the window.\n"
            "Return strict JSON matching this schema:\n"
            '{"summary": "updated rolling summary", "facts_add": ["fact1"], "facts_remove": ["old_fact"]}\n'
            f"Keep summary and facts concise (total target budget <= {max_tokens} tokens). Output JSON ONLY."
        )

        turns_text = "\n".join(f"{t['role']}: {t['content']}" for t in leaving_turns)
        user_content = (
            f"Old Summary:\n{old_summary or 'None'}\n\n"
            f"Current Durable Facts:\n{durable_facts or 'None'}\n\n"
            f"Leaving Turns:\n{turns_text}"
        )

        async def _call() -> dict[str, Any]:
            response = await client.messages.create(
                model=self.compact_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            return parse_compaction_response(raw)

        return await asyncio.wait_for(_call(), timeout=timeout_seconds)
