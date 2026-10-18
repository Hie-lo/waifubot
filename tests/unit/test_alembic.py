"""Unit tests for Alembic database migrations."""

import os
import shutil
import tempfile
import unittest

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


class TestAlembicMigrations(unittest.TestCase):
    """Test suite ensuring all Alembic migrations apply cleanly and create required tables."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_alembic_test_")
        self.db_path = os.path.join(self.temp_dir, "migration_test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"

        self.alembic_cfg = Config("alembic.ini")
        self.alembic_cfg.set_main_option("sqlalchemy.url", self.db_url)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migrations_upgrade_to_head_and_verify_schema(self) -> None:
        """Runs alembic upgrade head and asserts table structures (jobs, images, characters)."""
        command.upgrade(self.alembic_cfg, "head")

        # Inspect SQLite schema using synchronous engine
        sync_url = f"sqlite:///{self.db_path}"
        engine = create_engine(sync_url)
        inspector = inspect(engine)

        tables = set(inspector.get_table_names())
        self.assertIn("jobs", tables)
        self.assertIn("images", tables)
        self.assertIn("characters", tables)
        self.assertIn("schedules", tables)
        self.assertIn("audit_log", tables)
        self.assertIn("admins", tables)
        self.assertIn("users", tables)
        self.assertIn("admin_invites", tables)
        self.assertIn("plans", tables)
        self.assertIn("usage_counters", tables)
        self.assertIn("channels", tables)
        self.assertIn("channel_topics", tables)
        self.assertIn("character_ownership", tables)
        self.assertIn("claim_posts", tables)
        self.assertIn("character_personas", tables)
        self.assertIn("chat_sessions", tables)
        self.assertIn("chat_memory", tables)

        # Test downgrade to base and re-upgrade
        command.downgrade(self.alembic_cfg, "base")
        inspector_down = inspect(engine)
        self.assertEqual(len(inspector_down.get_table_names()), 1)  # only alembic_version remains

        command.upgrade(self.alembic_cfg, "head")

        img_columns = {col["name"] for col in inspector.get_columns("images")}
        self.assertIn("image_id", img_columns)
        self.assertIn("phash", img_columns)
        self.assertIn("job_id", img_columns)
        self.assertIn("final_prompt", img_columns)
        self.assertIn("provider", img_columns)
        self.assertIn("is_enhanced", img_columns)
        self.assertIn("telegram_file_id", img_columns)

        # Verify channels table columns (R56–R59)
        channel_columns = {col["name"] for col in inspector.get_columns("channels")}
        self.assertIn("tg_chat_id", channel_columns)
        self.assertIn("kind", channel_columns)
        self.assertIn("owner_user_id", channel_columns)
        self.assertIn("scope", channel_columns)
        self.assertIn("status", channel_columns)
        self.assertIn("bot_rights_json", channel_columns)

        # Verify characters table columns (R14, R18 versioning)
        char_columns = {col["name"] for col in inspector.get_columns("characters")}
        self.assertIn("character_id", char_columns)
        self.assertIn("version", char_columns)
        self.assertIn("is_current", char_columns)
        self.assertIn("edit_of", char_columns)
        self.assertIn("appearance_facts", char_columns)

        # Verify users table columns (R52)
        user_columns = {col["name"] for col in inspector.get_columns("users")}
        self.assertIn("plan_id", user_columns)
        self.assertIn("plan_expires_at", user_columns)
        self.assertIn("limit_overrides_json", user_columns)

        # Verify seeded plans
        with engine.connect() as conn:
            plan_rows = conn.execute(sa.text("SELECT id, is_builtin FROM plans")).fetchall()
            plan_ids = {r[0] for r in plan_rows}
            self.assertIn("free", plan_ids)
            self.assertIn("staff", plan_ids)

    def test_auto_create_nonexistent_parent_directory(self) -> None:
        """Alembic automatically creates missing parent directories for SQLite files."""
        deep_dir = os.path.join(self.temp_dir, "nested", "storage", "folder")
        deep_db = os.path.join(deep_dir, "test.db")
        self.assertFalse(os.path.exists(deep_dir))

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{deep_db}")
        command.upgrade(cfg, "head")

        self.assertTrue(os.path.exists(deep_dir))
        self.assertTrue(os.path.exists(deep_db))


if __name__ == "__main__":
    unittest.main()
