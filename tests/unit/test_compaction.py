"""Unit tests for chat memory compaction lifecycle and persistence (R64)."""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.adapters.chat.mock import MockChatModel
from waifubot.core.chat_memory import (
    ChatMemory,
    parse_compaction_response,
)
from waifubot.db.base import Base
from waifubot.services.chat.engine import ChatEngine
from waifubot.services.chat.session import (
    get_or_create_session,
    load_chat_memory,
    save_chat_memory,
)
from waifubot.services.identity import create_character
from waifubot.services.ownership import claim_character, set_character_status


class CompactingMockChatModel(MockChatModel):
    """Mock model that tracks compaction invocations and returns canned compaction data."""

    def __init__(self, canned_reply: str = "پاسخ تستی") -> None:
        super().__init__(canned_reply=canned_reply)
        self.compact_call_count: int = 0
        self.last_leaving_turns: list[dict[str, str]] = []

    async def compact_memory(
        self,
        old_summary: str,
        durable_facts: list[str],
        leaving_turns: list[dict[str, str]],
        max_tokens: int = 500,
    ) -> dict[str, any]:
        self.compact_call_count += 1
        self.last_leaving_turns = leaving_turns
        return {
            "summary": "کاربر به هنر انیمه و سفر علاقه دارد.",
            "facts_add": ["کاربر دوست دارد به ژاپن سفر کند", "کاربر برنامه‌نویس است"],
            "facts_remove": ["فکت قدیمی"],
        }


class TestChatCompaction(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def test_parse_compaction_edge_cases(self) -> None:
        """parse_compaction_response handles valid JSON, fenced JSON, and malformed strings (R64)."""
        # 1. Clean JSON
        payload = (
            '{"summary": "Summary text", "facts_add": ["Fact 1", "Fact 2"], "facts_remove": []}'
        )
        parsed = parse_compaction_response(payload)
        self.assertEqual(parsed["summary"], "Summary text")
        self.assertEqual(parsed["facts_add"], ["Fact 1", "Fact 2"])
        self.assertEqual(parsed["facts_remove"], [])

        # 2. Fenced with extra text before and after
        raw_llm_out = (
            "Here is the compaction result:\n"
            "```json\n"
            '{"summary": "Updated", "facts_add": ["New fact"], "facts_remove": ["Old fact"]}\n'
            "```\nHope this helps!"
        )
        parsed_fenced = parse_compaction_response(raw_llm_out)
        self.assertEqual(parsed_fenced["summary"], "Updated")
        self.assertEqual(parsed_fenced["facts_add"], ["New fact"])
        self.assertEqual(parsed_fenced["facts_remove"], ["Old fact"])

        # 3. Missing keys defaults to empty
        minimal = '{"summary": "Just summary"}'
        parsed_min = parse_compaction_response(minimal)
        self.assertEqual(parsed_min["summary"], "Just summary")
        self.assertEqual(parsed_min["facts_add"], [])
        self.assertEqual(parsed_min["facts_remove"], [])

        # 4. Malformed content raises ValueError
        with self.assertRaises(ValueError):
            parse_compaction_response("No JSON content anywhere in this text.")

    def test_apply_compaction_updates_memory_and_window(self) -> None:
        """apply_compaction correctly updates summary, facts, and trims compacted turns (R64)."""
        memory = ChatMemory(
            session_id="s1",
            persona_card="Card",
            summary="خلاصه اولیه",
            facts=["فکت قدیمی", "فکت ماندگار"],
        )
        for i in range(8):
            memory.append_turn("user", f"پیام {i}", 1.0)
            memory.append_turn("assistant", f"پاسخ {i}", 2.0)
        self.assertEqual(len(memory.window), 16)

        compaction_data = {
            "summary": "خلاصه جدید پس از فشرده‌سازی",
            "facts_add": ["علاقه به موسیقی سنتی", "فکت ماندگار"],  # includes duplicate
            "facts_remove": ["فکت قدیمی"],
        }

        # Compact first 10 turns (leaving 6 turns)
        memory.apply_compaction(
            compaction_dict=compaction_data,
            turns_compacted_count=10,
            chat_facts_max=5,
        )

        self.assertEqual(memory.summary, "خلاصه جدید پس از فشرده‌سازی")
        # "فکت قدیمی" removed
        self.assertNotIn("فکت قدیمی", memory.facts)
        # "فکت ماندگار" preserved
        self.assertIn("فکت ماندگار", memory.facts)
        # "علاقه به موسیقی سنتی" added
        self.assertIn("علاقه به موسیقی سنتی", memory.facts)
        # No duplicates
        self.assertEqual(memory.facts.count("فکت ماندگار"), 1)
        # 16 - 10 = 6 turns remaining
        self.assertEqual(len(memory.window), 6)
        self.assertEqual(memory.window[0].content, "پیام 5")

    async def test_end_to_end_compaction_in_chat_engine(self) -> None:
        """ChatEngine automatically invokes compact_memory when turn budget is exceeded (R64)."""
        mock_model = CompactingMockChatModel()
        chat_engine = ChatEngine(chat_model=mock_model)

        async with self.session_factory() as session:
            char = await create_character(session, "Kurisu", "Red hair scientist", created_by=1)
            await set_character_status(session, char.character_id, "listed")
            user_id = 900
            await claim_character(session, char.character_id, user_id=user_id)

            sess_rec = await get_or_create_session(session, user_id, char.character_id)
            mem = await load_chat_memory(session, sess_rec.session_id)
            mem.facts.append("فکت قدیمی")

            # Seed 12 turns in memory window (6 user, 6 assistant)
            for i in range(6):
                mem.append_turn("user", f"موضوع گفتگوی شماره {i}", float(i))
                mem.append_turn("assistant", f"پاسخ دستیار به شماره {i}", float(i) + 0.5)
            await save_chat_memory(session, mem)
            await session.commit()

            # Now send next message with small chat_compact_every_turns=10 and chat_window_turns=6
            # Current window has 12 turns >= 10, so compaction must trigger
            reply = await chat_engine.process_message(
                session=session,
                user_id=user_id,
                role="user",
                character_id=char.character_id,
                user_text="سلام، حالت چطوره؟",
                chat_compact_every_turns=10,
                chat_window_turns=6,
            )
            self.assertEqual(reply, "پاسخ تستی")
            self.assertEqual(mock_model.compact_call_count, 1)
            # 12 turns in window - 6 window turns kept = 6 turns leaving
            self.assertEqual(len(mock_model.last_leaving_turns), 6)

            # Reload memory from database and verify persistence
            reloaded_mem = await load_chat_memory(session, sess_rec.session_id)
            self.assertEqual(reloaded_mem.summary, "کاربر به هنر انیمه و سفر علاقه دارد.")
            self.assertIn("کاربر برنامه‌نویس است", reloaded_mem.facts)
            self.assertNotIn("فکت قدیمی", reloaded_mem.facts)
            # Window has 6 kept turns + 2 new turns (user message + assistant reply) = 8 turns
            self.assertEqual(len(reloaded_mem.window), 8)

            # Verify prompt context format
            context_block = reloaded_mem.to_prompt_context()
            self.assertIn("Dialogue Summary:\nکاربر به هنر انیمه و سفر علاقه دارد.", context_block)
            self.assertIn("- کاربر برنامه‌نویس است", context_block)


if __name__ == "__main__":
    unittest.main()
