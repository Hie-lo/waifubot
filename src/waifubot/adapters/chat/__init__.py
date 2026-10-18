"""Chat model adapters."""

from waifubot.adapters.chat.anthropic import AnthropicChatModel
from waifubot.adapters.chat.mock import MockChatModel
from waifubot.adapters.chat.openai_compatible import OpenAICompatibleChatModel

__all__ = ["AnthropicChatModel", "MockChatModel", "OpenAICompatibleChatModel"]
