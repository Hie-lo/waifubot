"""Unit tests for ChannelsHandler UI rendering and user interactions (R56–R59)."""

import json
import time
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.unit.test_channel_verify import MockChannelProbe
from waifubot.bot.handlers.channels import ChannelsHandler
from waifubot.db.base import Base
from waifubot.db.models import ChannelRecord, ScheduleRecord


class TestChannelsUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.handler = ChannelsHandler()
        self.probe = MockChannelProbe()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_render_channels_list_for_staff_and_user(self) -> None:
        """Staff see all channels; users see only their owned channels (R59)."""
        async with self.session_factory() as session:
            ch1 = ChannelRecord(
                tg_chat_id=-1001,
                title="Staff Shared",
                owner_user_id=1,
                status="active",
                scope="shared",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=1,
                created_at=time.time(),
                updated_at=time.time(),
            )
            ch2 = ChannelRecord(
                tg_chat_id=-1002,
                title="User Personal",
                owner_user_id=10,
                status="active",
                scope="private",
                bot_rights_json=json.dumps({"can_post_messages": True}),
                added_by=10,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add_all([ch1, ch2])
            await session.commit()

            # 1. Staff sees both
            staff_text, staff_kb = await self.handler.render_channels_list(session, 1, "admin")
            self.assertIn("مدیریت کانال‌ها", staff_text)
            flat_staff = [b["text"] for row in staff_kb for b in row]
            self.assertTrue(any("Staff Shared" in t for t in flat_staff))
            self.assertTrue(any("User Personal" in t for t in flat_staff))

            # 2. User sees only owned channel
            user_text, user_kb = await self.handler.render_channels_list(session, 10, "user")
            self.assertIn("کانال‌های من", user_text)
            flat_user = [b["text"] for row in user_kb for b in row]
            self.assertTrue(any("User Personal" in t for t in flat_user))
            self.assertFalse(any("Staff Shared" in t for t in flat_user))

    async def test_render_channel_card_controls(self) -> None:
        """Channel card displays details and actionable buttons (re-verify, scope, delete) (R58, R59)."""
        async with self.session_factory() as session:
            channel = ChannelRecord(
                tg_chat_id=-1003,
                title="Card Channel",
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

            text, kb = await self.handler.render_channel_card(session, chan_id, 10, "user")
            self.assertIn("Card Channel", text)
            self.assertIn("خصوصی", text)

            flat_btns = [b["text"] for row in kb for b in row]
            self.assertTrue(any("راستی‌آزمایی مجدد" in t for t in flat_btns))
            self.assertTrue(any("اشتراکی" in t for t in flat_btns))
            self.assertTrue(any("حذف کانال" in t for t in flat_btns))

    async def test_execute_add_and_delete_channel(self) -> None:
        """Adds channel through handler and tests deletion safety guard (R56, R58)."""
        self.probe.chats[-1004] = {"id": -1004, "title": "New Chat", "type": "channel"}
        self.probe.bot_rights[-1004] = {"can_post_messages": True}

        async with self.session_factory() as session:
            ok, reason, ch = await self.handler.execute_add_channel(
                session=session,
                probe=self.probe,
                chat_ref=-1004,
                adder_user_id=1,
                adder_role="superadmin",
            )
            self.assertTrue(ok)
            self.assertIsNotNone(ch)
            await session.commit()
            chan_id = ch.id

            # Add active schedule to channel
            sched = ScheduleRecord(
                channel_id=chan_id,
                post_count=1,
                next_at=time.time() + 100,
                is_running=True,
                created_at=time.time(),
                updated_at=time.time(),
            )
            session.add(sched)
            await session.commit()

            # Delete blocked by schedule guard
            del_ok, del_err = await self.handler.execute_delete_channel(
                session, chan_id, 1, "superadmin"
            )
            self.assertFalse(del_ok)
            self.assertIn("active schedules", del_err)

            # Force delete or paused schedule
            sched.is_running = False
            await session.commit()

            del_ok2, _ = await self.handler.execute_delete_channel(
                session, chan_id, 1, "superadmin"
            )
            self.assertTrue(del_ok2)


if __name__ == "__main__":
    unittest.main()
