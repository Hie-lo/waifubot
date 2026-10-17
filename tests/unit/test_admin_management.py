"""Unit tests for admin management, last-superadmin invariant, and invite links (R48)."""

import os
import shutil
import tempfile
import time
import unittest

from waifubot.bot.handlers.admins import AdminManagementHandler
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.admins import (
    AdminAlreadyExistsError,
    AdminNotFoundError,
    InviteExpiredError,
    InviteInvalidError,
    LastSuperadminError,
    add_or_restore_admin,
    consume_invite,
    create_invite,
    get_admin,
    list_active_admins,
    remove_admin,
    seed_admins,
    set_admin_role,
)


class TestAdminManagement(unittest.IsolatedAsyncioTestCase):
    """Test suite covering admin promotion/demotion, last-superadmin invariant, and invite links."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_adm_mgmt_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

        # Seed initial single superadmin
        async with self.session_factory() as session:
            async with session.begin():
                await seed_admins(session, [1001])

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_last_superadmin_invariant_demote_and_remove(self) -> None:
        """The last active superadmin cannot be demoted or removed (R48)."""
        async with self.session_factory() as session:
            async with session.begin():
                # Attempt to demote the sole superadmin
                with self.assertRaises(LastSuperadminError):
                    await set_admin_role(session, telegram_id=1001, new_role="admin", actor_id=1001)

                # Attempt to remove the sole superadmin
                with self.assertRaises(LastSuperadminError):
                    await remove_admin(session, telegram_id=1001, actor_id=1001)

        # Add a second superadmin
        async with self.session_factory() as session:
            async with session.begin():
                await add_or_restore_admin(session, telegram_id=2002, role="superadmin", added_by=1001)

        # Now 1001 can be demoted because 2002 remains superadmin
        async with self.session_factory() as session:
            async with session.begin():
                demoted = await set_admin_role(session, telegram_id=1001, new_role="admin", actor_id=2002)
                self.assertEqual(demoted.role, "admin")

            # But 2002 cannot now be demoted or removed (since 2002 is now the last superadmin!)
            async with session.begin():
                with self.assertRaises(LastSuperadminError):
                    await set_admin_role(session, telegram_id=2002, new_role="admin", actor_id=2002)
                with self.assertRaises(LastSuperadminError):
                    await remove_admin(session, telegram_id=2002, actor_id=2002)

    async def test_restore_soft_removed_admin(self) -> None:
        """Adding a previously soft-removed admin restores them (R48)."""
        async with self.session_factory() as session:
            async with session.begin():
                # Add regular admin 3003
                await add_or_restore_admin(session, telegram_id=3003, role="admin", added_by=1001)
                # Remove 3003
                await remove_admin(session, telegram_id=3003, actor_id=1001)

        # Verify 3003 is soft-removed
        async with self.session_factory() as session:
            adm = await get_admin(session, 3003)
            self.assertIsNone(adm)

        # Restore 3003 with role superadmin
        async with self.session_factory() as session:
            async with session.begin():
                restored = await add_or_restore_admin(session, telegram_id=3003, role="superadmin", added_by=1001)
                self.assertIsNone(restored.removed_at)
                self.assertEqual(restored.role, "superadmin")

        # Verify active in list
        async with self.session_factory() as session:
            active = await list_active_admins(session)
            tg_ids = [a.telegram_id for a in active]
            self.assertIn(3003, tg_ids)

    async def test_single_use_expiring_invite_link_lifecycle(self) -> None:
        """Invite link creation, successful consumption, and failure on replay or expiry (R48)."""
        # Create invite token
        async with self.session_factory() as session:
            async with session.begin():
                raw_token, invite = await create_invite(
                    session=session,
                    created_by=1001,
                    role="admin",
                    ttl_hours=48.0,
                )
                self.assertTrue(len(raw_token) >= 24)
                self.assertIsNone(invite.used_by)

        # Consume invite
        async with self.session_factory() as session:
            async with session.begin():
                admin_rec, used_invite = await consume_invite(
                    session=session,
                    raw_token=raw_token,
                    user_id=5005,
                )
                self.assertEqual(admin_rec.telegram_id, 5005)
                self.assertEqual(admin_rec.role, "admin")
                self.assertEqual(used_invite.used_by, 5005)

        # Attempt to consume again (replay rejection)
        async with self.session_factory() as session:
            async with session.begin():
                with self.assertRaises(InviteInvalidError):
                    await consume_invite(session=session, raw_token=raw_token, user_id=6006)

        # Expired invite test
        async with self.session_factory() as session:
            async with session.begin():
                exp_token, exp_invite = await create_invite(
                    session=session,
                    created_by=1001,
                    role="admin",
                    ttl_hours=-1.0,  # Expired in past
                )
                with self.assertRaises(InviteExpiredError):
                    await consume_invite(session=session, raw_token=exp_token, user_id=7007)

    async def test_admin_management_handler_views(self) -> None:
        """AdminManagementHandler properly renders list and card screens."""
        handler = AdminManagementHandler(bot_username="test_waifu_bot")

        async with self.session_factory() as session:
            # Render list
            text, keyboard = await handler.render_admin_list(session, locale="fa")
            self.assertIn("مدیریت ادمین‌ها", text)
            self.assertIn("1001", text)
            self.assertTrue(len(keyboard) >= 2)

            # Render card for 1001
            text_card, kbd_card = await handler.render_admin_card(session, target_tg_id=1001, locale="fa")
            self.assertIn("1001", text_card)
            self.assertIn("سوپرادمین", text_card)
            self.assertTrue(len(kbd_card) >= 3)


if __name__ == "__main__":
    unittest.main()
