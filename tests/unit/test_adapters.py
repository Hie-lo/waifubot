"""Unit tests for adapters, presentation helpers, and composition root."""

import os
import shutil
import tempfile
import unittest

from waifubot.adapters.clock import MockClock, SystemClock
from waifubot.adapters.enhance.mock import MockEnhancer
from waifubot.adapters.http import STANDARD_BROWSER_HEADERS, get_standard_browser_headers
from waifubot.adapters.providers.mock import MockGenerationProvider, MockSlot, MockSlotPool
from waifubot.adapters.storage.local_fs import LocalFsImageStore
from waifubot.adapters.storage.memory import MemoryImageStore
from waifubot.bot.app import BotContainer, create_app
from waifubot.bot.telegram_html import bold, code, escape_html, italic, link, pre, spoiler
from waifubot.bot.topics import extract_thread_id, format_chat_and_topic


class TestAdaptersAndBot(unittest.IsolatedAsyncioTestCase):
    """Test suite covering Stage 2 adapters and bot layer utilities."""

    def test_standard_browser_headers(self) -> None:
        """Verify headers match modern desktop Chrome and contain no 'stealth' branding."""
        headers = get_standard_browser_headers({"X-Custom": "test"})
        self.assertIn("User-Agent", headers)
        self.assertIn("Chrome", headers["User-Agent"])
        self.assertEqual(headers["X-Custom"], "test")

        # Verify no stealth terminology
        for k, v in STANDARD_BROWSER_HEADERS.items():
            self.assertNotIn("stealth", k.lower())
            self.assertNotIn("stealth", v.lower())

    def test_clock_adapters(self) -> None:
        """Verify SystemClock and MockClock time operations."""
        sys_clock = SystemClock()
        self.assertGreater(sys_clock.now_utc(), 1700000000.0)

        mock_clock = MockClock(100.0)
        self.assertEqual(mock_clock.now_utc(), 100.0)
        mock_clock.advance(25.5)
        self.assertEqual(mock_clock.now_utc(), 125.5)
        mock_clock.set_time(500.0)
        self.assertEqual(mock_clock.now_utc(), 500.0)

    async def test_mock_slot_pool(self) -> None:
        """Verify MockSlotPool provides slots exclusively and recycles them."""
        pool = MockSlotPool(size=2)
        async with pool.acquire() as slot1:
            self.assertIsInstance(slot1, MockSlot)
            self.assertEqual(slot1.slot_id, "mock-slot-1")
            async with pool.acquire() as slot2:
                self.assertEqual(slot2.slot_id, "mock-slot-2")

        # After releasing, acquiring again yields the recycled slots
        async with pool.acquire() as slot_recycled:
            self.assertIn(slot_recycled.slot_id, ["mock-slot-1", "mock-slot-2"])

    async def test_mock_generation_provider(self) -> None:
        """Verify MockGenerationProvider generates images and tracks calls."""
        provider = MockGenerationProvider(name="mock")
        slot = MockSlot("slot-a")
        images = await provider.generate(
            prompt="1girl, rem",
            negative_prompt="bad quality",
            needed_count=3,
            slot=slot,
        )
        self.assertEqual(len(images), 3)
        self.assertEqual(len(provider.call_history), 1)
        self.assertEqual(provider.call_history[0]["needed_count"], 3)
        self.assertNotEqual(images[0], images[1])  # distinct bytes

    async def test_memory_image_store(self) -> None:
        """Verify MemoryImageStore save and load."""
        store = MemoryImageStore()
        data = b"image_data_sample"
        file_id = await store.save_image(data)
        self.assertTrue(len(file_id) == 64)  # SHA-256 hex length
        loaded = await store.load_image(file_id)
        self.assertEqual(loaded, data)

        with self.assertRaises(FileNotFoundError):
            await store.load_image("non_existent_id")

    async def test_local_fs_image_store(self) -> None:
        """Verify LocalFsImageStore atomic write and content-addressed retrieval."""
        temp_dir = tempfile.mkdtemp(prefix="waifu_test_store_")
        try:
            store = LocalFsImageStore(root_dir=temp_dir)
            sample_bytes = b"sample_png_bytes_content"
            file_id = await store.save_image(sample_bytes, mime_type="image/png")

            # Check partitioned file path
            p1 = file_id[:2]
            p2 = file_id[2:4]
            expected_file = os.path.join(temp_dir, p1, p2, f"{file_id}.png")
            self.assertTrue(os.path.exists(expected_file))

            # Check load
            loaded = await store.load_image(file_id)
            self.assertEqual(loaded, sample_bytes)

            # Saving duplicate is idempotent
            file_id_2 = await store.save_image(sample_bytes, mime_type="image/png")
            self.assertEqual(file_id_2, file_id)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def test_mock_enhancer(self) -> None:
        """Verify MockEnhancer prompt generation."""
        enhancer = MockEnhancer()
        enhanced = await enhancer.enhance_prompt("beach sunset", {"name": "Megumin"})
        self.assertIn("Megumin", enhanced)
        self.assertIn("beach sunset", enhanced)

    def test_telegram_html_helpers(self) -> None:
        """Verify Telegram HTML escaping and tags."""
        raw = "Hello <world> & 'friends'"
        escaped = escape_html(raw)
        self.assertEqual(escaped, "Hello &lt;world&gt; &amp; &#x27;friends&#x27;")

        self.assertEqual(bold("text"), "<b>text</b>")
        self.assertEqual(italic("text"), "<i>text</i>")
        self.assertEqual(code("text"), "<code>text</code>")
        self.assertEqual(pre("code\nblock"), "<pre>code\nblock</pre>")
        self.assertEqual(
            pre("code", "python"),
            '<pre><code class="language-python">code</code></pre>',
        )
        self.assertEqual(
            link("Google", "https://google.com"), '<a href="https://google.com">Google</a>'
        )
        self.assertEqual(spoiler("secret"), '<span class="tg-spoiler">secret</span>')

    def test_telegram_topics(self) -> None:
        """Verify forum topic extraction and formatting."""

        class DummyMsg:
            message_thread_id = 42

        class DummyUpdate:
            effective_message = DummyMsg()

        self.assertEqual(extract_thread_id(DummyMsg()), 42)
        self.assertEqual(extract_thread_id(DummyUpdate()), 42)
        self.assertEqual(extract_thread_id({"message_thread_id": 99}), 99)
        self.assertEqual(extract_thread_id({"message": {"message_thread_id": 77}}), 77)
        self.assertIsNone(extract_thread_id(None))

        self.assertEqual(format_chat_and_topic(-100123456, 42), "-100123456/42")
        self.assertEqual(format_chat_and_topic(-100123456, None), "-100123456")

    def test_composition_root(self) -> None:
        """Verify composition root in bot/app.py builds application container."""
        container = create_app()
        self.assertIsInstance(container, BotContainer)
        self.assertIsNotNone(container.generation_service)
        self.assertIsNotNone(container.provider)
        self.assertIsNotNone(container.image_store)
        self.assertIsNotNone(container.clock)


if __name__ == "__main__":
    unittest.main()
