"""Unit tests for `waifubot health` CLI command (R46)."""

import os
import shutil
import tempfile
import unittest

from waifubot.cli import main, run_health_check
from waifubot.db.session import create_engine_and_sessionmaker, init_db


class TestCliHealth(unittest.IsolatedAsyncioTestCase):
    """Test suite covering CLI health checks, diagnostics, and return codes (R46)."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_cli_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.storage_dir = os.path.join(self.temp_dir, "images")

        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_run_health_check_healthy(self) -> None:
        """Health check returns healthy=True with descriptive check lines."""
        healthy, checks = await run_health_check(
            db_url=self.db_url,
            storage_dir=self.storage_dir,
        )

        self.assertTrue(healthy)
        self.assertTrue(len(checks) >= 4)
        check_text = "\n".join(checks)
        self.assertIn("Database connectivity", check_text)
        self.assertIn("Storage directory writable", check_text)
        self.assertIn("System free memory", check_text)
        self.assertIn("Error buffer", check_text)

    def test_cli_health_and_main_exit_code(self) -> None:
        """Invoking waifubot health via CLI entrypoint returns exit code 0."""
        os.environ["DATABASE_URL"] = self.db_url
        os.environ["STORAGE_DIR"] = self.storage_dir

        code = main(["health"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
