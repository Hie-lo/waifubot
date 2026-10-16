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
from waifubot.errors import ValidationError

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


async def publish_group(
    session: AsyncSession,
    gateway: TelegramGateway,
    *,
    images: list[Image],
    chat_id: int | str,
    caption: str = "",
    storage_root: Path,
) -> list[PublishOutcome]:
    """Publish 2-10 approved images as one media group (user 2026-10-16).

    Per-image rules that MUST hold inside a group too:
    - already-sent images are skipped (never double-post);
    - each photo carries its own spoiler flag (NSFW mix inside one group);
    - a send failure never regenerates anything; ambiguous sends become
      `unknown` per image and the human resolves them.
    """
    if not 2 <= len(images) <= 10:
        raise ValidationError("a media group needs 2 to 10 images")
    outcomes: list[PublishOutcome] = []
    to_send: list[Image] = []
    for img in images:
        existing = (
            await session.execute(
                select(Post).where(Post.image_id == img.id, Post.chat_id == str(chat_id))
            )
        ).scalars().first()
        if existing is not None and existing.status in ("sent", "unknown"):
            state = "already" if existing.status == "sent" else "unknown"
            outcomes.append(PublishOutcome(status=state, post=existing))
            continue
        post = existing or Post(
            image_id=img.id, chat_id=str(chat_id), status="pending", attempt_count=0
        )
        if existing is None:
            session.add(post)
        post.attempt_count += 1
        outcomes.append(PublishOutcome(status="pending", post=post))
        to_send.append(img)
    await session.flush()
    if not to_send:
        return outcomes

    items: list[tuple[Path, bool]] = []
    ready: list[tuple[Image, PublishOutcome]] = []
    for img in to_send:
        path = storage_root / img.file_path
        outcome = next(o for o in outcomes if o.post.image_id == img.id and o.status == "pending")
        if not path.is_file():
            outcome.status = "failed"
            outcome.error = "missing_file"
            outcome.post.status = "failed"
            outcome.post.last_error = "missing_file"
            continue
        reason = validate_send_limits(img, img.file_size or 0)
        if reason:
            outcome.status = "failed"
            outcome.error = reason
            outcome.post.status = "failed"
            outcome.post.last_error = reason
            continue
        items.append((path, bool(img.is_nsfw)))
        ready.append((img, outcome))
    if not ready:
        await session.flush()
        return outcomes

    if len(ready) == 1:
        # Telegram albums need 2-10 items: a single survivor goes out as one
        # photo (still with its own spoiler flag).
        img, outcome = ready[0]
        try:
            mid = await gateway.send_photo(
                chat_id, items[0][0], caption=caption[:CAPTION_MAX_CHARS],
                spoiler=items[0][1],
            )
            outcome.status = "sent"
            outcome.message_id = mid
            outcome.post.status = "sent"
            outcome.post.telegram_message_id = str(mid)
            outcome.post.sent_at = datetime.now(UTC)
            img.status = "published"
        except TimeoutError:
            outcome.status = "unknown"
            outcome.post.status = "unknown"
        except Exception as e:  # noqa: BLE001
            outcome.status = "failed"
            outcome.error = str(e)[:200]
            outcome.post.status = "failed"
            outcome.post.last_error = str(e)[:200]
        await session.flush()
        return outcomes

    try:
        message_ids = await gateway.send_media_group(
            chat_id, items, caption=caption[:CAPTION_MAX_CHARS]
        )
    except TimeoutError:
        for _img, outcome in ready:
            outcome.status = "unknown"
            outcome.post.status = "unknown"
        await session.flush()
        return outcomes
    except Exception as e:  # noqa: BLE001 — one failure never regenerates
        for _img, outcome in ready:
            outcome.status = "failed"
            outcome.error = str(e)[:200]
            outcome.post.status = "failed"
            outcome.post.last_error = str(e)[:200]
        await session.flush()
        return outcomes

    for i, (img, outcome) in enumerate(ready):
        mid = message_ids[i] if i < len(message_ids) else None
        outcome.status = "sent"
        outcome.message_id = mid
        outcome.post.status = "sent"
        outcome.post.telegram_message_id = str(mid) if mid is not None else None
        outcome.post.sent_at = datetime.now(UTC)
        img.status = "published"
    await session.flush()
    return outcomes
