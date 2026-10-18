"""OpenAI-compatible prompt enhancer (R20, R21, R62).

Supports OpenRouter, Google Gemini (via OpenAI-compatible endpoint), OpenAI,
Groq, DeepSeek, and local LLMs (Ollama, vLLM).
"""

import asyncio
from typing import Any

import httpx


class OpenAICompatibleEnhancer:
    """Enhances image generation prompts and personas via OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-2.0-flash-001",
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
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

    async def enhance_prompt(
        self,
        scene: str,
        character_data: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> str:
        """Enhances an anime image generation prompt using the configured model."""
        url = f"{self.base_url}/chat/completions"

        system_prompt = (
            "You are an anime art prompt engineer. Expand the user's description into a vivid, "
            "danbooru/civitai style anime image generation prompt. Keep it descriptive, tag-friendly, "
            "and output ONLY the final enhanced prompt text without explanation."
        )

        user_content = f"Scene: {scene}"
        if character_data:
            user_content = f"Character details: {character_data}\n{user_content}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 350,
        }

        async def _call() -> str:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(url, headers=self._build_headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()

            return data["choices"][0]["message"]["content"].strip()

        return await asyncio.wait_for(_call(), timeout=timeout_seconds + 5.0)
