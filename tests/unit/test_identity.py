"""Unit tests for character identity and versioning service (R14, R18)."""

import os
import shutil
import tempfile
import unittest

from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.identity import (
    create_character,
    create_new_version,
    format_version_label,
    get_current_character,
    list_character_versions,
)


class TestCharacterIdentity(unittest.IsolatedAsyncioTestCase):
    """Test suite covering character identity, version transitions, and appearance facts (R14, R18)."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_ident_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_character_creation_and_versioning(self) -> None:
        """Creating an edit spawns version 2, unmarks version 1 as current, and links via edit_of (R18)."""
        async with self.session_factory() as session:
            async with session.begin():
                char_v1 = await create_character(
                    session=session,
                    name="Toki",
                    appearance_facts="blonde hair, blue eyes, slim build",
                    created_by=101,
                )
                v1_id = char_v1.id
                char_stable_id = char_v1.character_id

        # Verify version 1 is current
        async with self.session_factory() as session:
            curr = await get_current_character(session, char_stable_id)
            self.assertIsNotNone(curr)
            assert curr is not None
            self.assertEqual(curr.version, 1)
            self.assertTrue(curr.is_current)
            self.assertIsNone(curr.edit_of)

        # Create new version 2 (editing appearance)
        async with self.session_factory() as session:
            async with session.begin():
                char_v2 = await create_new_version(
                    session=session,
                    parent_id=v1_id,
                    new_appearance_facts="blonde hair in ponytail, blue eyes, athletic build",
                )

        # Verify version 2 is current and version 1 is retired
        async with self.session_factory() as session:
            curr2 = await get_current_character(session, char_stable_id)
            self.assertIsNotNone(curr2)
            assert curr2 is not None
            self.assertEqual(curr2.version, 2)
            self.assertTrue(curr2.is_current)
            self.assertEqual(curr2.edit_of, v1_id)
            self.assertEqual(curr2.character_id, char_stable_id)

            # Check full history
            all_versions = await list_character_versions(session, char_stable_id)
            self.assertEqual(len(all_versions), 2)
            self.assertEqual(all_versions[0].version, 2)  # descending
            self.assertEqual(all_versions[1].version, 1)
            self.assertFalse(all_versions[1].is_current)

    def test_version_label_formatting(self) -> None:
        """Verify Persian version label formatting (R18)."""
        self.assertEqual(format_version_label(1), "نسخه 1")
        self.assertEqual(format_version_label(2, is_current=True), "نسخه 2 (جاری)")


if __name__ == "__main__":
    unittest.main()
