"""Publishing (SPEC §11): generation and publication are separate — a send
failure never triggers regeneration. Every attempt is recorded in `posts`.

Ambiguous sends (timeout / network error with unknown outcome) become `unknown`
and the admin resolves them with two buttons. Never auto-resend. The unique
(image_id, chat_id) key prevents scheduling the same publication twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.gateway import TelegramGateway
from waifubot.db.models import Image, Post

# Telegram limits (A5, [VERIFY] against current Bot API docs).
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_MAX_DIM_SUM = 10_000
CAPTION_MAX_CHARS = 1024
CALLBACK_DATA_MAX_BYTES = 64


@dataclass
class PublishOutcome:
    status: str  # "sent" | "failed" | "unknown" | "already"
    post: Post
    message_id: int | None = None
    error: str | None = None


def validate_send_limits(image: Image, file_size: int) -> str | None:
    """Returns a rejection reason code, or None when within limits (A5)."""
    if file_size > PHOTO_MAX_BYTES:
        return "too_large"
    if (image.width or 0) + (image.height or 0) > PHOTO_MAX_DIM_SUM:
        return "dims_too_large"
    return None


async def publish_image(
    session: AsyncSession,
    gateway: TelegramGateway,
    *,
    image: Image,
    chat_id: int | str,
    caption: str,
    spoiler: bool = False,
    storage_root: Path,
) -> PublishOutcome:
    """Attempt one publication. The caller commits."""
    caption = caption[:CAPTION_MAX_CHARS]
    existing = (
        await session.execute(
            select(Post).where(Post.image_id == image.id, Post.chat_id == str(chat_id))
        )
    ).scalars().first()
    if existing is not None and existing.status == "sent":
        return PublishOutcome(status="already", post=existing,
                              message_id=existing.telegram_message_id)
    if existing is not None and existing.status == "unknown":
        # NEVER auto-resend an ambiguous send: the human resolves it (res: buttons).
        return PublishOutcome(status="unknown", post=existing)
    post = existing or Post(
        image_id=image.id, chat_id=str(chat_id), status="pending", attempt_count=0
    )
    if existing is None:
        session.add(post)
    post.attempt_count += 1
    await session.flush()

    file_path = storage_root / image.file_path
    if not file_path.is_file():
        post.status = "failed"
        post.last_error = "missing_file"
        await session.flush()
        return PublishOutcome(status="failed", post=post, error="missing_file")

    reason = validate_send_limits(image, image.file_size or 0)
    if reason:
        post.status = "failed"
        post.last_error = reason
        await session.flush()
        return PublishOutcome(status="failed", post=post, error=reason)

    try:
        use_document = bool(image.mime_type and not image.mime_type.startswith("image/"))
        if use_document:
            message_id = await gateway.send_document(chat_id, file_path, caption=caption)
        else:
            message_id = await gateway.send_photo(
                chat_id, file_path, caption=caption, spoiler=spoiler
            )
    except TimeoutError as e:  # ambiguous: the message may or may not exist
        post.status = "unknown"
        post.last_error = f"timeout:{type(e).__name__}"
        await session.flush()
        return PublishOutcome(status="unknown", post=post, error="timeout")
    except Exception as e:  # noqa: BLE001 — every failure gets a category
        post.status = "failed"
        post.last_error = f"{type(e).__name__}: {str(e)[:120]}"
        await session.flush()
        return PublishOutcome(status="failed", post=post, error=type(e).__name__)

    post.status = "sent"
    post.telegram_message_id = message_id
    post.sent_at = datetime.now(UTC)
    post.last_error = None
    image.status = "published"
    await session.flush()
    return PublishOutcome(status="sent", post=post, message_id=message_id)


async def resolve_unknown_send(
    session: AsyncSession,
    *,
    post: Post,
    was_sent: bool,
    message_id: int | None = None,
) -> Post:
    """Ambiguous-send resolution (SPEC §11): the admin decides; never auto-resend.
    "it was sent" -> sent (the admin confirms/supplies the message); "it was not
    sent" -> pending so it can be published again deliberately."""
    if post.status != "unknown":
        return post
    if was_sent:
        post.status = "sent"
        if message_id is not None:
            post.telegram_message_id = message_id
        post.sent_at = post.sent_at or datetime.now(UTC)
        image = await session.get(Image, post.image_id)
        if image is not None:
            image.status = "published"
    else:
        post.status = "pending"
    await session.flush()
    return post
