"""Telegram forum topic thread utilities."""

from typing import Any


def extract_thread_id(update_or_message: Any) -> int | None:
    """Extracts forum topic message_thread_id from a Telegram Update or Message object."""
    if update_or_message is None:
        return None

    # Check direct attribute (Message)
    if hasattr(update_or_message, "message_thread_id"):
        return update_or_message.message_thread_id

    # Check effective_message (Update)
    effective_msg = getattr(update_or_message, "effective_message", None)
    if effective_msg and hasattr(effective_msg, "message_thread_id"):
        return effective_msg.message_thread_id

    # Check dict payload fallback
    if isinstance(update_or_message, dict):
        if "message_thread_id" in update_or_message:
            return update_or_message["message_thread_id"]
        msg = update_or_message.get("message") or update_or_message.get("effective_message")
        if isinstance(msg, dict):
            return msg.get("message_thread_id")

    return None


def format_chat_and_topic(chat_id: int | str, topic_id: int | None = None) -> str:
    """Formats chat and optional topic as a readable identifier."""
    if topic_id is not None:
        return f"{chat_id}/{topic_id}"
    return str(chat_id)
