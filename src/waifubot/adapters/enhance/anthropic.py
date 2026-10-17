"""Anthropic Claude enhancer implementing core.ports.Enhancer."""

import asyncio
from typing import Any


class AnthropicEnhancer:
    """Enhances prompts via Anthropic Claude API."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-latest",
    ) -> None:
        self.api_key = api_key
        self.model = model

    async def enhance_prompt(
        self,
        scene: str,
        character_data: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> str:
        # Lazy import to keep module clean when anthropic is not installed
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic package is required for AnthropicEnhancer") from e

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        system_prompt = (
            "You are an anime art prompt engineer. Expand the user's description into a vivid, "
            "danbooru/civitai style anime image generation prompt. Keep it descriptive, tag-friendly, "
            "and output ONLY the final enhanced prompt text without explanation."
        )

        user_content = f"Scene: {scene}"
        if character_data:
            user_content = f"Character details: {character_data}\n{user_content}"

        async def _call() -> str:
            response = await client.messages.create(
                model=self.model,
                max_tokens=350,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
            return "".join(text_blocks).strip()

        return await asyncio.wait_for(_call(), timeout=timeout_seconds)
