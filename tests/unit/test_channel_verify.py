"""Unit tests for channel 3-step verification and adder permissions (R56, R57)."""

import json
import time
import unittest
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.core.limits import Limits, effective_limits
from waifubot.db.base import Base
from waifubot.db.models import PlanRecord
from waifubot.services.channels import (
    ChannelVerificationError,
    verify_channel,
)
from waifubot.services.quota import QuotaExceededError


class MockChannelProbe:
    """Mock implementation of ChannelProbe for deterministic testing."""

    def __init__(self) -> None:
        self.chats: dict[int | str, dict[str, Any]] = {}
        self.member_roles: dict[tuple[int, int], str] = {}
        self.bot_rights: dict[int, dict[str, bool]] = {}

    async def get_chat_info(self, chat_ref: str | int) -> dict[str, Any]:
        if chat_ref not in self.chats:
            raise ValueError(f"Chat {chat_ref} not found")
        return self.chats[chat_ref]

    async def get_chat_member_role(self, chat_ref: str | int, user_id: int) -> str | None:
        chat_id = chat_ref if isinstance(chat_ref, int) else self.chats[chat_ref]["id"]
        return self.member_roles.get((chat_id, user_id))

    async def get_bot_rights(self, chat_ref: str | int) -> dict[str, bool]:
        chat_id = chat_ref if isinstance(chat_ref, int) else self.chats[chat_ref]["id"]
        return self.bot_rights.get(chat_id, {})


class TestChannelVerification(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        # Seed built-in free plan
        async with self.session_factory() as session:
            free_plan = PlanRecord(
                id="free",
                name="Free",
                limits_json=json.dumps(
                    {
                        "max_owned_characters": 3,
                        "daily_image_budget": 8,
                        "max_images_per_job": 4,
                        "max_concurrent_jobs": 1,
                        "max_stored_images": 100,
                        "daily_chat_messages": 60,
                        "chat_memory_tokens": 500,
                        "max_channels": 0,
                        "queue_priority": 0,
                    }
                ),
                is_builtin=True,
                created_at=time.time(),
            )
            session.add(free_plan)
            await session.commit()

        self.probe = MockChannelProbe()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_step1_chat_not_found(self) -> None:
        """Step 1 fails if chat does not exist or bot cannot see it."""
        async with self.session_factory() as session:
            with self.assertRaises(ChannelVerificationError) as ctx:
                await verify_channel(
                    session=session,
                    probe=self.probe,
                    chat_ref=-100999,
                    adder_user_id=1,
                    adder_role="superadmin",
                )
            self.assertEqual(ctx.exception.reason, "chat_not_found")

    async def test_step2_bot_insufficient_rights(self) -> None:
        """Step 2 fails if bot is not admin with post messages rights."""
        self.probe.chats[-100100] = {
            "id": -100100,
            "title": "Anime Channel",
            "type": "channel",
            "username": "anime_chan",
        }
        self.probe.bot_rights[-100100] = {"can_post_messages": False}

        async with self.session_factory() as session:
            with self.assertRaises(ChannelVerificationError) as ctx:
                await verify_channel(
                    session=session,
                    probe=self.probe,
                    chat_ref=-100100,
                    adder_user_id=1,
                    adder_role="superadmin",
                )
            self.assertEqual(ctx.exception.reason, "bot_insufficient_rights")

    async def test_step3_adder_not_owner(self) -> None:
        """Step 3 fails if regular admin/user is not creator or admin of the chat (R57)."""
        self.probe.chats[-100200] = {
            "id": -100200,
            "title": "Anime Group",
            "type": "channel",
            "username": "anime_group",
        }
        self.probe.bot_rights[-100200] = {"can_post_messages": True}
        self.probe.member_roles[(-100200, 50)] = "member"  # Just a member

        async with self.session_factory() as session:
            with self.assertRaises(ChannelVerificationError) as ctx:
                await verify_channel(
                    session=session,
                    probe=self.probe,
                    chat_ref=-100200,
                    adder_user_id=50,
                    adder_role="admin",
                )
            self.assertEqual(ctx.exception.reason, "channel_not_yours")

    async def test_superadmin_ownership_exemption(self) -> None:
        """Superadmin is exempt from the adder ownership check (R53, R57)."""
        self.probe.chats[-100300] = {
            "id": -100300,
            "title": "Official Anime News",
            "type": "channel",
            "username": "anime_news",
        }
        self.probe.bot_rights[-100300] = {"can_post_messages": True}
        # Notice: superadmin user 999 is NOT in member_roles

        async with self.session_factory() as session:
            channel = await verify_channel(
                session=session,
                probe=self.probe,
                chat_ref=-100300,
                adder_user_id=999,
                adder_role="superadmin",
            )
            await session.commit()

            self.assertIsNotNone(channel)
            self.assertEqual(channel.tg_chat_id, -100300)
            self.assertEqual(channel.status, "active")
            self.assertEqual(channel.scope, "shared")

    async def test_user_plan_channel_capacity_check(self) -> None:
        """Users can only add channels if permitted by their plan (R52, R57, R59)."""
        self.probe.chats[-100400] = {
            "id": -100400,
            "title": "User Personal Channel",
            "type": "channel",
        }
        self.probe.bot_rights[-100400] = {"can_post_messages": True}
        self.probe.member_roles[(-100400, 10)] = "creator"

        async with self.session_factory() as session:
            # Free plan: max_channels = 0
            user_limits = effective_limits("user")
            with self.assertRaises(QuotaExceededError) as ctx:
                await verify_channel(
                    session=session,
                    probe=self.probe,
                    chat_ref=-100400,
                    adder_user_id=10,
                    adder_role="user",
                    limits=user_limits,
                )
            self.assertEqual(ctx.exception.limit_name, "max_channels")

            # VIP plan with max_channels = 2
            vip_limits = Limits(
                max_owned_characters=5,
                daily_image_budget=50,
                max_images_per_job=8,
                max_concurrent_jobs=2,
                max_stored_images=200,
                daily_chat_messages=100,
                chat_memory_tokens=1000,
                max_channels=2,
                queue_priority=5,
            )
            channel = await verify_channel(
                session=session,
                probe=self.probe,
                chat_ref=-100400,
                adder_user_id=10,
                adder_role="user",
                limits=vip_limits,
            )
            await session.commit()
            self.assertEqual(channel.tg_chat_id, -100400)
            self.assertEqual(channel.scope, "private")

    async def test_reverify_existing_channel_updates_fields(self) -> None:
        """Re-verifying an existing channel updates title and rights without creating duplicates."""
        self.probe.chats[-100500] = {
            "id": -100500,
            "title": "Old Title",
            "type": "channel",
        }
        self.probe.bot_rights[-100500] = {"can_post_messages": True}

        async with self.session_factory() as session:
            ch1 = await verify_channel(
                session=session,
                probe=self.probe,
                chat_ref=-100500,
                adder_user_id=1,
                adder_role="superadmin",
            )
            await session.commit()
            orig_id = ch1.id

            # Update title in Telegram
            self.probe.chats[-100500]["title"] = "Updated New Title"
            ch2 = await verify_channel(
                session=session,
                probe=self.probe,
                chat_ref=-100500,
                adder_user_id=1,
                adder_role="superadmin",
            )
            await session.commit()

            self.assertEqual(ch2.id, orig_id)
            self.assertEqual(ch2.title, "Updated New Title")


if __name__ == "__main__":
    unittest.main()
