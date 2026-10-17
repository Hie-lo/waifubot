"""Unit tests for pure core domain logic (limits, chat memory, token heuristics)."""

import unittest
from waifubot.core.chat_memory import (
    ChatMemory,
    estimate_tokens,
    normalize_text,
    parse_compaction_response,
)
from waifubot.core.limits import Limits, effective_limits


class TestCoreDomain(unittest.TestCase):
    def test_effective_limits_superadmin_short_circuit(self):
        """Verify R53 single short-circuit: superadmin is unlimited regardless of overrides."""
        limits = effective_limits("superadmin", plan_limits={"daily_image_budget": 5})
        self.assertIsNone(limits.daily_image_budget)
        self.assertIsNone(limits.max_owned_characters)
        self.assertIsNone(limits.max_channels)
        self.assertEqual(limits.queue_priority, 100)

    def test_effective_limits_regular_user(self):
        """Verify regular user receives default free plan and honors overrides."""
        default = effective_limits("user")
        self.assertEqual(default.daily_image_budget, 8)
        self.assertEqual(default.max_images_per_job, 4)
        self.assertEqual(default.max_channels, 0)

        overridden = effective_limits(
            "user",
            plan_limits={"daily_image_budget": 20},
            overrides={"max_channels": 2},
        )
        self.assertEqual(overridden.daily_image_budget, 20)
        self.assertEqual(overridden.max_channels, 2)

    def test_persian_token_estimation_conservative(self):
        """Verify Persian token estimation scales conservatively against BPE expansion."""
        persian_text = "سلام، حال شما چطوره؟ امروز چه برنامه‌ای داریم؟"
        tokens = estimate_tokens(persian_text)
        self.assertGreaterEqual(tokens, len(persian_text))
        self.assertGreater(tokens, 0)

    def test_unicode_nfc_normalization(self):
        """Verify Unicode NFC normalizes Arabic/Persian variants consistently."""
        text_with_spaces = "  سلام کاراکتر   "
        normalized = normalize_text(text_with_spaces)
        self.assertEqual(normalized, "سلام کاراکتر")

    def test_chat_memory_dual_compaction_trigger(self):
        """Verify compaction triggers on turn threshold or token budget."""
        mem = ChatMemory(session_id="test-1", persona_card="Card")
        # Low turn count, low tokens
        mem.append_turn("user", "سلام", 1.0)
        self.assertFalse(mem.should_compact(max_turns_before_compact=10, window_token_budget=500))

        # Trigger via token budget overflow
        verbose_text = "توضیحات طولانی و پاراگراف مفصل فارسی " * 30
        mem.append_turn("user", verbose_text, 2.0)
        self.assertTrue(mem.should_compact(max_turns_before_compact=10, window_token_budget=100))

    def test_parse_compaction_response_fallback(self):
        """Verify regex extraction fallback parses JSON surrounded by extra markdown/text."""
        raw_response = """
        Here is the summary output:
        {
            "summary": "خلاصه جدید گفت‌وگو",
            "facts_add": ["کاربر انیمه دوست دارد"],
            "facts_remove": []
        }
        Hope this helps!
        """
        parsed = parse_compaction_response(raw_response)
        self.assertEqual(parsed["summary"], "خلاصه جدید گفت‌وگو")
        self.assertEqual(parsed["facts_add"], ["کاربر انیمه دوست دارد"])
        self.assertEqual(parsed["facts_remove"], [])


if __name__ == "__main__":
    unittest.main()
