"""Chat model adapters."""

from waifubot.adapters.chat.anthropic import AnthropicChatModel
from waifubot.adapters.chat.mock import MockChatModel

__all__ = ["AnthropicChatModel", "MockChatModel"]
