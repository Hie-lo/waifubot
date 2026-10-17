"""Limits value object and effective limits calculation (R52-R53).

The superadmin has no limits (R53): effective_limits() short-circuits here
and nowhere else.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Limits:
    max_owned_characters: int | None
    daily_image_budget: int | None
    max_images_per_job: int | None
    max_concurrent_jobs: int | None
    max_stored_images: int | None
    daily_chat_messages: int | None
    chat_memory_tokens: int | None
    max_channels: int | None
    queue_priority: int

    @classmethod
    def unlimited(cls) -> "Limits":
        """Single short-circuit definition for superadmin (R53)."""
        return cls(
            max_owned_characters=None,
            daily_image_budget=None,
            max_images_per_job=16,
            max_concurrent_jobs=None,
            max_stored_images=None,
            daily_chat_messages=None,
            chat_memory_tokens=1500,
            max_channels=None,
            queue_priority=100,
        )


def effective_limits(
    role: str,
    plan_limits: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Limits:
    """Derive effective limits based on role, plan defaults, and user overrides.

    This function is the SINGLE authority for limit resolution (R53).
    """
    if role == "superadmin":
        return Limits.unlimited()

    defaults = {
        "max_owned_characters": 3,
        "daily_image_budget": 8,
        "max_images_per_job": 4,
        "max_concurrent_jobs": 1,
        "max_stored_images": 100,
        "daily_chat_messages": 60,
        "chat_memory_tokens": 500,
        "max_channels": 0,
        "queue_priority": 0,
    }

    if role == "admin":
        defaults = {
            "max_owned_characters": None,
            "daily_image_budget": None,
            "max_images_per_job": 16,
            "max_concurrent_jobs": None,
            "max_stored_images": None,
            "daily_chat_messages": None,
            "chat_memory_tokens": 1500,
            "max_channels": None,
            "queue_priority": 10,
        }

    merged = dict(defaults)
    if plan_limits:
        merged.update({k: v for k, v in plan_limits.items() if k in merged})
    if overrides:
        merged.update({k: v for k, v in overrides.items() if k in merged})

    return Limits(
        max_owned_characters=merged["max_owned_characters"],
        daily_image_budget=merged["daily_image_budget"],
        max_images_per_job=merged["max_images_per_job"],
        max_concurrent_jobs=merged["max_concurrent_jobs"],
        max_stored_images=merged["max_stored_images"],
        daily_chat_messages=merged["daily_chat_messages"],
        chat_memory_tokens=merged["chat_memory_tokens"],
        max_channels=merged["max_channels"],
        queue_priority=int(merged.get("queue_priority", 0)),
    )
