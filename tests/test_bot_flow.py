"""Integration tests for Special Character Studio wizard and bot flow (R16B, R40, R42)."""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.adapters.chat.mock import MockChatModel
from waifubot.bot.handlers.chat import ChatHandler
from waifubot.bot.handlers.generate import SpecialCharacterStudioHandler
from waifubot.bot.panel import PanelEngine
from waifubot.bot.rate_limiter import FileIdCache, TelegramRateLimiter
from waifubot.bot.wizard import WizardStore
from waifubot.db.base import Base
from waifubot.services.chat.engine import ChatEngine
from waifubot.services.identity import create_character
from waifubot.services.ownership import claim_character, set_character_status


class TestBotFlow(unittest.IsolatedAsyncioTestCase):
    """End-to-end bot flow tests covering Special Character Studio and Telegram rate limiter."""

    async def test_special_character_studio_flow(self) -> None:
        """Verify 114-character catalog, click-only option sync, and typed text refusal (R16B)."""
        engine = PanelEngine()
        store = WizardStore()
        handler = SpecialCharacterStudioHandler(
            panel_engine=engine,
            wizard_store=store,
            config_path="config/vision_lab_presets.yaml",
        )

        # 1. Catalog loading verification
        self.assertEqual(len(handler.characters), 114)
        first_char = handler.get_character_by_index(0)
        self.assertIsNotNone(first_char)
        assert first_char is not None
        self.assertEqual(first_char["name"], "Toki")

        # 2. Start wizard
        user_id = 999
        chat_id = 888
        state = handler.start_studio_wizard(user_id, chat_id)
        self.assertEqual(state.mode, "special_studio")

        # 3. Disallow typed text in studio mode (R16B)
        accepted, msg = handler.handle_typed_text(state, "some custom prompt")
        self.assertFalse(accepted)
        self.assertIn("فقط با انتخاب دکمه‌ها", msg)

        # 4. Select character (Toki)
        state = handler.select_character(state, index=0)
        self.assertEqual(state.selected_character, "Toki")
        self.assertIn("long blonde hair", state.character_prompt)

        # 5. Select options (outfit, pose, style)
        handler.toggle_option(state, category_key="outfit", option_val="maid outfit")
        handler.toggle_option(state, category_key="pose", option_val="sitting on chair")
        handler.toggle_option(state, category_key="style", option_val="anime watercolor")

        # 6. Verify composed prompt matches Vision Lab sync() contract
        composed = state.compose_prompt()
        self.assertIn(state.character_prompt, composed)
        self.assertIn("maid outfit", composed)
        self.assertIn("sitting on chair", composed)
        self.assertIn("anime watercolor", composed)
        # Verify joined with commas
        self.assertIn(", maid outfit, ", composed)

        # 7. Set target count
        handler.set_image_count(state, 8)
        self.assertEqual(state.target_count, 8)

        # 8. Render studio panel
        text, keyboard = handler.render_studio_panel(state)
        self.assertIn("Toki", text)
        self.assertIn("8", text)
        self.assertTrue(any("شروع ساخت" in btn["text"] for row in keyboard for btn in row))

    async def test_telegram_rate_limiter_and_flood_wait(self) -> None:
        """Rate limiter honors flood wait backoff when Telegram returns retry_after (R42)."""
        limiter = TelegramRateLimiter(min_interval_seconds=0.01)

        class SimulatedFloodWait(Exception):
            def __init__(self, retry_after: float) -> None:
                self.retry_after = retry_after

        attempts = 0

        async def simulated_api_call() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # First call triggers flood wait of 0.1s
                raise SimulatedFloodWait(retry_after=0.1)
            return "ok_result"

        res = await limiter.execute(simulated_api_call)
        self.assertEqual(res, "ok_result")
        self.assertEqual(attempts, 2)

    def test_file_id_cache(self) -> None:
        """FileIdCache caches file_id across calls to avoid re-uploading (R42)."""
        cache = FileIdCache()
        sha = "sample_sha256_hash_value"
        self.assertIsNone(cache.get(sha))

        cache.put(sha, "AgACAgIAAxkBAAI...")
        self.assertEqual(cache.get(sha), "AgACAgIAAxkBAAI...")
        self.assertEqual(len(cache), 1)

    async def test_chat_mode_flow_and_endchat(self) -> None:
        """User enters companion chat mode, exchanges messages, and exits with /endchat (R63)."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        mock_model = MockChatModel(canned_reply="سلام! من برای گفتگو آماده‌ام.")
        chat_engine = ChatEngine(chat_model=mock_model)
        chat_handler = ChatHandler()

        user_id = 777
        async with session_factory() as session:
            # Setup character and ownership
            char = await create_character(session, "Aqua", "Water goddess", created_by=1)
            await set_character_status(session, char.character_id, "listed")
            await claim_character(session, char.character_id, user_id=user_id)

            # 1. Initially not in chat
            self.assertFalse(chat_handler.is_in_chat(user_id))
            reply, handled = await chat_handler.handle_incoming_text(
                session=session,
                engine=chat_engine,
                user_id=user_id,
                role="user",
                text="سلام",
            )
            self.assertFalse(handled)
            self.assertIsNone(reply)

            # 2. Enter chat via render_chat_panel
            text, keyboard = await chat_handler.render_chat_panel(
                session=session,
                user_id=user_id,
                character_id=char.character_id,
                locale="fa",
            )
            self.assertIn("Aqua", text)
            self.assertTrue(chat_handler.is_in_chat(user_id))
            self.assertEqual(chat_handler.get_active_character(user_id), char.character_id)

            # 3. Send text while in chat mode
            reply, handled = await chat_handler.handle_incoming_text(
                session=session,
                engine=chat_engine,
                user_id=user_id,
                role="user",
                text="امروز چطوری؟",
            )
            self.assertTrue(handled)
            self.assertEqual(reply, "سلام! من برای گفتگو آماده‌ام.")

            # 4. Persona panel rendering
            p_text, p_kb = await chat_handler.render_persona_panel(
                session=session,
                character_id=char.character_id,
            )
            self.assertIn("تنظیمات شخصیت", p_text)
            self.assertTrue(any("بهینه‌سازی" in b["text"] for row in p_kb for b in row))

            # 5. Memory panel rendering
            m_text, m_kb = await chat_handler.render_memory_panel(
                session=session,
                user_id=user_id,
                character_id=char.character_id,
            )
            self.assertIn("حافظه بلندمدت", m_text)

            # 6. Exit chat mode via /endchat
            exit_reply, handled = await chat_handler.handle_incoming_text(
                session=session,
                engine=chat_engine,
                user_id=user_id,
                role="user",
                text="/endchat",
            )
            self.assertTrue(handled)
            self.assertIn("گفتگو با کاراکتر به پایان رسید", exit_reply or "")
            self.assertFalse(chat_handler.is_in_chat(user_id))

        await engine.dispose()

    def test_build_bot_app_registers_handlers(self) -> None:
        """Verify build_bot_app registers commands, messages, callbacks and initializes session."""
        from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

        from waifubot.bot.app import build_bot_app

        app = build_bot_app(
            token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789",
            db_url="sqlite+aiosqlite:///:memory:",
            admin_ids=[1001, 1002],
        )
        self.assertIn("session_factory", app.bot_data)

        # Check registered handler types
        handlers = app.handlers.get(0, [])
        handler_types = [type(h) for h in handlers]
        self.assertIn(CommandHandler, handler_types)
        self.assertIn(CallbackQueryHandler, handler_types)
        self.assertIn(MessageHandler, handler_types)

        # Check command names
        command_handlers = [h for h in handlers if isinstance(h, CommandHandler)]
        commands = {list(h.commands)[0] for h in command_handlers if h.commands}
        self.assertTrue({"start", "status", "endchat"}.issubset(commands))

    def test_build_bot_app_from_environment(self) -> None:
        """Verify build_bot_app correctly reads BOT_TOKEN from environment when called with no args."""
        import os
        from unittest.mock import patch

        from waifubot.bot.app import build_bot_app

        env_patch = {
            "BOT_TOKEN": "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "ADMIN_USER_IDS": "2001,2002",
        }
        with patch.dict(os.environ, env_patch):
            app = build_bot_app()
            self.assertIsNotNone(app)
            self.assertIn("session_factory", app.bot_data)


if __name__ == "__main__":
    unittest.main()
