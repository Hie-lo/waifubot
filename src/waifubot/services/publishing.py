"""Channel and album publishing service with Telegram rate limits and file_id caching (R23, R24, R42, R59).

Contract:
- Pre-publish channel check via assert_usable() (R59).
- Telegram file_id cached after first upload and reused (R42).
- Users with channel capacity may publish their own images to their own channels (R59).
- Errors handled with explained messages (publish_too_large, publish_failed) (R24).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.ports import TelegramPublisher
from waifubot.db.models import ImageRecord, JobRecord
from waifubot.services.channels import assert_usable


class PublishError(Exception):
    """Raised when publishing to a channel or chat fails (R24)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"Publish error [{code}]: {message}")


async def publish_image(
    session: AsyncSession,
    publisher: TelegramPublisher,
    channel_id: int,
    image_id: str,
    requester_user_id: int,
    requester_role: str,
    caption: str = "",
    topic_id: int | None = None,
    has_spoiler: bool = False,
    max_image_mb: float = 10.0,
    character_id: str | None = None,
    claimable: bool = False,
    bot_username: str = "waifubot",
) -> str:
    """Publishes a single image to a verified channel (R24, R42, R59, R61).

    Returns:
        Telegram file_id of the published image.
    """
    # 1. Assert channel is usable by this requester (R59)
    try:
        channel = await assert_usable(session, channel_id, requester_user_id, requester_role)
    except Exception as e:
        raise PublishError("channel_unusable", str(e)) from e

    # 2. Retrieve image
    img_stmt = select(ImageRecord).where(ImageRecord.image_id == image_id)
    img_res = await session.execute(img_stmt)
    image = img_res.scalar_one_or_none()
    if image is None:
        raise PublishError("not_found", f"Image '{image_id}' not found")

    # 3. User ownership verification (R59: users only publish their own images to their own channels)
    if requester_role == "user":
        job_stmt = select(JobRecord).where(
            JobRecord.job_id == image.job_id,
            JobRecord.requester_user_id == requester_user_id,
        )
        job_res = await session.execute(job_stmt)
        if job_res.scalar_one_or_none() is None:
            raise PublishError(
                "not_authorized", "Users may only publish their own generated images"
            )

    # 4. Size check (R24: publish_too_large)
    if image.file_size_bytes > max_image_mb * 1024 * 1024:
        raise PublishError(
            "publish_too_large",
            f"Image size ({image.file_size_bytes} bytes) exceeds {max_image_mb}MB limit",
        )

    # 5. Telegram file_id caching (R42)
    payload: str = image.telegram_file_id if image.telegram_file_id else image.file_path

    # Build claimable deep-link button if requested by staff (R61)
    reply_markup = None
    if claimable and character_id and requester_role in ("superadmin", "admin"):
        from waifubot.services.ownership import set_character_status

        await set_character_status(session, character_id, status="listed", claimable=True)
        deep_link = f"https://t.me/{bot_username}?start=char_{character_id}"
        reply_markup = [[{"text": "💬 چت / دریافت (Claim)", "url": deep_link}]]

    # 6. Execute Telegram send
    try:
        try:
            returned_file_id = await publisher.send_photo(
                chat_id=channel.tg_chat_id,
                photo=payload,
                caption=caption or image.final_prompt[:200],
                topic_id=topic_id,
                has_spoiler=has_spoiler,
                reply_markup=reply_markup,
            )
        except TypeError:
            returned_file_id = await publisher.send_photo(
                chat_id=channel.tg_chat_id,
                photo=payload,
                caption=caption or image.final_prompt[:200],
                topic_id=topic_id,
                has_spoiler=has_spoiler,
            )
    except Exception as e:
        err_msg = str(e).lower()
        if "too large" in err_msg or "entity too large" in err_msg:
            raise PublishError("publish_too_large", str(e)) from e
        raise PublishError("publish_failed", str(e)) from e

    # 7. Update cache if newly uploaded
    if returned_file_id and not image.telegram_file_id:
        image.telegram_file_id = returned_file_id
        await session.flush()

    return returned_file_id or (image.telegram_file_id or "")
