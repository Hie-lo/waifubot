"""Integration tests for Special Character Studio wizard and bot flow (R16B, R40, R42)."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from waifubot.bot.handlers.generate import SpecialCharacterStudioHandler
from waifubot.bot.panel import PanelEngine
from waifubot.bot.rate_limiter import FileIdCache, TelegramRateLimiter
from waifubot.bot.wizard import WizardStore


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


if __name__ == "__main__":
    unittest.main()
