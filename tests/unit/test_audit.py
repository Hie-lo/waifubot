"""Unit tests for audit logging service (R44)."""

import os
import shutil
import tempfile
import unittest

from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.audit import (
    format_audit_entry,
    get_audit_logs,
    get_audit_logs_for_target,
    record_audit,
)


class TestAuditService(unittest.IsolatedAsyncioTestCase):
    """Test suite covering audit logging, serialization, queries, and formatting."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_audit_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_record_and_retrieve_audit_logs(self) -> None:
        """Audit entries are persisted and queryable in reverse chronological order."""
        async with self.session_factory() as session:
            async with session.begin():
                await record_audit(
                    session=session,
                    admin_id=1001,
                    action="setting_change",
                    target="worker_count",
                    old_val=2,
                    new_val=4,
                )
                await record_audit(
                    session=session,
                    admin_id=1001,
                    action="admin_add",
                    target="admin:2002",
                    old_val=None,
                    new_val={"role": "admin"},
                )

        async with self.session_factory() as session:
            logs = await get_audit_logs(session, limit=10)
            self.assertEqual(len(logs), 2)
            self.assertEqual(logs[0].action, "admin_add")
            self.assertEqual(logs[0].target, "admin:2002")
            self.assertIn('"role": "admin"', logs[0].new_val or "")

            self.assertEqual(logs[1].action, "setting_change")
            self.assertEqual(logs[1].target, "worker_count")
            self.assertEqual(logs[1].old_val, "2")
            self.assertEqual(logs[1].new_val, "4")

    async def test_get_audit_logs_for_target(self) -> None:
        """Target-specific filter retrieves only matching audit logs."""
        async with self.session_factory() as session:
            async with session.begin():
                await record_audit(session, 1001, "setting_change", "worker_count", 2, 4)
                await record_audit(session, 1001, "setting_change", "max_concurrent_jobs", 2, 4)
                await record_audit(session, 1001, "setting_change", "worker_count", 4, 6)

        async with self.session_factory() as session:
            target_logs = await get_audit_logs_for_target(session, target="worker_count")
            self.assertEqual(len(target_logs), 2)
            for entry in target_logs:
                self.assertEqual(entry.target, "worker_count")

    def test_format_audit_entry(self) -> None:
        """Verifies text formatting for bot views in Persian and English."""
        record = type("MockAudit", (), {
            "admin_id": 999,
            "action": "setting_change",
            "target": "challenge_wait_seconds",
            "old_val": "180",
            "new_val": "300",
        })()

        fa_str = format_audit_entry(record, locale="fa")
        self.assertIn("setting_change", fa_str)
        self.assertIn("999", fa_str)
        self.assertIn("challenge_wait_seconds", fa_str)
        self.assertIn("قبلی: 180", fa_str)
        self.assertIn("جدید: 300", fa_str)

        en_str = format_audit_entry(record, locale="en")
        self.assertIn("setting_change", en_str)
        self.assertIn("by 999", en_str)
        self.assertIn("old: 180", en_str)
        self.assertIn("new: 300", en_str)


if __name__ == "__main__":
    unittest.main()
