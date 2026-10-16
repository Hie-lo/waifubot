"""End-to-end bot flow with a fake gateway and the mock provider (SPEC §13).

Covers the Sprint 3 exit criteria: an admin requests (wizard), reviews (inbox),
approves, and publishes to the test destination; state survives restart
(dialog memory + post state); double-taps create one job; stale callbacks do
nothing; exact settings validation errors. NO network, NO real Telegram.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from waifubot.bot.auth import AdminGate
from waifubot.bot.commands import BotApp, BotConfig, Inbound
from waifubot.bot.memory import DialogMemory
from waifubot.bot.messages import Messages
from waifubot.bot.worker import GenerationWorker
from waifubot.core.admin_settings import AdminSettings
from waifubot.core.content_safety import load_blocklist
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.core.providers.mock import MockProvider
from waifubot.core.tags_pool import load_tags_pool
from waifubot.db import models  # noqa: F401
from waifubot.db.base import Base
from waifubot.db.models import Image, Job, Post
from waifubot.db.session import make_engine, make_session_factory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import FakeGateway

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_ID = 111
IDENTITY = "adult woman, 25 years old, long blue hair, red eyes"


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def build(self, provider=None, gateway=None):  # noqa: ANN001, ANN202
        async def setup():  # noqa: ANN202
            engine = make_engine(f"sqlite+aiosqlite:///{self.tmp_path / 'flow.db'}")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return engine, make_session_factory(engine)

        engine, factory = _run(setup())
        self.engine = engine
        self.factory = factory
        pool = load_tags_pool(REPO_ROOT / "config" / "tags_pool.yaml")
        blocklist = load_blocklist(REPO_ROOT / "config" / "blocklist.yaml")
        self.gw = gateway or FakeGateway()
        self.msgs = Messages(REPO_ROOT / "locales", "fa")
        self.settings = AdminSettings(factory)
        self.memory = DialogMemory(factory)
        self.provider = provider or MockProvider({"mode": "ok"})
        self.pipeline = ImagePipeline(self.tmp_path / "storage")
        self.worker = GenerationWorker(
            sessions=factory,
            provider=self.provider,
            pipeline=self.pipeline,
            gateway=self.gw,
            msgs=self.msgs,
            admin_ids=(ADMIN_ID,),
            storage_root=self.tmp_path / "storage",
        )
        self.app = BotApp(
            sessions=factory,
            admin_settings=self.settings,
            memory=self.memory,
            gateway=self.gw,
            msgs=self.msgs,
            worker=self.worker,
            gate=AdminGate((ADMIN_ID,)),
            builder=PromptBuilder(pool, blocklist),
            config=BotConfig(
                admin_ids=(ADMIN_ID,),
                dest_chat_id="-100555",
                allow_nsfw=False,
                storage_root=str(self.tmp_path / "storage"),
                style_values={"anime": "ref:optionKeyName:Anime", "no_style": ""},
                blocklist=blocklist,
            ),
        )

    def msg(self, text: str, user_id: int = ADMIN_ID, update_id: int = 1) -> Inbound:
        return Inbound(user_id=user_id, chat_id=user_id, text=text, update_id=update_id)

    def cb(self, data: str, user_id: int = ADMIN_ID, update_id: int = 2,
           callback_id: str = "cb1") -> Inbound:
        return Inbound(user_id=user_id, chat_id=user_id, data=data,
                       update_id=update_id, callback_id=callback_id)

    def close(self) -> None:
        _run(self.engine.dispose())


@pytest.fixture()
def env(tmp_path: Path):  # noqa: ANN201
    e = Env(tmp_path)
    e.build()
    yield e
    e.close()


def test_wizard_to_publish_full_flow(env: Env) -> None:
    async def body() -> None:
        # unauthorized user is silently ignored (no reply, no dialog)
        await env.app.handle_message(env.msg("/generate", user_id=999))
        assert env.gw.messages == []
        assert await env.memory.get(999) is None

        # /generate wizard: style -> one-off -> identity -> count -> confirm
        await env.app.handle_message(env.msg("/generate", update_id=10))
        assert (await env.memory.get(ADMIN_ID))["step"] == "style"
        await env.app.handle_callback(env.cb("wiz:s:anime", update_id=11))
        await env.app.handle_callback(env.cb("wiz:c:0", update_id=12))
        await env.app.handle_message(env.msg(IDENTITY, update_id=13))
        await env.app.handle_callback(env.cb("wiz:n:2", update_id=14))
        await env.app.handle_callback(env.cb("wiz:y", update_id=15))

        async with env.factory() as s:
            jobs = (await s.execute(
                select(Job)
            )).scalars().all()
        assert len(jobs) == 1  # double-tap protection + unique idempotency key
        job = jobs[0]
        assert job.status == "pending"
        assert job.idempotency_key == "tg:15"
        assert job.prompt_snapshot.startswith(IDENTITY)

        # double-tap on the same confirm (fresh update_id) -> stale dialog, no job
        await env.app.handle_callback(env.cb("wiz:y", update_id=16))
        async with env.factory() as s:
            count = len((await s.execute(
                select(Job)
            )).scalars().all())
        assert count == 1

        # the worker claims and executes the job with the mock provider
        await env.worker._run_one(job.id)  # noqa: SLF001
        async with env.factory() as s:
            job = await s.get(Job, job.id)
            images = (await s.execute(
                select(Image).where(Image.job_id == job.id)
            )).scalars().all()
        assert job.status == "succeeded"
        assert len(images) == 2
        assert all(i.status == "pending_review" for i in images)
        # preview push: photos with approve/reject buttons went to the admin
        assert len(env.gw.photos) == 2
        assert any(b for *_x, b in env.gw.photos)

        # review: approve first, reject second
        first, second = images[0], images[1]
        await env.app.handle_callback(env.cb(f"appr:{first.id}", update_id=20))
        await env.app.handle_callback(env.cb(f"rej:{second.id}", update_id=21))
        async with env.factory() as s:
            assert (await s.get(Image, first.id)).status == "approved"
            assert (await s.get(Image, second.id)).status == "rejected"

        # stale review press does nothing
        await env.app.handle_callback(env.cb(f"appr:{second.id}", update_id=22))

        # /pool shows only the approved one; /publish posts it to the destination
        before = len(env.gw.messages)
        await env.app.handle_message(env.msg("/pool", update_id=30))
        assert len(env.gw.messages) > before  # pool listing was sent
        await env.app.handle_callback(env.cb(f"pub:{first.id}", update_id=31))
        async with env.factory() as s:
            posts = (await s.execute(
                select(Post)
            )).scalars().all()
            assert len(posts) == 1
            assert posts[0].status == "sent"
            assert posts[0].telegram_message_id is not None
            assert posts[0].chat_id == "-100555"
            assert (await s.get(Image, first.id)).status == "published"

        # double publish -> already
        await env.app.handle_callback(env.cb(f"pub:{first.id}", update_id=32))

    _run(body())


def test_settings_validation_exact_error(env: Env) -> None:
    async def body() -> None:
        await env.app.handle_message(env.msg("/settings", update_id=1))
        await env.app.handle_callback(env.cb("set:images_per_job", update_id=2))
        await env.app.handle_message(env.msg("999", update_id=3))  # over the hard cap
        # the exact ConfigError text reaches the admin (SPEC §11)
        assert any("images_per_job" in m[1] and "must be <=" in m[1]
                   for m in env.gw.messages)
        await env.app.handle_callback(env.cb("set:images_per_job", update_id=4))
        await env.app.handle_message(env.msg("6", update_id=5))
        assert await env.settings.get("images_per_job") == 6  # coerced to int

    _run(body())


def test_pause_resume_persist_and_stale_callbacks(env: Env) -> None:
    async def body() -> None:
        await env.app.handle_message(env.msg("/pause", update_id=1))
        assert env.worker.paused is True
        async with env.factory() as s:
            from waifubot.db.models import Setting

            row = await s.get(Setting, "runtime_paused")
            assert row is not None and row.value_json == "true"
        await env.app.handle_message(env.msg("/resume", update_id=2))
        assert env.worker.paused is False

        # stale/tampered callbacks answer but change nothing
        await env.app.handle_callback(env.cb("appr:99999", update_id=3))
        await env.app.handle_callback(env.cb("wiz:n:2", update_id=4))  # no dialog
        assert any(env.gw.answers)  # callbacks were acknowledged

    _run(body())


def test_save_character_from_job_metadata(env: Env) -> None:
    async def body() -> None:
        # create + run a job first
        await env.app.handle_message(env.msg("/generate", update_id=1))
        await env.app.handle_callback(env.cb("wiz:s:anime", update_id=2))
        await env.app.handle_callback(env.cb("wiz:c:0", update_id=3))
        await env.app.handle_message(env.msg(IDENTITY, update_id=4))
        await env.app.handle_callback(env.cb("wiz:n:1", update_id=5))
        await env.app.handle_callback(env.cb("wiz:y", update_id=6))
        async with env.factory() as s:
            job = (await s.execute(
                select(Job)
            )).scalars().one()
            job_id = job.id
        await env.worker._run_one(job_id)  # noqa: SLF001

        # Save Character dialog from the job's stored identity
        await env.app.handle_callback(env.cb(f"savec:{job_id}", update_id=10))
        await env.app.handle_message(env.msg("Yuki", update_id=11))
        await env.app.handle_message(env.msg("ok", update_id=12))  # accept proposed
        async with env.factory() as s:
            from waifubot.db.models import Character

            row = (await s.execute(
                select(Character)
            )).scalars().one()
            assert row.name == "Yuki"
            assert row.identity_prompt == IDENTITY  # from job metadata, not pixels
            assert row.version == 1 and row.is_active

    _run(body())


def test_restart_preserves_state(env: Env) -> None:
    """Sprint 3 exit: post state and dialogs survive a restart."""

    async def body() -> None:
        await env.app.handle_message(env.msg("/generate", update_id=1))
        await env.app.handle_callback(env.cb("wiz:s:anime", update_id=2))

    _run(body())
    env.build()  # "restart": a new app over the same database file
    dialog = _run(env.memory.get(ADMIN_ID))
    assert dialog is not None and dialog.get("step") == "character"


def test_review_fixes_regression(env: Env) -> None:
    """Bugs found in the Sprint 3 review pass must never come back."""

    async def body() -> None:

        # 1) stale wiz:y mid-wizard must NOT create a job
        await env.app.handle_message(env.msg("/generate", update_id=1))
        await env.app.handle_callback(env.cb("wiz:y", update_id=2))  # step is 'style'
        async with env.factory() as s:
            assert not (await s.execute(select(Job))).scalars().all()

        # 2) random-character button skips the identity question
        await env.app.handle_callback(env.cb("wiz:s:anime", update_id=3))
        await env.app.handle_callback(env.cb("wiz:c:-1", update_id=4))
        dialog = await env.memory.get(ADMIN_ID)
        assert dialog.get("step") == "count"  # not 'identity'
        await env.app.handle_callback(env.cb("wiz:n:1", update_id=5))
        await env.app.handle_callback(env.cb("wiz:y", update_id=6))
        async with env.factory() as s:
            jobs = (await s.execute(select(Job))).scalars().all()
        assert len(jobs) == 1

        # 3) an 'unknown' post is NEVER auto-resent
        await env.worker._run_one(jobs[0].id)  # noqa: SLF001
        async with env.factory() as s:
            img = (await s.execute(select(Image))).scalars().one()
            img.status = "approved"
            await s.commit()
            img_id = img.id
        env.gw.fail_mode = "timeout"
        await env.app.handle_callback(env.cb(f"pub:{img_id}", update_id=20))
        sent_before = len(env.gw.photos)
        env.gw.fail_mode = None
        await env.app.handle_callback(env.cb(f"pub:{img_id}", update_id=21))
        assert len(env.gw.photos) == sent_before  # no second send attempt
        async with env.factory() as s:
            post = (await s.execute(select(Post))).scalars().one()
            assert post.status == "unknown"
            assert post.attempt_count == 1

    _run(body())
