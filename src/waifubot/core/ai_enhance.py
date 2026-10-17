"""AI prompt enhancement (Stage 3, user 2026-10-16).

A single fixed instruction drives every enhancement: the AI must preserve
ALL user-given details (age, height, colors, sizes, scene), may add canonical
details for a known/famous character, and returns only the improved prompt.

The real client is Anthropic (lazy-imported). Tests inject a fake and never
touch the network (AGENT.md). If no API key is configured the buttons report
a friendly error and nothing else breaks.
"""
from __future__ import annotations

from typing import Protocol

from waifubot.errors import AiError

# The fixed instruction — identical for every call (user requirement).
ENHANCE_SYSTEM = (
    "You are a professional stable-diffusion prompt engineer. Rewrite the "
    "given image prompt to be more professional and more consistent.\n"
    "RULES (never break them):\n"
    "1. PRESERVE every detail the user provided: age, height, face, eye/skin/"
    "hair colors, body type and body proportions, clothing, pose, activity "
    "and background. Never remove, soften or invent contradictions to them.\n"
    "2. If the prompt names a known/famous character (with its anime/game), "
    "add accurate canonical details of that character's original design so "
    "the rendered character stays faithful.\n"
    "3. Improve structure, clarity and visual coherence; use professional "
    "image-prompt language; keep it in English.\n"
    "4. Output ONLY the enhanced prompt text - no explanations, no quotes, "
    "no markdown."
)


class AiEnhancer(Protocol):
    """What the bot needs: text in, enhanced text out. `model` may override
    the configured model per call (the admin setting wins at call time)."""

    async def enhance(self, prompt: str, *, known: bool, model: str = "") -> str: ...


class AnthropicEnhancer:
    """Real enhancer on the Anthropic Messages API (production only)."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self._api_key)

    async def enhance(self, prompt: str, *, known: bool, model: str = "") -> str:
        if not self._api_key:
            raise AiError("no API key configured")
        try:
            import anthropic  # lazy: dev/CLI installs may not need it
        except ImportError as e:  # pragma: no cover - depends on install
            raise AiError("anthropic package is not installed") from e
        hint = (
            "This is a KNOWN/FAMOUS character - rule 2 applies."
            if known
            else "This is an original character - rule 2 does not apply."
        )
        try:
            client = anthropic.AsyncAnthropic(api_key=self._api_key)
            reply = await client.messages.create(
                model=model or self._model,
                max_tokens=1024,
                system=ENHANCE_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": f"{hint}\n\nPrompt:\n{prompt}",
                    }
                ],
            )
        except Exception as e:  # noqa: BLE001 - normalize provider errors
            raise AiError(f"enhance request failed: {e}") from e
        parts = [
            block.text
            for block in reply.content
            if getattr(block, "type", "") == "text"
        ]
        out = "\n".join(parts).strip()
        if not out:
            raise AiError("enhance returned an empty prompt")
        return out


def build_enhancer(api_key: str, model: str) -> AnthropicEnhancer:
    """Production factory. Always returns an enhancer; `available()` tells
    whether it can actually serve requests (key present)."""
    return AnthropicEnhancer(api_key=(api_key or "").strip(), model=model)
