"""Unit tests for config and .env file loading (R25, R65)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from waifubot.config import load_env


class TestConfigEnv(unittest.TestCase):
    def test_load_env_from_file_and_set_environ(self) -> None:
        """load_env parses variables, strips quotes, ignores comments, and updates os.environ."""
        content = """
        # Comment line
        BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        ADMIN_USER_IDS='987654321'
        ALLOW_NSFW=true
        EMPTY_VAL=
        # ANOTHER_KEY=ignored
        """
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_env(tmp_path)
                self.assertEqual(loaded["BOT_TOKEN"], "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
                self.assertEqual(loaded["ADMIN_USER_IDS"], "987654321")
                self.assertEqual(loaded["ALLOW_NSFW"], "true")
                self.assertNotIn("ANOTHER_KEY", loaded)

                # Verify set in os.environ
                self.assertEqual(
                    os.environ.get("BOT_TOKEN"), "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
                )
                self.assertEqual(os.environ.get("ADMIN_USER_IDS"), "987654321")
                self.assertEqual(os.environ.get("ALLOW_NSFW"), "true")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_load_env_nonexistent_file(self) -> None:
        """load_env handles missing path gracefully without raising."""
        loaded = load_env("non_existent_file_path.env")
        self.assertIsInstance(loaded, dict)


if __name__ == "__main__":
    unittest.main()
