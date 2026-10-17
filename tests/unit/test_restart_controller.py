"""Unit tests for bot restart controller, pending marker lifecycle, and system controls (R49)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from waifubot.bot.handlers.system import SystemManagementHandler
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.system import RestartAlreadyInProgressError, RestartController


class MockGateway:
    """Mock Telegram gateway to verify panel editing on startup recovery."""

    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kwargs) -> None:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})


class MockShutdownService:
    """Mock shutdown service to verify graceful shutdown invocation."""

    def __init__(self) -> None:
        self.shutdown_called = False
        self.reason = ""

    async def graceful_shutdown(self, reason: str = "") -> None:
        self.shutdown_called = True
        self.reason = reason


class TestRestartController(unittest.IsolatedAsyncioTestCase):
    """Test suite covering restart pending markers, exit code 75, and startup panel edits (R49)."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_restart_test_")
        self.marker_path = Path(self.temp_dir) / "restart.pending"
        self.controller = RestartController(marker_path=self.marker_path)

        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_marker_lifecycle(self) -> None:
        """Marker is properly checked, persisted, read, and deleted."""
        self.assertFalse(self.controller.is_restart_pending())
        self.assertIsNone(self.controller.get_restart_pending())

        # Set marker
        self.controller.set_restart_pending(requested_by=1001, chat_id=-100222, message_id=456)
        self.assertTrue(self.controller.is_restart_pending())

        marker = self.controller.get_restart_pending()
        self.assertIsNotNone(marker)
        assert marker is not None
        self.assertEqual(marker["requested_by"], 1001)
        self.assertEqual(marker["chat_id"], -100222)
        self.assertEqual(marker["message_id"], 456)

        # Clear marker
        self.controller.clear_restart_pending()
        self.assertFalse(self.controller.is_restart_pending())
        self.assertIsNone(self.controller.get_restart_pending())

    async def test_duplicate_restart_rejected(self) -> None:
        """Duplicate restart request raises RestartAlreadyInProgressError (R7, R49)."""
        self.controller.set_restart_pending(1001, -100222, 456)

        with self.assertRaises(RestartAlreadyInProgressError):
            await self.controller.request_bot_restart(
                requested_by=1001,
                chat_id=-100222,
                message_id=456,
            )

    async def test_bot_restart_execution_exit_code_75(self) -> None:
        """Bot restart executes graceful shutdown and triggers exit with code 75 (R49)."""
        shutdown = MockShutdownService()
        captured_exit_codes: list[int] = []

        def mock_exit(code: int) -> None:
            captured_exit_codes.append(code)

        async with self.session_factory() as session, session.begin():
            await self.controller.request_bot_restart(
                requested_by=1001,
                chat_id=-100222,
                message_id=456,
                shutdown_service=shutdown,
                session=session,
                exit_code=75,
                exit_fn=mock_exit,
            )

        # Verify marker was saved
        self.assertTrue(self.controller.is_restart_pending())
        # Verify graceful shutdown was called
        self.assertTrue(shutdown.shutdown_called)
        self.assertEqual(shutdown.reason, "admin_restart")
        # Verify exit code was exactly 75
        self.assertEqual(captured_exit_codes, [75])

    async def test_startup_recovery_edits_panel_and_cleans_marker(self) -> None:
        """On startup recovery, panel at original coordinates is edited to 'restarted ✅' and marker is cleared."""
        # Setup pending marker
        self.controller.set_restart_pending(requested_by=1001, chat_id=-100222, message_id=456)

        gateway = MockGateway()
        recovered = await self.controller.handle_startup_recovery(
            telegram_gateway=gateway,
            recovered_jobs_count=3,
            locale="fa",
        )

        self.assertIsNotNone(recovered)
        self.assertEqual(len(gateway.edits), 1)
        edit = gateway.edits[0]
        self.assertEqual(edit["chat_id"], -100222)
        self.assertEqual(edit["message_id"], 456)
        self.assertIn("restarted ✅", edit["text"])
        self.assertIn("3", edit["text"])

        # Marker should now be cleaned up
        self.assertFalse(self.controller.is_restart_pending())

    def test_system_handler_rendering(self) -> None:
        """SystemManagementHandler properly renders main menu and restart confirmation."""
        handler = SystemManagementHandler()

        # Main menu
        menu_text, menu_kbd = handler.render_system_menu(locale="fa")
        self.assertIn("کنترل‌های سیستم", menu_text)
        self.assertEqual(len(menu_kbd), 3)

        # Confirm restart
        confirm_text, confirm_kbd = handler.render_restart_confirm(locale="fa")
        self.assertIn("تأیید راه‌اندازی مجدد ربات", confirm_text)
        self.assertEqual(len(confirm_kbd), 2)


if __name__ == "__main__":
    unittest.main()
