"""OpenAI-compatible chat and memory compaction adapter (R64, R65).

Supports OpenRouter, Google Gemini (via OpenAI-compatible endpoint), OpenAI,
Groq, DeepSeek, and local LLMs (Ollama, vLLM).
"""

import asyncio
from typing import Any

import httpx

from waifubot.core.chat_memory import parse_compaction_response


class OpenAICompatibleChatModel:
    """Chat and compaction model adapter backed by any OpenAI-compatible completions API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-2.0-flash-001",
        compact_model: str | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.compact_model = compact_model or model
        self.default_headers = default_headers or {
            "HTTP-Referer": "https://github.com/Hie-lo/waifubot",
            "X-Title": "WaifuBot",
        }

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.default_headers)
        return headers

    async def complete(
        self,
        system_prefix: str,
        messages: list[dict[str, str]],
        max_reply_tokens: int = 300,
        timeout_seconds: float = 30.0,
    ) -> tuple[str, dict[str, int]]:
        """Executes completion against the OpenAI-compatible chat endpoint."""
        url = f"{self.base_url}/chat/completions"

        full_messages = [{"role": "system", "content": system_prefix}]
        for m in messages:
            full_messages.append({"role": m["role"], "content": m["content"]})

        payload = {
            "model": self.model,
            "messages": full_messages,
            "max_tokens": max_reply_tokens,
        }

        async def _call() -> tuple[str, dict[str, int]]:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(url, headers=self._build_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()

            reply_text = data["choices"][0]["message"]["content"].strip()
            raw_usage = data.get("usage", {})
            prompt_details = raw_usage.get("prompt_tokens_details", {}) or {}
            cached_tokens = prompt_details.get("cached_tokens", 0) or 0

            usage = {
                "input_tokens": raw_usage.get("prompt_tokens", 0),
                "output_tokens": raw_usage.get("completion_tokens", 0),
                "cached_tokens": cached_tokens,
            }
            return reply_text, usage

        return await asyncio.wait_for(_call(), timeout=timeout_seconds + 5.0)

    async def compact_memory(
        self,
        old_summary: str,
        durable_facts: list[str],
        leaving_turns: list[dict[str, str]],
        max_tokens: int = 500,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Compacts dialogue turns leaving the window into summary + facts diff."""
        url = f"{self.base_url}/chat/completions"

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

        payload = {
            "model": self.compact_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
        }

        async def _call() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(url, headers=self._build_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()

            raw_reply = data["choices"][0]["message"]["content"]
            return parse_compaction_response(raw_reply)

        return await asyncio.wait_for(_call(), timeout=timeout_seconds + 5.0)
