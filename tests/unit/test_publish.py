"""Unit tests for publishing images to channels, Telegram rate limits, and file_id caching (R24, R42, R59)."""

import json
import time
import unittest
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.db.base import Base
from waifubot.db.models import ChannelRecord, ImageRecord, JobRecord
from waifubot.services.publishing import PublishError, publish_image


class MockTelegramPublisher:
    """Mock Telegram publisher tracking sent items and file_id caching."""

    def __init__(self) -> None:
        self.sent_photos: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None

    async def send_photo(
        self,
        chat_id: int,
        photo: str | bytes,
        caption: str = "",
        topic_id: int | None = None,
        has_spoiler: bool = False,
    ) -> str:
        if self.fail_with:
            raise self.fail_with

        if isinstance(photo, str) and photo.startswith("tg_file_id_"):
            file_id = photo
        else:
            file_id = f"tg_file_id_{len(self.sent_photos) + 1}"

        self.sent_photos.append(
            {
                "chat_id": chat_id,
                "photo": photo,
                "caption": caption,
                "topic_id": topic_id,
                "has_spoiler": has_spoiler,
                "returned_file_id": file_id,
            }
        )
        return file_id


class TestPublishing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.publisher = MockTelegramPublisher()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_successful_publish_and_file_id_caching(self) -> None:
        """First publish uploads file and caches file_id; second publish reuses file_id (R42)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100888,
                title="Anime Showcase",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)

            job = JobRecord(
                job_id="job_staff_1",
                requester_user_id=1,
                admin_id=1,
                prompt="masterpiece waifu",
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(job)

            image = ImageRecord(
                image_id="img_publish_1",
                file_path="data/images/waifu.png",
                job_id="job_staff_1",
                final_prompt="masterpiece waifu",
                file_size_bytes=500_000,  # 500 KB
                created_at=time.time(),
            )
            session.add(image)
            await session.commit()
            chan_id = channel.id

            # 1. First publish -> passes file_path
            fid1 = await publish_image(
                session=session,
                publisher=self.publisher,
                channel_id=chan_id,
                image_id="img_publish_1",
                requester_user_id=1,
                requester_role="admin",
            )
            await session.commit()

            self.assertEqual(fid1, "tg_file_id_1")
            self.assertEqual(self.publisher.sent_photos[0]["photo"], "data/images/waifu.png")

            # Check that file_id was persisted in database
            res = await session.execute(
                ImageRecord.__table__.select().where(ImageRecord.image_id == "img_publish_1")
            )
            updated_img = res.fetchone()
            self.assertEqual(updated_img.telegram_file_id, "tg_file_id_1")

            # 2. Second publish of same image -> reuses cached telegram_file_id (R42)
            fid2 = await publish_image(
                session=session,
                publisher=self.publisher,
                channel_id=chan_id,
                image_id="img_publish_1",
                requester_user_id=1,
                requester_role="admin",
            )
            self.assertEqual(fid2, "tg_file_id_1")
            self.assertEqual(self.publisher.sent_photos[1]["photo"], "tg_file_id_1")

    async def test_users_can_only_publish_their_own_images(self) -> None:
        """User cannot publish an image belonging to another user (R59)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100999,
                title="User Channel",
                owner_user_id=10,
                status="active",
                scope="private",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=10,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)

            job = JobRecord(
                job_id="job_other_user",
                requester_user_id=99,  # Belongs to user 99
                admin_id=99,
                prompt="secret anime",
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(job)

            image = ImageRecord(
                image_id="img_other_user",
                file_path="data/images/other.png",
                job_id="job_other_user",
                final_prompt="secret anime",
                created_at=time.time(),
            )
            session.add(image)
            await session.commit()
            chan_id = channel.id

            # User 10 tries to publish User 99's image -> rejected
            with self.assertRaises(PublishError) as ctx:
                await publish_image(
                    session=session,
                    publisher=self.publisher,
                    channel_id=chan_id,
                    image_id="img_other_user",
                    requester_user_id=10,
                    requester_role="user",
                )
            self.assertEqual(ctx.exception.code, "not_authorized")

    async def test_publish_size_limit_and_api_error_handling(self) -> None:
        """Oversized images reject with publish_too_large; API failures report publish_failed (R24)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100111,
                title="Channel",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)

            job = JobRecord(
                job_id="job_large",
                requester_user_id=1,
                admin_id=1,
                prompt="giant art",
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(job)

            # 15 MB image
            image = ImageRecord(
                image_id="img_large",
                file_path="data/images/huge.png",
                job_id="job_large",
                final_prompt="giant art",
                file_size_bytes=15 * 1024 * 1024,
                created_at=time.time(),
            )
            session.add(image)
            await session.commit()
            chan_id = channel.id

            # 1. Size check with 10MB limit
            with self.assertRaises(PublishError) as ctx:
                await publish_image(
                    session=session,
                    publisher=self.publisher,
                    channel_id=chan_id,
                    image_id="img_large",
                    requester_user_id=1,
                    requester_role="admin",
                    max_image_mb=10.0,
                )
            self.assertEqual(ctx.exception.code, "publish_too_large")

            # 2. Telegram API failure simulation
            image.file_size_bytes = 1000
            await session.commit()
            self.publisher.fail_with = RuntimeError("Telegram network timeout")

            with self.assertRaises(PublishError) as ctx2:
                await publish_image(
                    session=session,
                    publisher=self.publisher,
                    channel_id=chan_id,
                    image_id="img_large",
                    requester_user_id=1,
                    requester_role="admin",
                )
            self.assertEqual(ctx2.exception.code, "publish_failed")


if __name__ == "__main__":
    unittest.main()
