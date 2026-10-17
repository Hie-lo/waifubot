"""Unit tests for structured JSON logging, context variables, and status collection (R46)."""

import json
import logging
import os
import shutil
import tempfile
import unittest

from waifubot.bot.handlers.status import format_status_message
from waifubot.core.logging import (
    JsonFormatter,
    bind_log_context,
    clear_recent_errors,
    get_recent_errors,
)
from waifubot.db.models import JobRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.settings import SettingsService
from waifubot.services.status import collect_system_status, format_uptime


class TestObservability(unittest.IsolatedAsyncioTestCase):
    """Test suite covering structured JSON logs, context variables, and status metrics (R46)."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_obs_test_")
        self.log_file = os.path.join(self.temp_dir, "test.log")
        clear_recent_errors()

        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_structured_json_logging_with_context(self) -> None:
        """JsonFormatter attaches job_id, admin_id, provider, slot context (R46)."""
        formatter = JsonFormatter()
        logger = logging.getLogger("test_logger")

        with bind_log_context(job_id="job_abc123", admin_id=1001, provider="perchance", slot=2):
            record = logger.makeRecord(
                name="test_logger",
                level=logging.INFO,
                fn="worker.py",
                lno=42,
                msg="Starting job execution",
                args=(),
                exc_info=None,
            )
            json_str = formatter.format(record)
            payload = json.loads(json_str)

            self.assertEqual(payload["level"], "INFO")
            self.assertEqual(payload["msg"], "Starting job execution")
            self.assertEqual(payload["job_id"], "job_abc123")
            self.assertEqual(payload["admin_id"], 1001)
            self.assertEqual(payload["provider"], "perchance")
            self.assertEqual(payload["slot"], 2)

    def test_recent_errors_buffer(self) -> None:
        """Error-level records are automatically appended to the recent errors ring buffer."""
        formatter = JsonFormatter()
        logger = logging.getLogger("test_err_logger")

        record = logger.makeRecord(
            name="test_err_logger",
            level=logging.ERROR,
            fn="worker.py",
            lno=100,
            msg="Browser context crashed",
            args=(),
            exc_info=None,
        )
        formatter.format(record)

        errors = get_recent_errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["msg"], "Browser context crashed")

    def test_format_uptime_durations(self) -> None:
        """Uptime helper properly formats seconds into readable strings."""
        self.assertEqual(format_uptime(45), "45s")
        self.assertEqual(format_uptime(125), "2m 5s")
        self.assertEqual(format_uptime(3665), "1h 1m 5s")
        self.assertEqual(format_uptime(90061), "1d 1h 1m 1s")

    async def test_collect_system_status_and_formatting(self) -> None:
        """collect_system_status queries DB and settings to build /status response."""
        # Populate mock job records
        async with self.session_factory() as session, session.begin():
            j1 = JobRecord(
                job_id="job_pend_1",
                requester_user_id=1001,
                admin_id=1001,
                prompt="test",
                target_count=4,
                needed=4,
                status="pending",
            )
            j2 = JobRecord(
                job_id="job_run_1",
                requester_user_id=1001,
                admin_id=1001,
                prompt="test",
                target_count=4,
                needed=4,
                status="running",
            )
            session.add_all([j1, j2])

        settings = SettingsService({"worker_count": 4, "max_concurrent_jobs": 4})

        async with self.session_factory() as session:
            status = await collect_system_status(session=session, settings_service=settings)

            self.assertEqual(status["queue_depth"], 1)
            self.assertEqual(status["running_jobs"], 1)
            self.assertEqual(
                status["effective_concurrency"], 2
            )  # clamped by default browser_contexts=2
            self.assertTrue(status["free_ram_mb"] > 0)

            # Test message formatting in Persian and English
            fa_msg = format_status_message(status, locale="fa")
            self.assertIn("وضعیت سلامت ربات", fa_msg)
            self.assertIn("<b>1</b> کار در صف", fa_msg)

            en_msg = format_status_message(status, locale="en")
            self.assertIn("Bot Health", en_msg)
            self.assertIn("<b>1</b> jobs", en_msg)


if __name__ == "__main__":
    unittest.main()
