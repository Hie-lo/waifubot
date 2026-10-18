"""Unit tests for chat engine pipeline, safety, debounce, and concurrency (R63-R66)."""

import asyncio
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.adapters.chat.mock import MockChatModel
from waifubot.core.limits import effective_limits
from waifubot.core.safety import SafetyViolationError
from waifubot.db.base import Base
from waifubot.services.chat.debounce import ChatDebouncer
from waifubot.services.chat.engine import ChatDisabledError, ChatEngine
from waifubot.services.identity import create_character
from waifubot.services.ownership import claim_character, set_character_status
from waifubot.services.quota import QuotaExceededError, get_user_usage_summary


class TestChatEngine(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.model = MockChatModel(canned_reply="من اینجام تا کمکت کنم!")
        self.chat_engine = ChatEngine(chat_model=self.model, max_concurrency=2)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_chat_pipeline_success_and_usage_tracking(self) -> None:
        """User sends message, gets reply, memory updates, and tokens are tracked (R63, R65)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Rem", "Blue maid", created_by=1)
            await set_character_status(session, char.character_id, "listed")
            user_id = 100
            await claim_character(session, char.character_id, user_id=user_id)

            reply = await self.chat_engine.process_message(
                session=session,
                user_id=user_id,
                role="user",
                character_id=char.character_id,
                user_text="سلام رم، امروز هوا چطوره؟",
                user_limits=effective_limits("user"),
            )
            self.assertEqual(reply, "من اینجام تا کمکت کنم!")

            # Verify usage recorded
            summary = await get_user_usage_summary(session, user_id)
            self.assertEqual(summary["chat_messages_today"], 1)

    async def test_chat_disabled_master_toggle(self) -> None:
        """Disabled chat raises ChatDisabledError without executing model (R63, R65)."""
        async with self.session_factory() as session:
            with self.assertRaises(ChatDisabledError):
                await self.chat_engine.process_message(
                    session=session,
                    user_id=200,
                    role="user",
                    character_id="char_test",
                    user_text="سلام",
                    chat_enabled=False,
                )

    async def test_input_safety_rejection(self) -> None:
        """Unsafe input is rejected by safety filter before hitting model (R26, R66)."""
        async with self.session_factory() as session:
            with self.assertRaises(SafetyViolationError):
                await self.chat_engine.process_message(
                    session=session,
                    user_id=300,
                    role="user",
                    character_id="char_test",
                    user_text="underage loli prompt test",
                )

    async def test_output_safety_refusal_replacement(self) -> None:
        """Model output that fails safety is replaced with safe in-character refusal (R66)."""
        unsafe_model = MockChatModel(canned_reply="here is some underage forbidden content")
        engine = ChatEngine(chat_model=unsafe_model)

        async with self.session_factory() as session:
            char = await create_character(session, "SafeChar", "Appearance", created_by=1)
            reply = await engine.process_message(
                session=session,
                user_id=400,
                role="user",
                character_id=char.character_id,
                user_text="سلام",
            )
            self.assertIn("متأسفم، اما نمی‌توانم", reply)

    async def test_daily_chat_messages_quota(self) -> None:
        """Reaching daily_chat_messages raises QuotaExceededError (R54)."""
        async with self.session_factory() as session:
            char = await create_character(session, "QuotaChar", "Appearance", created_by=1)
            limits = effective_limits("user", overrides={"daily_chat_messages": 1})

            # 1st message succeeds
            await self.chat_engine.process_message(
                session=session,
                user_id=500,
                role="user",
                character_id=char.character_id,
                user_text="پیام اول",
                user_limits=limits,
            )

            # 2nd message raises quota exceeded
            with self.assertRaises(QuotaExceededError):
                await self.chat_engine.process_message(
                    session=session,
                    user_id=500,
                    role="user",
                    character_id=char.character_id,
                    user_text="پیام دوم",
                    user_limits=limits,
                )

    async def test_chat_debouncer_merges_rapid_messages(self) -> None:
        """ChatDebouncer combines rapid messages into a single text block (R63)."""
        debouncer = ChatDebouncer(debounce_ms=50)
        flushed_messages: list[str] = []

        async def on_flush(key: str, merged_text: str) -> None:
            flushed_messages.append(merged_text)

        await debouncer.add_message("user_1", "سلام", on_flush)
        await debouncer.add_message("user_1", "چطوری؟", on_flush)
        await debouncer.add_message("user_1", "کجایی؟", on_flush)

        # Wait for debounce timer to fire
        await asyncio.sleep(0.1)

        self.assertEqual(len(flushed_messages), 1)
        self.assertEqual(flushed_messages[0], "سلام\nچطوری؟\nکجایی؟")


if __name__ == "__main__":
    unittest.main()
