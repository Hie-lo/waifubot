"""Unit tests for user self-service: generation, private My Images, and My Account (R5, R51, R54)."""

import json
import time
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.bot.auth import (
    RequestContext,
    get_role_reply_keyboard_buttons,
    verify_callback_authorization,
)
from waifubot.bot.handlers.user_generation import UserGenerationHandler
from waifubot.db.base import Base
from waifubot.db.models import ImageRecord, JobRecord, PlanRecord, UserRecord
from waifubot.services.quota import QuotaExceededError, get_user_usage_summary


class TestUserGeneration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        # Seed built-in free and staff plans
        async with self.session_factory() as session:
            free_plan = PlanRecord(
                id="free",
                name="Free",
                limits_json=json.dumps(
                    {
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
                ),
                is_builtin=True,
                created_at=time.time(),
            )
            staff_plan = PlanRecord(
                id="staff",
                name="Staff",
                limits_json=json.dumps(
                    {
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
                ),
                is_builtin=True,
                created_at=time.time(),
            )
            session.add_all([free_plan, staff_plan])

            # Seed user
            user = UserRecord(
                telegram_id=123,
                name="Normal User",
                plan_id="free",
                first_seen=time.time(),
                last_seen=time.time(),
            )
            session.add(user)
            await session.commit()

        self.handler = UserGenerationHandler()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_user_job_reserves_quota(self) -> None:
        """Creating a user job reserves quota and sets job_scope to 'user' (R51, R54)."""
        async with self.session_factory() as session:
            job = await self.handler.create_user_job(
                session=session,
                user_id=123,
                prompt="masterpiece, 1girl",
                target_count=4,
                role="user",
            )
            await session.commit()

            self.assertEqual(job.requester_user_id, 123)
            self.assertEqual(job.job_scope, "user")
            self.assertEqual(job.status, "pending")
            self.assertEqual(job.target_count, 4)

            # Quota is reserved: 4 images today
            summary = await get_user_usage_summary(session, 123)
            self.assertEqual(summary["images_today"], 4)
            self.assertEqual(summary["jobs_today"], 1)

    async def test_create_user_job_exceeds_max_images_per_job(self) -> None:
        """User cannot request more than max_images_per_job in free plan (4)."""
        async with self.session_factory() as session:
            with self.assertRaises(QuotaExceededError) as ctx:
                await self.handler.create_user_job(
                    session=session,
                    user_id=123,
                    prompt="masterpiece, 1girl",
                    target_count=8,  # Free limit is 4
                    role="user",
                )
            self.assertEqual(ctx.exception.limit_name, "max_images_per_job")

    async def test_user_job_settle_and_refund(self) -> None:
        """Settle refunds unused images at gate; failure refunds full reservation."""
        async with self.session_factory() as session:
            job = await self.handler.create_user_job(
                session=session,
                user_id=123,
                prompt="masterpiece",
                target_count=4,
                role="user",
            )
            await session.commit()

            # Partial success: gate kept 2 images out of 4 -> settles 2
            job.status = "succeeded"
            await self.handler.on_job_completed(session, job, role="user", kept_count=2)
            await session.commit()

            summary = await get_user_usage_summary(session, 123)
            self.assertEqual(summary["images_today"], 2)

            # Start second job with 4 images
            job2 = await self.handler.create_user_job(
                session=session,
                user_id=123,
                prompt="another masterpiece",
                target_count=4,
                role="user",
            )
            await session.commit()

            # Second job fails -> full refund of 4 images
            job2.status = "failed"
            await self.handler.on_job_completed(session, job2, role="user")
            await session.commit()

            summary2 = await get_user_usage_summary(session, 123)
            self.assertEqual(summary2["images_today"], 2)

    async def test_my_images_viewer_empty_and_populated(self) -> None:
        """My Images private viewer handles empty state and pagination (R5, R51)."""
        async with self.session_factory() as session:
            # 1. Empty state
            msg, kb, path = await self.handler.render_my_images_viewer(session, 123, page=0)
            self.assertIn("شما هنوز هیچ تصویری تولید نکرده‌اید", msg)
            self.assertIsNone(path)

            # 2. Add job and image
            job = JobRecord(
                job_id="job_usr_123_test",
                requester_user_id=123,
                admin_id=123,
                job_scope="user",
                prompt="test prompt",
                target_count=1,
                needed=0,
                status="succeeded",
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(job)
            await session.flush()

            img1 = ImageRecord(
                image_id="img_123_1",
                file_path="data/images/img1.png",
                job_id=job.job_id,
                final_prompt="test prompt",
                created_at=time.time(),
            )
            img2 = ImageRecord(
                image_id="img_123_2",
                file_path="data/images/img2.png",
                job_id=job.job_id,
                final_prompt="test prompt 2",
                created_at=time.time() + 1,
            )
            session.add_all([img1, img2])
            await session.commit()

            # Render page 0
            msg2, kb2, path2 = await self.handler.render_my_images_viewer(session, 123, page=0)
            self.assertIn("1/2", msg2)
            self.assertEqual(path2, "data/images/img2.png")  # newest first

            # Delete image 2
            deleted = await self.handler.delete_user_image(session, 123, img2.id)
            self.assertTrue(deleted)
            await session.commit()

            # Now total is 1
            _, total = await self.handler.list_user_images(session, 123)
            self.assertEqual(total, 1)

    async def test_render_my_account(self) -> None:
        """My Account displays active plan and usage metrics (R51)."""
        async with self.session_factory() as session:
            text, kb = await self.handler.render_my_account(session, 123, role="user")
            self.assertIn("FREE", text)
            self.assertIn("تصاویر امروز", text)
            self.assertIn("تصاویر ذخیره‌شده", text)
            self.assertGreaterEqual(len(kb), 1)

    def test_role_keyboards_and_callback_authorization(self) -> None:
        """Verifies role-based reply keyboards and staff-only callback rejection (R27, R51)."""
        user_ctx = RequestContext(telegram_id=123, role="user", is_blocked=False)
        staff_ctx = RequestContext(telegram_id=456, role="admin", is_blocked=False)

        user_btns = get_role_reply_keyboard_buttons(user_ctx)
        flat_user = [b for row in user_btns for b in row]
        self.assertIn("🎨 تولید تصویر", flat_user)
        self.assertIn("🖼 تصاویر من", flat_user)
        self.assertIn("👤 حساب کاربری من", flat_user)
        self.assertNotIn("⚙️ تنظیمات", flat_user)
        self.assertNotIn("📥 بررسی", flat_user)

        staff_btns = get_role_reply_keyboard_buttons(staff_ctx)
        flat_staff = [b for row in staff_btns for b in row]
        self.assertIn("⚙️ تنظیمات", flat_staff)
        self.assertIn("📥 بررسی", flat_staff)

        # Callback authorization (R27)
        self.assertFalse(verify_callback_authorization(user_ctx, "v1:sys:restart:confirm"))
        self.assertFalse(verify_callback_authorization(user_ctx, "v1:adm:role:123"))
        self.assertFalse(verify_callback_authorization(user_ctx, "v1:pln:del:vip"))
        self.assertTrue(verify_callback_authorization(staff_ctx, "v1:sys:restart:confirm"))
        self.assertTrue(verify_callback_authorization(user_ctx, "v1:myimg:view:1:0"))


if __name__ == "__main__":
    unittest.main()
