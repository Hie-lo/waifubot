"""Mock AI enhancer for deterministic testing."""

import asyncio
from typing import Any


class MockEnhancer:
    """Mock enhancer implementing core.ports.Enhancer."""

    def __init__(
        self,
        fixed_response: str | None = None,
        delay_seconds: float = 0.0,
        fail_with_error: Exception | None = None,
    ) -> None:
        self.fixed_response = fixed_response
        self.delay_seconds = delay_seconds
        self.fail_with_error = fail_with_error
        self.call_history: list[dict[str, Any]] = []

    async def enhance_prompt(
        self,
        scene: str,
        character_data: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> str:
        self.call_history.append(
            {
                "scene": scene,
                "character_data": character_data,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.fail_with_error is not None:
            raise self.fail_with_error

        if self.fixed_response is not None:
            return self.fixed_response

        # Default predictable enhancement
        char_prefix = ""
        if character_data and "name" in character_data:
            char_prefix = f"featuring {character_data['name']}, "
        return f"masterpiece, highly detailed, {char_prefix}{scene}, cinematic lighting, vibrant anime aesthetic"
