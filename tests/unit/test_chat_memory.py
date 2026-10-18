"""Unit tests for chat memory, token estimation, and compaction parsing (R64)."""

import unittest

from waifubot.core.chat_memory import (
    ChatMemory,
    estimate_tokens,
    normalize_text,
    parse_compaction_response,
)


class TestChatMemory(unittest.TestCase):
    def test_unicode_normalization_and_token_estimation(self) -> None:
        """NFC normalization and conservative Persian token estimation (R64)."""
        persian_text = "سلام دوست من! امروز حالت چطوره؟"
        normalized = normalize_text(persian_text)
        self.assertEqual(normalized, persian_text)

        tokens = estimate_tokens(persian_text)
        self.assertGreater(tokens, 0)
        # Upper bound ensures tokens >= character count
        self.assertGreaterEqual(tokens, len(persian_text))

        # Empty string yields 0
        self.assertEqual(estimate_tokens(""), 0)

    def test_chat_memory_token_counting_and_facts_management(self) -> None:
        """ChatMemory tracks memory tokens, durable facts, and supports reset (R64)."""
        mem = ChatMemory(
            session_id="sess_123",
            persona_card="Name: Holo",
            summary="User likes apples.",
            facts=["Owner is a traveling merchant."],
        )

        self.assertGreater(mem.total_memory_tokens(), 0)
        self.assertEqual(mem.total_window_tokens(), 0)

        # Add facts up to cap
        added = mem.add_fact("Owner visits trade guilds often.", chat_facts_max=3)
        self.assertTrue(added)
        self.assertEqual(len(mem.facts), 2)

        # Duplicate fact is ignored
        self.assertFalse(mem.add_fact("Owner visits trade guilds often.", chat_facts_max=3))

        # Delete fact
        self.assertTrue(mem.remove_fact(0))
        self.assertEqual(len(mem.facts), 1)

        # Reset
        mem.reset()
        self.assertEqual(mem.summary, "")
        self.assertEqual(len(mem.facts), 0)
        self.assertEqual(len(mem.window), 0)

    def test_should_compact_dual_condition(self) -> None:
        """Compaction triggers on either turn count OR window token budget overflow (R64)."""
        mem = ChatMemory(session_id="sess_456", persona_card="Name: Test")

        # 1. Below thresholds
        for i in range(4):
            mem.append_turn("user", f"short msg {i}", 1.0)
            mem.append_turn("assistant", f"reply {i}", 2.0)
        self.assertEqual(len(mem.window), 8)
        self.assertFalse(mem.should_compact(max_turns_before_compact=10, window_token_budget=1000))

        # 2. Trigger via turn count (>= 10 turns)
        mem.append_turn("user", "msg 5", 3.0)
        mem.append_turn("assistant", "reply 5", 4.0)
        self.assertEqual(len(mem.window), 10)
        self.assertTrue(mem.should_compact(max_turns_before_compact=10, window_token_budget=1000))

        # 3. Trigger via token budget overflow even with few turns
        small_mem = ChatMemory(session_id="sess_789", persona_card="Name: Test")
        very_long_text = "متن بسیار طولانی " * 200
        small_mem.append_turn("user", very_long_text, 1.0)
        self.assertTrue(
            small_mem.should_compact(max_turns_before_compact=10, window_token_budget=100)
        )

    def test_parse_compaction_response_strict_and_regex_fallback(self) -> None:
        """Parses strict JSON or extracts JSON block via regex (R64)."""
        # Strict JSON
        json_str = '{"summary": "User is a merchant", "facts_add": ["likes wine"], "facts_remove": ["old fact"]}'
        res = parse_compaction_response(json_str)
        self.assertEqual(res["summary"], "User is a merchant")
        self.assertEqual(res["facts_add"], ["likes wine"])
        self.assertEqual(res["facts_remove"], ["old fact"])

        # Markdown wrapped JSON
        wrapped = f"```json\n{json_str}\n```"
        res_wrapped = parse_compaction_response(wrapped)
        self.assertEqual(res_wrapped["summary"], "User is a merchant")

        # Invalid response raises ValueError
        with self.assertRaises(ValueError):
            parse_compaction_response("No json here at all!")


if __name__ == "__main__":
    unittest.main()
