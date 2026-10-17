"""Unit tests for PanelEngine, per-admin lock, panel versioning, and edit fallback (R2–R4, R40)."""

import unittest
from typing import Any

from waifubot.bot.panel import PanelEngine, PanelLockManager, PanelState


class MockTelegramGateway:
    """Mock gateway recording Telegram API operations."""

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.deleted_messages: list[dict[str, Any]] = []
        self.message_counter = 100
        self.fail_edit_with: Exception | None = None

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> Any:
        self.message_counter += 1
        msg = type(
            "Msg", (), {"message_id": self.message_counter, "chat_id": chat_id, "text": text}
        )()
        self.sent_messages.append(
            {"message_id": self.message_counter, "chat_id": chat_id, "text": text}
        )
        return msg

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: Any = None
    ) -> Any:
        if self.fail_edit_with:
            raise self.fail_edit_with
        self.edited_messages.append({"chat_id": chat_id, "message_id": message_id, "text": text})
        return type("Msg", (), {"message_id": message_id, "chat_id": chat_id, "text": text})()

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return True


class TestPanelEngine(unittest.IsolatedAsyncioTestCase):
    """Test suite covering panel lifecycle, locking, and fallback contracts."""

    async def test_panel_lock_manager(self) -> None:
        """LockManager returns the same lock for the same admin, different locks for different admins."""
        manager = PanelLockManager()
        lock1 = manager.get_lock(admin_id=101)
        lock2 = manager.get_lock(admin_id=101)
        lock_other = manager.get_lock(admin_id=202)

        self.assertIs(lock1, lock2)
        self.assertIsNot(lock1, lock_other)

    async def test_panel_versioning_and_stale_rejection(self) -> None:
        """Stale or duplicate callback versions are rejected (R40)."""
        engine = PanelEngine()
        await engine.memory.set(
            PanelState(
                admin_id=101,
                chat_id=505,
                message_id=999,
                version=3,
                screen="main",
                updated_at=1000.0,
            )
        )

        # Version 2 is stale (current is 3) -> rejected
        self.assertFalse(
            await engine.is_valid_version(admin_id=101, chat_id=505, callback_version=2)
        )

        # Version 3 is current -> accepted
        self.assertTrue(
            await engine.is_valid_version(admin_id=101, chat_id=505, callback_version=3)
        )

        # Version 4 is fresh -> accepted
        self.assertTrue(
            await engine.is_valid_version(admin_id=101, chat_id=505, callback_version=4)
        )

    async def test_render_panel_edits_in_place(self) -> None:
        """Existing panel edits in place and increments version (R3, R40)."""
        gateway = MockTelegramGateway()
        engine = PanelEngine(telegram_gateway=gateway)

        # First render creates a new panel
        state1 = await engine.render_panel(
            admin_id=101,
            chat_id=505,
            text="Initial screen",
            screen="start",
        )
        self.assertEqual(state1.version, 1)
        self.assertEqual(len(gateway.sent_messages), 1)
        self.assertEqual(len(gateway.edited_messages), 0)

        # Second render edits existing panel in place
        state2 = await engine.render_panel(
            admin_id=101,
            chat_id=505,
            text="Second screen",
            screen="options",
        )
        self.assertEqual(state2.version, 2)
        self.assertEqual(state2.message_id, state1.message_id)
        self.assertEqual(len(gateway.edited_messages), 1)
        self.assertEqual(gateway.edited_messages[0]["text"], "Second screen")

    async def test_render_panel_fallback_when_message_deleted(self) -> None:
        """When edit fails with message not found, creates a new panel and updates memory (R40)."""
        gateway = MockTelegramGateway()
        engine = PanelEngine(telegram_gateway=gateway)

        state1 = await engine.render_panel(
            admin_id=101,
            chat_id=505,
            text="First message",
        )
        self.assertEqual(state1.message_id, 101)

        # Simulate message was deleted by user or Telegram
        gateway.fail_edit_with = RuntimeError("Bad Request: message to edit not found")

        state2 = await engine.render_panel(
            admin_id=101,
            chat_id=505,
            text="Recovered message",
        )
        # Succeeded by creating a new message
        self.assertNotEqual(state2.message_id, state1.message_id)
        self.assertEqual(state2.version, 2)
        self.assertEqual(len(gateway.sent_messages), 2)

    async def test_close_panel_deletes_message_and_clears_memory(self) -> None:
        """close_panel deletes message and wipes memory (R4)."""
        gateway = MockTelegramGateway()
        engine = PanelEngine(telegram_gateway=gateway)

        state = await engine.render_panel(
            admin_id=101,
            chat_id=505,
            text="Panel to close",
        )
        await engine.close_panel(admin_id=101, chat_id=505)

        self.assertEqual(len(gateway.deleted_messages), 1)
        self.assertEqual(gateway.deleted_messages[0]["message_id"], state.message_id)
        self.assertIsNone(await engine.memory.get(101, 505))


if __name__ == "__main__":
    unittest.main()
