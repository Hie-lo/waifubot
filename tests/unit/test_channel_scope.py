"""Unit tests for channel scopes (private vs shared), usability assertions, and schedule guards (R58, R59)."""

import json
import time
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.db.base import Base
from waifubot.db.models import ChannelRecord, ScheduleRecord
from waifubot.services.channels import (
    ChannelUsabilityError,
    assert_usable,
    delete_channel,
    get_channel,
    set_channel_scope,
    set_channel_status,
)


class TestChannelScope(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_private_channel_usable_only_by_owner_and_superadmin(self) -> None:
        """Private channels are usable by owner and superadmin, not other users/admins (R59)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100222,
                title="User's Private Channel",
                owner_user_id=100,
                status="active",
                scope="private",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=100,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()
            chan_id = channel.id

            # 1. Owner can use it
            ch1 = await assert_usable(
                session, chan_id, requester_user_id=100, requester_role="user"
            )
            self.assertEqual(ch1.id, chan_id)

            # 2. Superadmin can use it
            ch2 = await assert_usable(
                session, chan_id, requester_user_id=999, requester_role="superadmin"
            )
            self.assertEqual(ch2.id, chan_id)

            # 3. Another admin cannot use private channel
            with self.assertRaises(ChannelUsabilityError) as ctx:
                await assert_usable(session, chan_id, requester_user_id=200, requester_role="admin")
            self.assertEqual(ctx.exception.reason, "permission_denied")

            # 4. Another user cannot use private channel
            with self.assertRaises(ChannelUsabilityError) as ctx2:
                await assert_usable(session, chan_id, requester_user_id=300, requester_role="user")
            self.assertEqual(ctx2.exception.reason, "permission_denied")

    async def test_shared_channel_usable_by_all_staff(self) -> None:
        """Shared channels are usable by all staff (admins and superadmins) (R59)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100333,
                title="Staff Shared Channel",
                owner_user_id=50,
                status="active",
                scope="shared",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=50,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()
            chan_id = channel.id

            # Staff admin (not the owner) can use shared channel
            ch = await assert_usable(
                session, chan_id, requester_user_id=200, requester_role="admin"
            )
            self.assertEqual(ch.id, chan_id)

            # Regular user (not the owner) cannot use shared channel
            with self.assertRaises(ChannelUsabilityError) as ctx:
                await assert_usable(session, chan_id, requester_user_id=300, requester_role="user")
            self.assertEqual(ctx.exception.reason, "permission_denied")

    async def test_set_channel_scope_permissions(self) -> None:
        """Only channel owner or superadmin can change channel scope (R59)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100444,
                title="Personal Channel",
                owner_user_id=10,
                status="active",
                scope="private",
                bot_rights_json="{}",
                added_by=10,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()
            chan_id = channel.id

            # Other user/admin fails
            with self.assertRaises(PermissionError):
                await set_channel_scope(
                    session, chan_id, "shared", requester_user_id=20, requester_role="admin"
                )

            # Owner succeeds
            await set_channel_scope(
                session, chan_id, "shared", requester_user_id=10, requester_role="user"
            )
            await session.commit()
            ch = await get_channel(session, chan_id)
            self.assertEqual(ch.scope, "shared")

    async def test_disabled_channel_usability_check(self) -> None:
        """Disabled channel cannot be used even by owner until re-enabled (R58)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100555,
                title="Paused Channel",
                owner_user_id=10,
                status="active",
                scope="private",
                bot_rights_json="{}",
                added_by=10,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.commit()
            chan_id = channel.id

            await set_channel_status(
                session, chan_id, "disabled", requester_user_id=10, requester_role="user"
            )
            await session.commit()

            with self.assertRaises(ChannelUsabilityError) as ctx:
                await assert_usable(session, chan_id, requester_user_id=10, requester_role="user")
            self.assertEqual(ctx.exception.reason, "disabled")

    async def test_delete_channel_with_active_schedule_guard(self) -> None:
        """Channel removal is blocked if active schedules exist on it (R58)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-100666,
                title="Scheduled Channel",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json="{}",
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(channel)
            await session.flush()

            schedule = ScheduleRecord(
                channel_id=channel.id,
                post_count=1,
                next_at=time.time() + 1000,
                is_running=True,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(schedule)
            await session.commit()
            chan_id = channel.id

            # Deletion should be blocked by active schedule guard
            with self.assertRaises(ValueError) as ctx:
                await delete_channel(
                    session, chan_id, requester_user_id=1, requester_role="superadmin"
                )
            self.assertIn("active schedules", str(ctx.exception))

            # Pause the schedule
            schedule.is_running = False
            await session.commit()

            # Now deletion succeeds
            await delete_channel(session, chan_id, requester_user_id=1, requester_role="superadmin")
            await session.commit()
            self.assertIsNone(await get_channel(session, chan_id))


if __name__ == "__main__":
    unittest.main()
