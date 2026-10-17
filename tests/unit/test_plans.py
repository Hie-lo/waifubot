"""Unit tests for Plan management and user plan resolution (R52, R53)."""

import json
import time
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.db.base import Base
from waifubot.db.models import PlanRecord, UserRecord
from waifubot.services.plans import (
    BuiltinPlanError,
    assign_user_plan,
    create_plan,
    delete_plan,
    get_effective_user_limits,
    get_plan,
    list_plans,
    set_user_limit_overrides,
    update_plan,
)


class TestPlansService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        # Seed built-in plans
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
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_list_plans_includes_builtins(self) -> None:
        async with self.session_factory() as session:
            plans = await list_plans(session)
            plan_ids = [p.id for p in plans]
            self.assertIn("free", plan_ids)
            self.assertIn("staff", plan_ids)

    async def test_create_and_update_custom_plan(self) -> None:
        async with self.session_factory() as session:
            plan = await create_plan(
                session=session,
                plan_id="vip",
                name="VIP Plan",
                limits={
                    "daily_image_budget": 50,
                    "max_images_per_job": 8,
                    "queue_priority": 5,
                },
                created_by=111,
            )
            await session.commit()
            self.assertEqual(plan.id, "vip")
            self.assertFalse(plan.is_builtin)

            # Update plan limits and name
            updated = await update_plan(
                session=session,
                plan_id="vip",
                name="VIP Plus",
                limits={"daily_image_budget": 100},
                admin_id=111,
            )
            await session.commit()
            self.assertEqual(updated.name, "VIP Plus")
            limits = json.loads(updated.limits_json)
            self.assertEqual(limits["daily_image_budget"], 100)
            self.assertEqual(limits["max_images_per_job"], 8)

    async def test_delete_plan_builtins_rejected(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaises(BuiltinPlanError):
                await delete_plan(session, "free", replacement_plan_id="staff")
            with self.assertRaises(BuiltinPlanError):
                await delete_plan(session, "staff", replacement_plan_id="free")

    async def test_delete_plan_migrates_users(self) -> None:
        async with self.session_factory() as session:
            # Create custom plan
            await create_plan(session, "pro", "Pro", {"daily_image_budget": 20})

            # Create user on pro plan
            user = UserRecord(
                telegram_id=999,
                name="Test User",
                plan_id="pro",
                first_seen=time.time(),
                last_seen=time.time(),
            )
            session.add(user)
            await session.commit()

            # Delete pro plan with replacement free
            await delete_plan(session, "pro", replacement_plan_id="free", admin_id=111)
            await session.commit()

            self.assertIsNone(await get_plan(session, "pro"))
            # User should now be on free plan
            res = await session.execute(
                UserRecord.__table__.select().where(UserRecord.telegram_id == 999)
            )
            migrated_user = res.fetchone()
            self.assertIsNotNone(migrated_user)
            self.assertEqual(migrated_user.plan_id, "free")

    async def test_user_plan_assignment_and_expiry(self) -> None:
        async with self.session_factory() as session:
            await create_plan(session, "trial", "Trial", {"daily_image_budget": 30})

            user = UserRecord(
                telegram_id=555,
                name="Trial User",
                plan_id="free",
                first_seen=time.time(),
                last_seen=time.time(),
            )
            session.add(user)
            await session.commit()

            # Assign trial plan with past expiry
            now = 1000.0
            expired_at = now - 10.0
            await assign_user_plan(session, 555, "trial", expires_at=expired_at, admin_id=111)
            await session.commit()

            # Resolution should detect expiry and revert to default free plan
            limits, active_plan_id = await get_effective_user_limits(
                session=session,
                user_id=555,
                role="user",
                default_plan_id="free",
                now=now,
            )
            await session.commit()
            self.assertEqual(active_plan_id, "free")
            self.assertEqual(limits.daily_image_budget, 8)

    async def test_user_limit_overrides_applied(self) -> None:
        async with self.session_factory() as session:
            user = UserRecord(
                telegram_id=777,
                name="Special User",
                plan_id="free",
                first_seen=time.time(),
                last_seen=time.time(),
            )
            session.add(user)
            await session.commit()

            await set_user_limit_overrides(
                session=session,
                user_id=777,
                overrides={"daily_image_budget": 999, "max_images_per_job": 16},
                admin_id=111,
            )
            await session.commit()

            limits, _ = await get_effective_user_limits(session, 777, "user")
            self.assertEqual(limits.daily_image_budget, 999)
            self.assertEqual(limits.max_images_per_job, 16)
            self.assertEqual(limits.max_owned_characters, 3)  # unchanged from plan


if __name__ == "__main__":
    unittest.main()
