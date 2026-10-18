"""Unit tests for multi-schedule channel posting, Jalali dates, and missed-run policies (R45)."""

import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime

from waifubot.db.models import ChannelRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.scheduler import (
    advance_schedule,
    calculate_next_run,
    create_schedule,
    delete_schedule,
    evaluate_schedule_trigger,
    format_jalali_datetime,
    get_due_schedules,
    get_schedule,
    gregorian_to_jalali,
    list_schedules,
    set_schedule_running,
)


class TestSchedulerService(unittest.IsolatedAsyncioTestCase):
    """Test suite covering multi-schedule operations, Jalali calendar, and downtime recovery."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_sched_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_jalali_conversion_accuracy(self) -> None:
        """Verifies pure-Python Gregorian to Jalali conversion with known benchmark dates."""
        # Known benchmarks:
        # 2026-10-18 -> 1405-07-26 (Mehr 26, 1405)
        jy, jm, jd = gregorian_to_jalali(2026, 10, 18)
        self.assertEqual((jy, jm, jd), (1405, 7, 26))

        # 2024-03-20 -> 1403-01-01 (Nowruz)
        jy, jm, jd = gregorian_to_jalali(2024, 3, 20)
        self.assertEqual((jy, jm, jd), (1403, 1, 1))

        # 2025-01-01 -> 1403-10-12
        jy, jm, jd = gregorian_to_jalali(2025, 1, 1)
        self.assertEqual((jy, jm, jd), (1403, 10, 12))

    def test_format_jalali_datetime(self) -> None:
        """Verifies format_jalali_datetime generates localized strings."""
        # 2026-10-18 12:30:00 UTC
        dt = datetime(2026, 10, 18, 12, 30, 0)
        formatted = format_jalali_datetime(dt, tz_name="Asia/Tehran")
        # In Tehran (UTC+3:30), 12:30 UTC is 16:00
        self.assertTrue(formatted.startswith("1405/07/26"))
        self.assertIn("16:00", formatted)

    def test_calculate_next_run_bounds(self) -> None:
        """Calculated interval must be within min_gap and max_gap bounds."""
        now = 1000000.0
        min_gap = 10
        max_gap = 20
        for _ in range(20):
            next_at = calculate_next_run(min_gap, max_gap, from_time=now)
            self.assertGreaterEqual(next_at, now + (min_gap * 60))
            self.assertLessEqual(next_at, now + (max_gap * 60))

    def test_evaluate_schedule_trigger_and_policies(self) -> None:
        """Verifies trigger evaluation and policies (catch_up_one vs skip)."""
        now = time.time()
        # Schedule in the future should not trigger
        future_sched = type(
            "MockSchedule",
            (),
            {
                "is_running": True,
                "next_at": now + 500,
                "min_gap_minutes": 10,
                "max_gap_minutes": 20,
                "missed_run_policy": "catch_up_one",
            },
        )()
        should_run, next_at = evaluate_schedule_trigger(future_sched, now)
        self.assertFalse(should_run)
        self.assertEqual(next_at, now + 500)

        # Schedule past due (missed run due to downtime)
        past_sched = type(
            "MockSchedule",
            (),
            {
                "is_running": True,
                "next_at": now - 3600,
                "min_gap_minutes": 30,
                "max_gap_minutes": 60,
                "missed_run_policy": "catch_up_one",
            },
        )()
        should_run, new_next_at = evaluate_schedule_trigger(past_sched, now)
        self.assertTrue(should_run)
        self.assertGreaterEqual(new_next_at, now + (30 * 60))

    async def test_multi_schedule_crud_and_due_queries(self) -> None:
        """Verifies multi-schedule independent lifecycles and query filtering (R45)."""
        now = time.time()

        async with self.session_factory() as session, session.begin():
            ch1 = ChannelRecord(
                tg_chat_id=-100111111,
                title="Channel 1",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json="{}",
                added_by=1,
                created_at=now,
                updated_at=now,
            )
            ch2 = ChannelRecord(
                tg_chat_id=-100222222,
                title="Channel 2",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json="{}",
                added_by=1,
                created_at=now,
                updated_at=now,
            )
            session.add_all([ch1, ch2])
            await session.flush()

            s1 = await create_schedule(
                session=session,
                channel_id=ch1.id,
                topic_id=None,
                post_count=2,
                min_gap_minutes=60,
                max_gap_minutes=120,
                first_run_at=now - 100,  # due now
            )
            s2 = await create_schedule(
                session=session,
                channel_id=ch2.id,
                topic_id=42,
                post_count=1,
                min_gap_minutes=30,
                max_gap_minutes=60,
                first_run_at=now + 500,  # future
            )
            s1_id = s1.id
            s2_id = s2.id

        # Query due schedules
        async with self.session_factory() as session:
            due = await get_due_schedules(session, now=now)
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].id, s1_id)

            # Advance schedule 1
            await advance_schedule(session, s1_id, new_next_at=now + 1000)
            await session.commit()

        # Now neither should be due
        async with self.session_factory() as session:
            due = await get_due_schedules(session, now=now)
            self.assertEqual(len(due), 0)

            # Test pause/resume
            await set_schedule_running(session, s2_id, is_running=False)
            await session.commit()
            updated_s2 = await get_schedule(session, s2_id)
            self.assertIsNotNone(updated_s2)
            assert updated_s2 is not None
            self.assertFalse(updated_s2.is_running)

            # Test delete
            deleted = await delete_schedule(session, s1_id)
            self.assertTrue(deleted)
            await session.commit()

            all_remaining = await list_schedules(session)
            self.assertEqual(len(all_remaining), 1)
            self.assertEqual(all_remaining[0].id, s2_id)


if __name__ == "__main__":
    unittest.main()
