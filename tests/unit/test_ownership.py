"""Unit tests for character ownership, claiming, catalog, and admin controls (R60, R61)."""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.bot.handlers.ownership import CharacterOwnershipUIHandler
from waifubot.core.limits import effective_limits
from waifubot.db.base import Base
from waifubot.services.identity import create_character
from waifubot.services.ownership import (
    claim_character,
    count_user_owned_characters,
    create_character_in_dm,
    ensure_character_ownership,
    get_character_ownership,
    get_random_unclaimed_character,
    list_catalog_characters,
    reassign_ownership,
    record_claim_post,
    release_character,
    revoke_ownership,
    set_character_status,
    update_claim_posts_best_effort,
)


class MockPublisher:
    def __init__(self) -> None:
        self.edited_messages: list[dict[str, object]] = []

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: object = None,
    ) -> bool:
        self.edited_messages.append(
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
        )
        return True


class TestCharacterOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.ui_handler = CharacterOwnershipUIHandler()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_initial_state_and_status_transitions(self) -> None:
        """Staff characters start as draft and can transition to listed and claimed (R60)."""
        async with self.session_factory() as session:
            char = await create_character(
                session=session,
                name="Asuka Langley",
                appearance_facts="Auburn hair, blue eyes, plugsuit",
                created_by=1,
            )
            # Default state is draft
            ownership = await ensure_character_ownership(session, char.character_id)
            self.assertEqual(ownership.status, "draft")
            self.assertFalse(ownership.claimable)
            self.assertIsNone(ownership.owner_user_id)

            # Transition to listed (claimable)
            ownership = await set_character_status(session, char.character_id, "listed")
            self.assertEqual(ownership.status, "listed")
            self.assertTrue(ownership.claimable)

            # Claim by user 100
            ok, reason = await claim_character(session, char.character_id, user_id=100)
            self.assertTrue(ok)
            self.assertEqual(reason, "claimed")

            updated = await get_character_ownership(session, char.character_id)
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.status, "claimed")
            self.assertEqual(updated.owner_user_id, 100)
            self.assertFalse(updated.claimable)
            self.assertIsNotNone(updated.claimed_at)

    async def test_claiming_respects_max_owned_characters_quota(self) -> None:
        """User cannot exceed max_owned_characters limit; superadmin is exempt (R53, R54, R61)."""
        async with self.session_factory() as session:
            user_limits = effective_limits("user", overrides={"max_owned_characters": 2})

            # Create 3 listed characters
            c1 = await create_character(session, "Char 1", "Facts", created_by=1)
            c2 = await create_character(session, "Char 2", "Facts", created_by=1)
            c3 = await create_character(session, "Char 3", "Facts", created_by=1)
            await set_character_status(session, c1.character_id, "listed")
            await set_character_status(session, c2.character_id, "listed")
            await set_character_status(session, c3.character_id, "listed")

            user_id = 200

            # 1. Claim 1st character -> ok
            ok1, r1 = await claim_character(
                session, c1.character_id, user_id, user_limits=user_limits
            )
            self.assertTrue(ok1)

            # 2. Claim 2nd character -> ok (now at capacity: 2)
            ok2, r2 = await claim_character(
                session, c2.character_id, user_id, user_limits=user_limits
            )
            self.assertTrue(ok2)

            count = await count_user_owned_characters(session, user_id)
            self.assertEqual(count, 2)

            # 3. Claim 3rd character -> limit_reached
            ok3, r3 = await claim_character(
                session, c3.character_id, user_id, user_limits=user_limits
            )
            self.assertFalse(ok3)
            self.assertEqual(r3, "limit_reached")

            # 4. Superadmin is exempt from claim limit (R53)
            ok_sa, r_sa = await claim_character(
                session, c3.character_id, user_id=999, user_limits=user_limits, is_superadmin=True
            )
            self.assertTrue(ok_sa)
            self.assertEqual(r_sa, "claimed")

    async def test_claim_disabled_master_toggle(self) -> None:
        """Claiming fails when claim_enabled master setting is False (R61)."""
        async with self.session_factory() as session:
            c = await create_character(session, "Char", "Facts", created_by=1)
            await set_character_status(session, c.character_id, "listed")

            ok, reason = await claim_character(
                session, c.character_id, user_id=300, claim_enabled=False
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "claim_disabled")

    async def test_create_character_in_dm_and_release(self) -> None:
        """Private characters in DM are owned from start; release deletes them (R60)."""
        async with self.session_factory() as session:
            user_limits = effective_limits("user", overrides={"max_owned_characters": 1})
            user_id = 400

            # 1. Create in DM
            char, status = await create_character_in_dm(
                session=session,
                user_id=user_id,
                name="My Waifu",
                appearance_facts="Silver hair, purple eyes",
                user_limits=user_limits,
            )
            self.assertEqual(status, "ok")
            self.assertIsNotNone(char)
            assert char is not None

            ow = await get_character_ownership(session, char.character_id)
            self.assertIsNotNone(ow)
            assert ow is not None
            self.assertEqual(ow.status, "private")
            self.assertEqual(ow.owner_user_id, user_id)

            # 2. Second creation fails quota
            char2, status2 = await create_character_in_dm(
                session=session,
                user_id=user_id,
                name="Waifu 2",
                appearance_facts="Facts",
                user_limits=user_limits,
            )
            self.assertEqual(status2, "limit_reached")
            self.assertIsNone(char2)

            # 3. Release private character -> deletes it
            ok_rel, act_rel = await release_character(session, char.character_id, user_id)
            self.assertTrue(ok_rel)
            self.assertEqual(act_rel, "deleted")

            # Ownership and character are deleted
            self.assertIsNone(await get_character_ownership(session, char.character_id))
            self.assertEqual(await count_user_owned_characters(session, user_id), 0)

    async def test_release_claimed_character_returns_to_catalog(self) -> None:
        """Releasing a claimed character returns it to 'listed' in catalog (R60)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Public Waifu", "Facts", created_by=1)
            await set_character_status(session, char.character_id, "listed")

            user_id = 500
            await claim_character(session, char.character_id, user_id)

            # User releases character
            ok, action = await release_character(session, char.character_id, user_id)
            self.assertTrue(ok)
            self.assertEqual(action, "released_to_listed")

            ow = await get_character_ownership(session, char.character_id)
            self.assertIsNotNone(ow)
            assert ow is not None
            self.assertEqual(ow.status, "listed")
            self.assertIsNone(ow.owner_user_id)
            self.assertTrue(ow.claimable)
            self.assertIsNotNone(ow.released_at)

    async def test_catalog_listing_and_random_selection(self) -> None:
        """Catalog lists only listed characters with pagination and random picks one (R61)."""
        async with self.session_factory() as session:
            for i in range(7):
                c = await create_character(session, f"Heroine {i}", f"Facts {i}", created_by=1)
                await set_character_status(session, c.character_id, "listed")

            # Claim one of them so it shouldn't show in catalog
            items, total = await list_catalog_characters(session, offset=0, limit=10)
            self.assertEqual(total, 7)
            first_char = items[0][0]
            await claim_character(session, first_char.character_id, user_id=600)

            # Total should now be 6
            items_page1, total_now = await list_catalog_characters(session, offset=0, limit=5)
            self.assertEqual(total_now, 6)
            self.assertEqual(len(items_page1), 5)

            items_page2, _ = await list_catalog_characters(session, offset=5, limit=5)
            self.assertEqual(len(items_page2), 1)

            # Random selection
            rand = await get_random_unclaimed_character(session)
            self.assertIsNotNone(rand)
            assert rand is not None
            self.assertIn(rand[0].name, [f"Heroine {i}" for i in range(7)])

    async def test_admin_revoke_and_reassign(self) -> None:
        """Admin can revoke or reassign ownership, generating audit logs (R44, R50)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Megumin", "Archwizard", created_by=1)
            await set_character_status(session, char.character_id, "listed")
            await claim_character(session, char.character_id, user_id=701)

            admin_id = 99

            # 1. Admin revokes ownership
            rev_ok = await revoke_ownership(session, char.character_id, admin_id=admin_id)
            self.assertTrue(rev_ok)

            ow = await get_character_ownership(session, char.character_id)
            self.assertIsNotNone(ow)
            assert ow is not None
            self.assertIsNone(ow.owner_user_id)
            self.assertEqual(ow.status, "listed")

            # 2. Admin reassigns ownership to user 702
            reas_ok = await reassign_ownership(
                session, char.character_id, new_owner_user_id=702, admin_id=admin_id
            )
            self.assertTrue(reas_ok)

            ow2 = await get_character_ownership(session, char.character_id)
            self.assertIsNotNone(ow2)
            assert ow2 is not None
            self.assertEqual(ow2.owner_user_id, 702)
            self.assertEqual(ow2.status, "claimed")

    async def test_claim_posts_and_best_effort_update(self) -> None:
        """Channel posts are recorded and button edited best-effort upon claim (R61)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Rem", "Blue maid", created_by=1)
            await set_character_status(session, char.character_id, "listed")

            # Record two posts where this character was published
            await record_claim_post(session, char.character_id, chat_id=-1001, message_id=50)
            await record_claim_post(session, char.character_id, chat_id=-1002, message_id=60)

            publisher = MockPublisher()
            edited = await update_claim_posts_best_effort(session, publisher, char.character_id)
            self.assertEqual(edited, 2)
            self.assertEqual(len(publisher.edited_messages), 2)
            self.assertEqual(publisher.edited_messages[0]["chat_id"], -1001)

    async def test_ui_handler_screens(self) -> None:
        """UI handler renders catalog, claim card, random confirm, and my characters (R60, R61)."""
        async with self.session_factory() as session:
            c = await create_character(session, "Holo", "Wise Wolf", created_by=1)
            await set_character_status(session, c.character_id, "listed")

            # 1. Catalog screen
            text, kb = await self.ui_handler.render_catalog(session, page=0, limit=5, locale="fa")
            self.assertIn("Holo", text)
            self.assertTrue(any("مشاهده و دریافت" in btn["text"] for row in kb for btn in row))

            # 2. Claim card screen
            text_card, kb_card = await self.ui_handler.render_claim_card(
                session, c.character_id, locale="fa"
            )
            self.assertIn("Holo", text_card)
            self.assertTrue(any("دریافت کاراکتر" in btn["text"] for row in kb_card for btn in row))

            # 3. Random confirm screen
            text_rnd, kb_rnd = await self.ui_handler.render_random_confirm(session, locale="fa")
            self.assertIn("Holo", text_rnd)
            self.assertTrue(any("تأیید و دریافت" in btn["text"] for row in kb_rnd for btn in row))

            # 4. Handle claim via UI
            pub = MockPublisher()
            msg_cl, kb_cl = await self.ui_handler.handle_claim(
                session=session,
                character_id=c.character_id,
                user_id=888,
                publisher=pub,
                locale="fa",
            )
            self.assertIn("با موفقیت به شما واگذار شد", msg_cl)

            # 5. My characters screen
            text_my, kb_my = await self.ui_handler.render_my_characters(
                session, user_id=888, locale="fa"
            )
            self.assertIn("Holo", text_my)

            # 6. Deep link handler (/start char_<id>)
            text_dl, kb_dl = await self.ui_handler.handle_deep_link(
                session, f"char_{c.character_id}", user_id=888, locale="fa"
            )
            self.assertIsNotNone(text_dl)


if __name__ == "__main__":
    unittest.main()
