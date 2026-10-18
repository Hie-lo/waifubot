"""External adapters implementing core ports."""

from waifubot.adapters.ai_factory import create_chat_model, create_enhancer

__all__ = ["create_chat_model", "create_enhancer"]
