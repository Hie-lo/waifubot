"""Unit tests for ADMIN_IDS seeding, idempotency, and soft-removal protection (R44)."""

import os
import shutil
import tempfile
import time
import unittest

from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.admins import (
    get_admin,
    list_active_admins,
    remove_admin,
    seed_admins,
    set_admin_role,
)
from waifubot.services.audit import get_audit_logs


class TestAdminSeed(unittest.IsolatedAsyncioTestCase):
    """Test suite covering ADMIN_IDS insert-if-missing and soft-disable persistence."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_seed_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_seed_admins_inserts_missing_as_superadmin(self) -> None:
        """Missing admin IDs are inserted as superadmin with audit log."""
        admin_ids = [1001, 1002]

        async with self.session_factory() as session:
            async with session.begin():
                count = await seed_admins(session, admin_ids)
                self.assertEqual(count, 2)

        async with self.session_factory() as session:
            admins = await list_active_admins(session)
            self.assertEqual(len(admins), 2)
            for adm in admins:
                self.assertEqual(adm.role, "superadmin")
                self.assertIsNone(adm.removed_at)

            logs = await get_audit_logs(session)
            self.assertEqual(len(logs), 2)
            self.assertEqual(logs[0].action, "admin_seed")

    async def test_seed_admins_is_idempotent_and_preserves_roles(self) -> None:
        """Calling seed_admins again does not overwrite or alter existing roles."""
        admin_ids = [1001, 1002]

        # First seed
        async with self.session_factory() as session:
            async with session.begin():
                await seed_admins(session, admin_ids)
                # Demote 1002 to regular admin (with 1001 remaining as superadmin)
                await set_admin_role(session, telegram_id=1002, new_role="admin", actor_id=1001)

        # Second seed at next startup
        async with self.session_factory() as session:
            async with session.begin():
                count = await seed_admins(session, admin_ids)
                self.assertEqual(count, 0)  # No new admins inserted

        # Verify 1002 stayed as 'admin' and was NOT overwritten to superadmin
        async with self.session_factory() as session:
            adm_1002 = await get_admin(session, 1002)
            self.assertIsNotNone(adm_1002)
            assert adm_1002 is not None
            self.assertEqual(adm_1002.role, "admin")

    async def test_seed_admins_does_not_reseed_soft_removed_admins(self) -> None:
        """Soft-removed admins (removed_at is not None) are NOT re-seeded at startup (R44)."""
        admin_ids = [1001, 1002]

        # Seed both
        async with self.session_factory() as session:
            async with session.begin():
                await seed_admins(session, admin_ids)

        # Remove 1002 (soft-disable)
        async with self.session_factory() as session:
            async with session.begin():
                await remove_admin(session, telegram_id=1002, actor_id=1001)

        # Startup seed runs again with same admin_ids
        async with self.session_factory() as session:
            async with session.begin():
                count = await seed_admins(session, admin_ids)
                self.assertEqual(count, 0)

        # Verify 1002 is still inactive / soft-removed
        async with self.session_factory() as session:
            adm_1002 = await get_admin(session, 1002)
            self.assertIsNone(adm_1002)  # Active query returns None

            active_admins = await list_active_admins(session)
            self.assertEqual(len(active_admins), 1)
            self.assertEqual(active_admins[0].telegram_id, 1001)


if __name__ == "__main__":
    unittest.main()
