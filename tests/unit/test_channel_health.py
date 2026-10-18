"""Unit tests for channel health monitoring, lost access handling, and schedule pausing (R58)."""

import json
import time
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.unit.test_channel_verify import MockChannelProbe
from waifubot.db.base import Base
from waifubot.db.models import ChannelRecord, ScheduleRecord
from waifubot.services.channels import (
    ChannelUsabilityError,
    assert_usable,
    handle_bot_membership_update,
    reverify_channel,
)


class TestChannelHealth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.probe = MockChannelProbe()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_rights_lost_pauses_schedules_without_deleting(self) -> None:
        """When bot loses rights, channel becomes lost_access and schedules are paused (R58)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100777,
                title="Anime Schedule Channel",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.flush()

            # Create an active schedule on this channel
            schedule = ScheduleRecord(
                channel_id=channel.id,
                post_count=1,
                min_gap_minutes=60,
                max_gap_minutes=120,
                next_at=time.time() + 3600,
                is_running=True,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(schedule)
            await session.commit()
            sched_id = schedule.id
            chan_id = channel.id

        # Telegram probe now returns False for bot rights
        self.probe.chats[-100777] = {"id": -100777, "type": "channel"}
        self.probe.bot_rights[-100777] = {"can_post_messages": False}

        async with self.session_factory() as session:
            ok, status = await reverify_channel(session, self.probe, chan_id, 1, "superadmin")
            await session.commit()

            self.assertFalse(ok)
            self.assertEqual(status, "lost_access")

            # Check that schedule was PAUSED, NOT deleted (R58)
            res = await session.execute(select(ScheduleRecord).where(ScheduleRecord.id == sched_id))
            sched = res.scalar_one_or_none()
            self.assertIsNotNone(sched)
            self.assertFalse(sched.is_running)

    async def test_rights_restoration_reactivates_channel(self) -> None:
        """When rights return and re-verify succeeds, channel status becomes active (R58)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100888,
                title="Recovered Channel",
                owner_user_id=1,
                status="lost_access",
                scope="shared",
                bot_rights_json="{}",
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()
            chan_id = channel.id

        # Probe returns restored rights
        self.probe.chats[-100888] = {"id": -100888, "type": "channel"}
        self.probe.bot_rights[-100888] = {"can_post_messages": True}

        async with self.session_factory() as session:
            ok, status = await reverify_channel(session, self.probe, chan_id, 1, "superadmin")
            await session.commit()

            self.assertTrue(ok)
            self.assertEqual(status, "active")

    async def test_my_chat_member_lost_access_event(self) -> None:
        """Handling my_chat_member kick or demotion marks channel lost_access immediately (R58)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100999,
                title="Monitored Group",
                kind="group",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json=json.dumps({"can_send_media": True}),
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()

            # Bot kicked from chat
            updated = await handle_bot_membership_update(
                session=session,
                tg_chat_id=-100999,
                new_status="kicked",
                new_rights={},
            )
            await session.commit()

            self.assertIsNotNone(updated)
            self.assertEqual(updated.status, "lost_access")

    async def test_legacy_unverified_requires_reverify(self) -> None:
        """Legacy channels migrated from v4.0 schedules reject use until verified (R58)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100111,
                title="Legacy Channel",
                owner_user_id=0,
                status="legacy_unverified",
                scope="shared",
                bot_rights_json="{}",
                added_by=0,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()
            chan_id = channel.id

            with self.assertRaises(ChannelUsabilityError) as ctx:
                await assert_usable(session, chan_id, requester_user_id=1, requester_role="admin")
            self.assertEqual(ctx.exception.reason, "legacy_unverified")


if __name__ == "__main__":
    unittest.main()
