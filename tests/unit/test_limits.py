"""Unit tests for Limits domain object and effective_limits short-circuit (R52, R53)."""

import unittest

from waifubot.core.limits import effective_limits


class TestLimits(unittest.TestCase):
    def test_superadmin_unlimited_short_circuit(self) -> None:
        """Superadmin has no limits on any activity (R53)."""
        lim = effective_limits("superadmin")
        self.assertIsNone(lim.max_owned_characters)
        self.assertIsNone(lim.daily_image_budget)
        self.assertIsNone(lim.max_concurrent_jobs)
        self.assertIsNone(lim.max_stored_images)
        self.assertIsNone(lim.daily_chat_messages)
        self.assertIsNone(lim.max_channels)
        self.assertEqual(lim.max_images_per_job, 16)
        self.assertGreaterEqual(lim.queue_priority, 10)

        # Even with restrictive plan or overrides passed, superadmin remains unlimited
        restrictive_plan = {"daily_image_budget": 1, "max_owned_characters": 0}
        restrictive_overrides = {"daily_image_budget": 2}
        lim2 = effective_limits(
            "superadmin", plan_limits=restrictive_plan, overrides=restrictive_overrides
        )
        self.assertIsNone(lim2.daily_image_budget)
        self.assertIsNone(lim2.max_owned_characters)

    def test_admin_staff_defaults(self) -> None:
        """Admin defaults match the built-in staff plan (§3.6)."""
        lim = effective_limits("admin")
        self.assertIsNone(lim.daily_image_budget)
        self.assertIsNone(lim.max_concurrent_jobs)
        self.assertEqual(lim.max_images_per_job, 16)
        self.assertEqual(lim.queue_priority, 10)

    def test_user_free_defaults(self) -> None:
        """Standard user role defaults to the built-in free plan (§3.6)."""
        lim = effective_limits("user")
        self.assertEqual(lim.max_owned_characters, 3)
        self.assertEqual(lim.daily_image_budget, 8)
        self.assertEqual(lim.max_images_per_job, 4)
        self.assertEqual(lim.max_concurrent_jobs, 1)
        self.assertEqual(lim.max_stored_images, 100)
        self.assertEqual(lim.daily_chat_messages, 60)
        self.assertEqual(lim.chat_memory_tokens, 500)
        self.assertEqual(lim.max_channels, 0)
        self.assertEqual(lim.queue_priority, 0)

    def test_plan_limits_override_defaults(self) -> None:
        """Plan limits properly override defaults."""
        custom_plan = {
            "daily_image_budget": 50,
            "max_images_per_job": 8,
            "queue_priority": 5,
        }
        lim = effective_limits("user", plan_limits=custom_plan)
        self.assertEqual(lim.daily_image_budget, 50)
        self.assertEqual(lim.max_images_per_job, 8)
        self.assertEqual(lim.queue_priority, 5)
        # Non-overridden fields retain defaults
        self.assertEqual(lim.max_owned_characters, 3)

    def test_user_overrides_precedence(self) -> None:
        """Per-user overrides on user card override plan limits (R52)."""
        custom_plan = {"daily_image_budget": 50, "max_images_per_job": 8}
        overrides = {"daily_image_budget": 100, "max_owned_characters": 10}
        lim = effective_limits("user", plan_limits=custom_plan, overrides=overrides)
        self.assertEqual(lim.daily_image_budget, 100)
        self.assertEqual(lim.max_owned_characters, 10)
        self.assertEqual(lim.max_images_per_job, 8)


if __name__ == "__main__":
    unittest.main()
