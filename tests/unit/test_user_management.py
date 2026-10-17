"""Unit tests for user management, overview stats, blocking, and auth middleware (R50)."""

import os
import shutil
import tempfile
import time
import unittest

from waifubot.bot.auth import check_access_and_notify, reset_blocked_notices, resolve_request_context
from waifubot.bot.handlers.users import UserManagementHandler
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.admins import seed_admins
from waifubot.services.users import (
    CannotBlockAdminError,
    block_user,
    delete_user_record,
    get_user,
    get_users_overview,
    list_users,
    record_user_seen,
    set_user_locale,
    set_user_note,
    unblock_user,
)


class MockBot:
    """Mock Telegram bot to capture outbound messages."""

    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text, **kwargs})


class TestUserManagement(unittest.IsolatedAsyncioTestCase):
    """Test suite covering user tracking, overview metrics, search, blocking, and middleware."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_usr_mgmt_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)
        reset_blocked_notices()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_record_user_seen_upsert(self) -> None:
        """First interaction creates profile (is_new=True), subsequent update last_seen (is_new=False)."""
        async with self.session_factory() as session:
            async with session.begin():
                user1, is_new1 = await record_user_seen(
                    session=session,
                    telegram_id=111,
                    name="Alice",
                    username="alice_tg",
                    locale="fa",
                )
                self.assertTrue(is_new1)
                self.assertEqual(user1.telegram_id, 111)
                self.assertEqual(user1.status, "active")

            async with session.begin():
                user2, is_new2 = await record_user_seen(
                    session=session,
                    telegram_id=111,
                    name="Alice Updated",
                    username="alice_new",
                )
                self.assertFalse(is_new2)
                self.assertEqual(user2.name, "Alice Updated")
                self.assertEqual(user2.username, "alice_new")

    async def test_overview_metrics_and_list_filtering(self) -> None:
        """Overview counts and filter tabs (all, active, blocked, new) return correct counts."""
        async with self.session_factory() as session:
            async with session.begin():
                # 3 active users, 1 blocked user
                await record_user_seen(session, 101, "User 1", "u1")
                await record_user_seen(session, 102, "User 2", "u2")
                await record_user_seen(session, 103, "User 3", "u3")
                await record_user_seen(session, 104, "User 4", "u4")
                await block_user(session, 104, reason="Spam", admin_id=999)

        async with self.session_factory() as session:
            stats = await get_users_overview(session)
            self.assertEqual(stats["total"], 4)
            self.assertEqual(stats["active_today"], 4)
            self.assertEqual(stats["blocked"], 1)

            # Test list with filters
            all_users, count_all = await list_users(session, filter_type="all")
            self.assertEqual(count_all, 4)
            self.assertEqual(len(all_users), 4)

            blocked_users, count_blocked = await list_users(session, filter_type="blocked")
            self.assertEqual(count_blocked, 1)
            self.assertEqual(blocked_users[0].telegram_id, 104)

            # Test search by username
            matched, count_m = await list_users(session, search_query="u2")
            self.assertEqual(count_m, 1)
            self.assertEqual(matched[0].telegram_id, 102)

            # Test search by numeric Telegram ID
            matched_id, count_id = await list_users(session, search_query="103")
            self.assertEqual(count_id, 1)
            self.assertEqual(matched_id[0].telegram_id, 103)

    async def test_cannot_block_active_admin_invariant(self) -> None:
        """Blocking an active administrator is forbidden (R50: change role in R48 first)."""
        async with self.session_factory() as session:
            async with session.begin():
                await seed_admins(session, [9001])
                await record_user_seen(session, 9001, "Admin User")

            async with session.begin():
                with self.assertRaises(CannotBlockAdminError):
                    await block_user(session, telegram_id=9001, reason="Test", admin_id=1001)

    async def test_user_card_actions_and_profile_deletion(self) -> None:
        """Note setting, unblocking, and full record deletion lifecycle."""
        async with self.session_factory() as session:
            async with session.begin():
                await record_user_seen(session, 555, "Bob", "bob555")
                await block_user(session, 555, reason="Violation", admin_id=1001)
                await set_user_note(session, 555, note="Watch closely", admin_id=1001)

        async with self.session_factory() as session:
            u = await get_user(session, 555)
            self.assertIsNotNone(u)
            assert u is not None
            self.assertEqual(u.status, "blocked")
            self.assertEqual(u.note, "Watch closely")

            # Unblock
            await unblock_user(session, 555, admin_id=1001)
            await session.commit()

            u_unblocked = await get_user(session, 555)
            assert u_unblocked is not None
            self.assertEqual(u_unblocked.status, "active")
            self.assertIsNone(u_unblocked.block_reason)

            # Delete record
            deleted = await delete_user_record(session, 555, admin_id=1001)
            self.assertTrue(deleted)
            await session.commit()

            self.assertIsNone(await get_user(session, 555))

    async def test_blocked_user_middleware_notice_once(self) -> None:
        """Blocked user receives blocked_notice exactly once per session and is rejected (R50)."""
        mock_bot = MockBot()

        async with self.session_factory() as session:
            async with session.begin():
                await record_user_seen(session, 777, "Eve", "eve_tg")
                await block_user(session, 777, reason="Abuse", admin_id=1001)

        # First interaction: rejected, notice sent
        async with self.session_factory() as session:
            is_allowed, ctx = await check_access_and_notify(
                session=session,
                telegram_id=777,
                name="Eve",
                bot=mock_bot,
            )
            self.assertFalse(is_allowed)
            self.assertTrue(ctx.is_blocked)
            self.assertEqual(len(mock_bot.sent_messages), 1)
            self.assertIn("مسدود", mock_bot.sent_messages[0]["text"])

        # Second interaction in same session: rejected, NO duplicate notice sent
        async with self.session_factory() as session:
            is_allowed2, _ = await check_access_and_notify(
                session=session,
                telegram_id=777,
                name="Eve",
                bot=mock_bot,
            )
            self.assertFalse(is_allowed2)
            self.assertEqual(len(mock_bot.sent_messages), 1)  # Still 1!

        # After session restart / reset
        reset_blocked_notices()
        async with self.session_factory() as session:
            is_allowed3, _ = await check_access_and_notify(
                session=session,
                telegram_id=777,
                name="Eve",
                bot=mock_bot,
            )
            self.assertFalse(is_allowed3)
            self.assertEqual(len(mock_bot.sent_messages), 2)  # Notice sent once in new session

    async def test_user_management_handler_render(self) -> None:
        """UserManagementHandler renders overview, list, and user card screens."""
        handler = UserManagementHandler()

        async with self.session_factory() as session:
            async with session.begin():
                await record_user_seen(session, 888, "Charlie", "charlie_tg")

            text_ovw, kbd_ovw = await handler.render_overview(session, locale="fa")
            self.assertIn("نمای کلی کاربران", text_ovw)
            self.assertTrue(len(kbd_ovw) >= 3)

            text_list, kbd_list = await handler.render_user_list(session, filter_type="all", locale="fa")
            self.assertIn("لیست کاربران", text_list)
            self.assertIn("charlie_tg", text_list)

            text_card, kbd_card = await handler.render_user_card(
                session,
                target_tg_id=888,
                viewer_is_superadmin=True,
                locale="fa",
            )
            self.assertIn("Charlie", text_card)
            self.assertIn("پروفایل کاربر", text_card)
            self.assertTrue(len(kbd_card) >= 3)


if __name__ == "__main__":
    unittest.main()
