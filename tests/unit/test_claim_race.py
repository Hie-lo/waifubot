"""Unit tests verifying atomic first-come-first-served claiming race condition resilience (R61)."""

import asyncio
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.bot.handlers.ownership import CharacterOwnershipUIHandler
from waifubot.core.limits import effective_limits
from waifubot.db.base import Base
from waifubot.services.identity import create_character
from waifubot.services.ownership import (
    claim_character,
    get_character_ownership,
    record_claim_post,
    set_character_status,
)


class MockRacePublisher:
    def __init__(self) -> None:
        self.edit_count = 0

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: object = None,
    ) -> bool:
        self.edit_count += 1
        return True


class TestClaimRace(unittest.IsolatedAsyncioTestCase):
    """Verifies that simultaneous claims on the same character result in exactly 1 winner (R61)."""

    async def asyncSetUp(self) -> None:
        # SQLite with WAL or standard memory database
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.ui_handler = CharacterOwnershipUIHandler()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_concurrent_claim_race_single_winner(self) -> None:
        """Multiple users claiming the same listed character concurrently: exactly 1 wins (R61)."""
        async with self.session_factory() as session:
            char = await create_character(
                session, "Kurisu Makise", "Red hair, lab coat", created_by=1
            )
            await set_character_status(session, char.character_id, "listed")
            await record_claim_post(session, char.character_id, chat_id=-10099, message_id=123)
            await session.commit()
            char_id = char.character_id

        user_ids = [1001, 1002, 1003, 1004, 1005]

        async def attempt_claim(uid: int) -> tuple[int, bool, str]:
            async with self.session_factory() as s:
                ok, reason = await claim_character(
                    session=s,
                    character_id=char_id,
                    user_id=uid,
                    user_limits=effective_limits("user", overrides={"max_owned_characters": 5}),
                )
                if ok:
                    await s.commit()
                return uid, ok, reason

        results = await asyncio.gather(*(attempt_claim(uid) for uid in user_ids))

        winners = [r for r in results if r[1] is True]
        losers = [r for r in results if r[1] is False]

        # Exactly 1 winner
        self.assertEqual(len(winners), 1, f"Expected exactly 1 winner, got {len(winners)}")
        self.assertEqual(winners[0][2], "claimed")

        # All losers got 'already_claimed'
        self.assertEqual(len(losers), 4)
        for _, ok, reason in losers:
            self.assertFalse(ok)
            self.assertEqual(reason, "already_claimed")

        # Verify DB state
        async with self.session_factory() as verify_session:
            ow = await get_character_ownership(verify_session, char_id)
            self.assertIsNotNone(ow)
            assert ow is not None
            self.assertEqual(ow.status, "claimed")
            self.assertEqual(ow.owner_user_id, winners[0][0])
            self.assertFalse(ow.claimable)

    async def test_ui_claim_race_and_post_edit(self) -> None:
        """Simultaneous UI claim requests: winner gets success message, loser gets already_claimed (R61)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Chika Fujiwara", "Pink hair, bow", created_by=1)
            await set_character_status(session, char.character_id, "listed")
            await record_claim_post(session, char.character_id, chat_id=-10077, message_id=456)
            await session.commit()
            char_id = char.character_id

        publisher = MockRacePublisher()

        async def attempt_ui_claim(uid: int) -> tuple[str, list[list[dict[str, object]]]]:
            async with self.session_factory() as s:
                text, kb = await self.ui_handler.handle_claim(
                    session=s,
                    character_id=char_id,
                    user_id=uid,
                    user_limits=effective_limits("user", overrides={"max_owned_characters": 5}),
                    publisher=publisher,
                    locale="fa",
                )
                await s.commit()
                return text, kb

        res1, res2 = await asyncio.gather(attempt_ui_claim(2001), attempt_ui_claim(2002))

        texts = [res1[0], res2[0]]
        self.assertTrue(any("با موفقیت به شما واگذار شد" in t for t in texts))
        self.assertTrue(any("پیش از شما توسط کاربر دیگری دریافت شده است" in t for t in texts))
        # Best effort edit called
        self.assertGreaterEqual(publisher.edit_count, 1)


if __name__ == "__main__":
    unittest.main()
