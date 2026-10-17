"""Unit tests for Quota accounting and atomic reservation service (R54, R55)."""

import time
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.core.limits import Limits, effective_limits
from waifubot.db.base import Base
from waifubot.db.models import JobRecord
from waifubot.services.quota import (
    QuotaExceededError,
    check_and_reserve,
    get_current_period_key,
    get_user_usage_summary,
    refund_reservation,
    settle_reservation,
)


class TestQuotaService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_superadmin_short_circuits(self) -> None:
        """Superadmin is completely exempt from all limits (R53)."""
        async with self.session_factory() as session:
            limits = effective_limits("superadmin")
            # Should not raise even with ridiculous amount
            reserved = await check_and_reserve(
                session=session,
                user_id=1,
                role="superadmin",
                limits=limits,
                kind="generation",
                amount=1000,
            )
            self.assertTrue(reserved)

    async def test_generation_max_images_per_job(self) -> None:
        """Single job cannot request more than max_images_per_job."""
        async with self.session_factory() as session:
            limits = effective_limits("user")  # max_images_per_job is 4
            with self.assertRaises(QuotaExceededError) as ctx:
                await check_and_reserve(
                    session=session,
                    user_id=10,
                    role="user",
                    limits=limits,
                    kind="generation",
                    amount=8,
                )
            self.assertEqual(ctx.exception.limit_name, "max_images_per_job")

    async def test_generation_max_concurrent_jobs(self) -> None:
        """Cannot start a new job when active jobs reach max_concurrent_jobs."""
        async with self.session_factory() as session:
            limits = effective_limits("user")  # max_concurrent_jobs is 1
            # Add an active job
            job = JobRecord(
                job_id="job_active_1",
                requester_user_id=20,
                admin_id=20,
                prompt="anime girl",
                status="running",
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(job)
            await session.commit()

            with self.assertRaises(QuotaExceededError) as ctx:
                await check_and_reserve(
                    session=session,
                    user_id=20,
                    role="user",
                    limits=limits,
                    kind="generation",
                    amount=2,
                )
            self.assertEqual(ctx.exception.limit_name, "max_concurrent_jobs")

    async def test_generation_daily_budget_and_settlement(self) -> None:
        """Daily budget is reserved at start and settled at exact-count gate (R54)."""
        async with self.session_factory() as session:
            limits = Limits(
                max_owned_characters=3,
                daily_image_budget=6,
                max_images_per_job=4,
                max_concurrent_jobs=5,
                max_stored_images=100,
                daily_chat_messages=60,
                chat_memory_tokens=500,
                max_channels=0,
                queue_priority=0,
            )

            # Reserve 4 images
            await check_and_reserve(
                session=session,
                user_id=30,
                role="user",
                limits=limits,
                kind="generation",
                amount=4,
            )
            await session.commit()

            summary = await get_user_usage_summary(session, 30)
            self.assertEqual(summary["images_today"], 4)
            self.assertEqual(summary["jobs_today"], 1)

            # Attempting to reserve 4 more will breach budget of 6 (4 + 4 > 6)
            with self.assertRaises(QuotaExceededError) as ctx:
                await check_and_reserve(
                    session=session,
                    user_id=30,
                    role="user",
                    limits=limits,
                    kind="generation",
                    amount=4,
                )
            self.assertEqual(ctx.exception.limit_name, "daily_image_budget")
            self.assertIsNotNone(ctx.exception.reset_at)

            # Gate settlement: suppose job delivered only 2 valid images -> refund 2
            await settle_reservation(
                session=session,
                user_id=30,
                role="user",
                reserved_amount=4,
                actual_amount=2,
            )
            await session.commit()

            summary2 = await get_user_usage_summary(session, 30)
            self.assertEqual(summary2["images_today"], 2)

            # Now reserving 4 more succeeds because 2 + 4 <= 6
            reserved2 = await check_and_reserve(
                session=session,
                user_id=30,
                role="user",
                limits=limits,
                kind="generation",
                amount=4,
            )
            self.assertTrue(reserved2)

    async def test_generation_failed_job_refund(self) -> None:
        """Failed or cancelled jobs refund the entire reservation (R54)."""
        async with self.session_factory() as session:
            limits = effective_limits("user")
            await check_and_reserve(
                session=session,
                user_id=40,
                role="user",
                limits=limits,
                kind="generation",
                amount=4,
            )
            await session.commit()

            summary = await get_user_usage_summary(session, 40)
            self.assertEqual(summary["images_today"], 4)
            self.assertEqual(summary["jobs_today"], 1)

            # Job fails -> refund all 4 images
            await refund_reservation(
                session=session,
                user_id=40,
                role="user",
                amount=4,
            )
            await session.commit()

            summary_after = await get_user_usage_summary(session, 40)
            self.assertEqual(summary_after["images_today"], 0)
            self.assertEqual(summary_after["jobs_today"], 0)

    async def test_chat_messages_quota(self) -> None:
        """Chat messages quota increments counter and rejects when limit reached."""
        async with self.session_factory() as session:
            limits = Limits(
                max_owned_characters=3,
                daily_image_budget=8,
                max_images_per_job=4,
                max_concurrent_jobs=1,
                max_stored_images=100,
                daily_chat_messages=2,
                chat_memory_tokens=500,
                max_channels=0,
                queue_priority=0,
            )

            await check_and_reserve(session, 50, "user", limits, "chat", amount=1)
            await check_and_reserve(session, 50, "user", limits, "chat", amount=1)

            with self.assertRaises(QuotaExceededError) as ctx:
                await check_and_reserve(session, 50, "user", limits, "chat", amount=1)
            self.assertEqual(ctx.exception.limit_name, "daily_chat_messages")

    async def test_tehran_timezone_period_key(self) -> None:
        """Verifies daily period keys and reset times align with Tehran timezone."""
        # 2026-10-18 20:00 UTC is 2026-10-18 23:30 Tehran time (UTC+3:30)
        t1 = 1792353600.0  # approximate timestamp
        k1, reset_at = get_current_period_key("Asia/Tehran", now=t1)
        self.assertRegex(k1, r"^\d{4}-\d{2}-\d{2}$")
        self.assertGreater(reset_at, t1)


if __name__ == "__main__":
    unittest.main()
