"""Automated callback data registry audit: <= 64 bytes limit and type-safe routing (R8, R41)."""

import unittest

from waifubot.bot.callbacks import (
    AccountCallback,
    AdminCallback,
    GenActionCallback,
    GenCountCallback,
    MyImagesCallback,
    NavCallback,
    PlanCallback,
    ScheduleCallback,
    SettingCallback,
    StudioCharCallback,
    StudioOptCallback,
    SystemCallback,
    UserCallback,
    ViewerCallback,
    registry,
)


class TestCallbackRegistry(unittest.TestCase):
    """Enforces R8 & R41 contracts: all callbacks are registered, reversible, and <= 64 bytes."""

    def test_registered_classes_not_empty(self) -> None:
        classes = registry.registered_classes()
        self.assertGreaterEqual(len(classes), 11)

    def test_all_callbacks_within_64_bytes_and_roundtrip(self) -> None:
        """Every registered callback must pack to <= 64 bytes and unpack identically."""
        sample_callbacks = [
            NavCallback(action="back", target="settings", panel_version=42),
            NavCallback(action="close", target="", panel_version=1),
            StudioCharCallback(char_index=113, page=14),
            StudioOptCallback(category_key="outfit", item_index=62),
            GenCountCallback(count=16),
            GenActionCallback(action="submit", nonce="abc123xyz"),
            ViewerCallback(action="next", page=99, image_id="img_sha256_hash_12345"),
            SettingCallback(key="worker_count", action="set", val="8"),
            AdminCallback(action="role", target_id=987654321, extra="superadmin"),
            UserCallback(action="block", target_id=123456789, extra="spam"),
            ScheduleCallback(action="toggle", schedule_id=42, extra="pause"),
            SystemCallback(action="restart_bot_confirm", extra=""),
            PlanCallback(action="view", plan_id="vip_pro", extra="1"),
            MyImagesCallback(action="view", img_id=12345, page=2),
            AccountCallback(action="view", extra=""),
        ]

        for cb in sample_callbacks:
            packed = cb.pack()
            byte_len = len(packed.encode("utf-8"))
            self.assertLessEqual(
                byte_len,
                64,
                f"Callback {cb.__class__.__name__} packed to {byte_len} bytes (> 64 bytes Telegram limit): '{packed}'",
            )

            # Test round-trip unpacking
            parsed = registry.parse(packed)
            self.assertIsInstance(parsed, cb.__class__)
            self.assertEqual(parsed.pack(), packed)

    def test_unregistered_prefix_raises_error(self) -> None:
        with self.assertRaises(KeyError):
            registry.parse("v1:unknown_prefix:foo:bar")

    def test_invalid_format_raises_error(self) -> None:
        with self.assertRaises(ValueError):
            registry.parse("bad_string_without_colons")


if __name__ == "__main__":
    unittest.main()
