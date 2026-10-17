"""Unit tests for content safety and soft-fail enhance (R26, R39)."""

import unittest

from waifubot.core.safety import check_safety, recheck_enhancer_output


class TestContentSafety(unittest.TestCase):
    def test_clean_anime_prompt_passes(self):
        """Standard adult anime prompts pass safety without restriction (R26)."""
        prompt = "1girl, mature woman, red evening dress, highly detailed"
        is_safe, reason = check_safety(prompt)
        self.assertTrue(is_safe)
        self.assertIsNone(reason)

    def test_minor_terms_rejected(self):
        """Minor terms are rejected unconditionally (R26)."""
        bad_prompts = [
            "cute loli girl playing in park",
            "young child portrait",
            "underage school scene",
            "shota character",
        ]
        for p in bad_prompts:
            is_safe, reason = check_safety(p)
            self.assertFalse(is_safe, f"Failed to reject: {p}")
            self.assertEqual(reason, "safety_rejected_minor")

    def test_enhancer_output_recheck_fails_soft(self):
        """Enhancer output failing safety soft-fails back to original prompt (R39)."""
        original = "anime girl in kimono"
        unsafe_enhanced = "anime loli girl in beautiful silk kimono"

        final, passed, reason = recheck_enhancer_output(original, unsafe_enhanced)
        self.assertEqual(final, original)
        self.assertFalse(passed)
        self.assertEqual(reason, "safety_rejected_minor")

    def test_enhancer_output_safe_passes(self):
        """Safe enhanced prompt is accepted."""
        original = "anime girl in kimono"
        safe_enhanced = "1girl, elegant anime woman in silk kimono, cherry blossoms"

        final, passed, reason = recheck_enhancer_output(original, safe_enhanced)
        self.assertEqual(final, safe_enhanced)
        self.assertTrue(passed)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
