"""Factory for resolving AI chat models and prompt enhancers from configuration (R20, R21, R65).

Supports Anthropic (Claude), OpenRouter, Google Gemini, OpenAI, Groq, and local LLMs.
"""

import os

from waifubot.adapters.chat.anthropic import AnthropicChatModel
from waifubot.adapters.chat.mock import MockChatModel
from waifubot.adapters.chat.openai_compatible import OpenAICompatibleChatModel
from waifubot.adapters.enhance.anthropic import AnthropicEnhancer
from waifubot.adapters.enhance.mock import MockEnhancer
from waifubot.adapters.enhance.openai_compatible import OpenAICompatibleEnhancer
from waifubot.core.ports import ChatModel, Enhancer


def _detect_provider(explicit: str | None = None) -> str:
    """Detects provider from argument or environment variables."""
    if explicit:
        return explicit.lower()
    env_provider = os.environ.get("AI_PROVIDER")
    if env_provider:
        return env_provider.strip().lower()
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("GEMINI_API_KEY"):
        return "google"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


def create_chat_model(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    compact_model: str | None = None,
) -> ChatModel:
    """Builds a ChatModel adapter based on provider choice and environment variables."""
    resolved_provider = _detect_provider(provider)

    if resolved_provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AI_API_KEY", "")
        resolved_model = model or os.environ.get("AI_CHAT_MODEL", "claude-3-5-haiku-latest")
        resolved_compact = (
            compact_model or os.environ.get("AI_CHAT_COMPACT_MODEL") or resolved_model
        )
        return AnthropicChatModel(
            api_key=key,
            model=resolved_model,
            compact_model=resolved_compact,
        )

    if resolved_provider == "openrouter":
        key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY", "")
        url = base_url or os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1")
        resolved_model = model or os.environ.get("AI_CHAT_MODEL", "google/gemini-2.0-flash-001")
        resolved_compact = (
            compact_model or os.environ.get("AI_CHAT_COMPACT_MODEL") or resolved_model
        )
        return OpenAICompatibleChatModel(
            api_key=key,
            base_url=url,
            model=resolved_model,
            compact_model=resolved_compact,
        )

    if resolved_provider == "google":
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY", "")
        url = base_url or os.environ.get(
            "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        resolved_model = model or os.environ.get("AI_CHAT_MODEL", "gemini-2.0-flash")
        resolved_compact = (
            compact_model or os.environ.get("AI_CHAT_COMPACT_MODEL") or resolved_model
        )
        return OpenAICompatibleChatModel(
            api_key=key,
            base_url=url,
            model=resolved_model,
            compact_model=resolved_compact,
        )

    if resolved_provider in ("openai", "custom"):
        key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY", "")
        url = base_url or os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
        resolved_model = model or os.environ.get("AI_CHAT_MODEL", "gpt-4o-mini")
        resolved_compact = (
            compact_model or os.environ.get("AI_CHAT_COMPACT_MODEL") or resolved_model
        )
        return OpenAICompatibleChatModel(
            api_key=key,
            base_url=url,
            model=resolved_model,
            compact_model=resolved_compact,
        )

    return MockChatModel()


def create_enhancer(
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> Enhancer:
    """Builds an Enhancer adapter based on provider choice and environment variables."""
    resolved_provider = _detect_provider(provider)

    if resolved_provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AI_API_KEY", "")
        resolved_model = model or os.environ.get("AI_ENHANCE_MODEL", "claude-3-5-haiku-latest")
        return AnthropicEnhancer(
            api_key=key,
            model=resolved_model,
        )

    if resolved_provider == "openrouter":
        key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY", "")
        url = base_url or os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1")
        resolved_model = model or os.environ.get("AI_ENHANCE_MODEL", "google/gemini-2.0-flash-001")
        return OpenAICompatibleEnhancer(
            api_key=key,
            base_url=url,
            model=resolved_model,
        )

    if resolved_provider == "google":
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY", "")
        url = base_url or os.environ.get(
            "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        resolved_model = model or os.environ.get("AI_ENHANCE_MODEL", "gemini-2.0-flash")
        return OpenAICompatibleEnhancer(
            api_key=key,
            base_url=url,
            model=resolved_model,
        )

    if resolved_provider in ("openai", "custom"):
        key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY", "")
        url = base_url or os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
        resolved_model = model or os.environ.get("AI_ENHANCE_MODEL", "gpt-4o-mini")
        return OpenAICompatibleEnhancer(
            api_key=key,
            base_url=url,
            model=resolved_model,
        )

    return MockEnhancer()
