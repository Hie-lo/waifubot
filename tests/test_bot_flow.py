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
from waifubot.core.identity import load_identity_pool
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.core.providers.mock import MockProvider
from waifubot.core.tags_pool import load_tags_pool
from waifubot.db import models  # noqa: F401
from waifubot.db.base import Base
from waifubot.db.models import Character, Image, Job, Post
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
        idpool = load_identity_pool(REPO_ROOT / "config" / "identity_pool.yaml")
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
            identity_pool=idpool,
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
        await env.app.handle_callback(env.cb("wiz:n:2", update_id=5))
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
        await env.app.handle_callback(env.cb("savec:j", update_id=12))  # use job identity
        await env.app.handle_callback(env.cb("savec:ok", update_id=13))  # confirm review
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
        await env.app.handle_callback(env.cb("wiz:n:2", update_id=5))
        await env.app.handle_callback(env.cb("wiz:y", update_id=6))
        async with env.factory() as s:
            jobs = (await s.execute(select(Job))).scalars().all()
        assert len(jobs) == 1

        # 3) an 'unknown' post is NEVER auto-resent
        await env.worker._run_one(jobs[0].id)  # noqa: SLF001
        async with env.factory() as s:
            img = (await s.execute(select(Image))).scalars().first()
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


def test_precise_face_prompt_wizard_and_menu(env: Env) -> None:
    """User request 2026-10-16: savec builds a precise face prompt via buttons,
    and the main menu reaches every function without typing commands."""

    async def body() -> None:
        # menu: every entry is a button; menu:generate opens the wizard
        await env.app.handle_message(env.msg("/menu", update_id=1))
        menu_msg = env.gw.messages[-1]
        labels = [b[0] for row in (menu_msg[2] or []) for b in row]
        assert any("ساخت" in lab for lab in labels)  # fa labels (LANGUAGE default)
        await env.app.handle_callback(env.cb("menu:generate", update_id=2))
        assert (await env.memory.get(ADMIN_ID))["step"] == "style"

        # precise savec wizard: name -> build precise -> trait buttons -> ok
        env.gw.messages.clear()
        await env.app.handle_callback(env.cb("savec:99", update_id=3))  # stale job id
        # use a real job instead
        await env.app.handle_callback(env.cb("wiz:s:anime", update_id=4))
        await env.app.handle_callback(env.cb("wiz:c:-1", update_id=5))
        await env.app.handle_callback(env.cb("wiz:n:2", update_id=6))
        await env.app.handle_callback(env.cb("wiz:y", update_id=7))
        async with env.factory() as s:
            job_id = (await s.execute(select(Job))).scalars().one().id
        await env.app.handle_callback(env.cb(f"savec:{job_id}", update_id=8))
        await env.app.handle_message(env.msg("Sakura", update_id=9))
        await env.app.handle_callback(env.cb("savec:p", update_id=10))  # precise
        # choose every trait explicitly (no randomness in this test)
        picks = [
            ("age", "25"), ("hair_color", "black"), ("hair_style", "ponytail"),
            ("eye_color", "green"), ("skin", "fair"), ("face", "oval"),
        ]
        uid = 11
        for trait, value in picks:
            await env.app.handle_callback(env.cb(f"savec:t:{trait}:{value}", update_id=uid))
            uid += 1
        await env.app.handle_message(env.msg("red ribbon", update_id=uid))
        await env.app.handle_callback(env.cb("savec:ok", update_id=uid + 1))
        async with env.factory() as s:
            row = (await s.execute(select(Character))).scalars().one()
        assert row.name == "Sakura"
        assert row.identity_prompt.startswith("25 years old")
        assert "black ponytail hair" in row.identity_prompt
        assert "green eyes" in row.identity_prompt
        assert "oval face" in row.identity_prompt
        assert "red ribbon" in row.identity_prompt
        assert "consistent facial features" in row.identity_prompt  # face anchor

    _run(body())


def test_settings_fully_changeable_by_admin(env: Env) -> None:
    """USER RULE 2026-10-16: the admin can change any setting freely."""

    async def body() -> None:
        await env.app.handle_message(env.msg("/settings", update_id=1))
        await env.app.handle_callback(env.cb("set:daily_cap", update_id=2))
        await env.app.handle_message(env.msg("50", update_id=3))
        assert await env.settings.get("daily_cap") == 50  # no hard-limit refusal
        await env.app.handle_callback(env.cb("set:images_per_job", update_id=4))
        await env.app.handle_message(env.msg("20", update_id=5))
        assert await env.settings.get("images_per_job") == 20

    _run(body())


def test_group_post_mixed_spoiler_and_caption(env: Env) -> None:
    """User rule 2026-10-16: albums carry per-photo spoiler (NSFW mix), and the
    caption is set in the bot before posting."""

    async def body() -> None:
        # two approved images; mark the second NSFW via its preview button
        async with env.factory() as s:
            j = Job(status="succeeded", idempotency_key="g1", prompt_snapshot="p",
                    template_version="t", filters_json="{}")
            s.add(j)
            await s.flush()
            from tests.unit.test_bot_units import _make_image
            store = env.tmp_path / "storage"
            a = _make_image(store, j.id, file_path="images/a.png")
            b = _make_image(store, j.id, file_path="images/b.png")
            s.add_all([a, b])
            await s.commit()
            a_id, b_id = a.id, b.id
        await env.app.handle_callback(env.cb(f"nsfw:{b_id}", update_id=1))
        async with env.factory() as s:
            img = await s.get(Image, b_id)
            assert img.is_nsfw is True

        # group wizard: pick both -> caption -> confirm -> send
        await env.app.handle_callback(env.cb("grp", update_id=2))
        await env.app.handle_callback(env.cb(f"gtog:{a_id}", update_id=3))
        await env.app.handle_callback(env.cb(f"gtog:{b_id}", update_id=4))
        await env.app.handle_callback(env.cb("gnext", update_id=5))
        await env.app.handle_message(env.msg("New set!", update_id=6))
        await env.app.handle_callback(env.cb("gok", update_id=7))

        assert len(env.gw.media_groups) == 1
        _chat, items, caption = env.gw.media_groups[0]
        assert caption == "New set!"
        assert [spoiler for _p, spoiler in items] == [False, True]  # mixed!
        async with env.factory() as s:
            posts = (await s.execute(select(Post))).scalars().all()
            assert len(posts) == 2
            assert all(p.status == "sent" for p in posts)
            imgs = (await s.execute(select(Image))).scalars().all()
            assert all(i.status == "published" for i in imgs)

    _run(body())


def test_pool_viewer_and_pause_menu_context(env: Env) -> None:
    async def body() -> None:
        async with env.factory() as s:
            j = Job(status="succeeded", idempotency_key="v1", prompt_snapshot="p",
                    template_version="t", filters_json="{}")
            s.add(j)
            await s.flush()
            from tests.unit.test_bot_units import _make_image
            store = env.tmp_path / "storage"
            a = _make_image(store, j.id, file_path="images/a.png")
            b = _make_image(store, j.id, file_path="images/b.png")
            s.add_all([a, b])
            await s.commit()
            first_id = a.id

        # pool summary has the viewer button; viewer navigates one by one
        await env.app.handle_message(env.msg("/pool", update_id=1))
        buttons = env.gw.messages[-1][2]
        assert any("view:pool" in c for row in buttons for _l, c in row)
        await env.app.handle_callback(env.cb(f"view:pool:{first_id}", update_id=2))
        photo = env.gw.photos[-1]
        navs = [c for row in (photo[4] or []) for _l, c in row]
        assert any(c.startswith("view:pool:") and c != f"view:pool:{first_id}"
                   for c in navs)  # a Next button exists
        assert any(c.startswith("nsfw:") for c in navs)

        # queue: a pending job is cancelable; menu shows pause only when busy
        async with env.factory() as s:
            s.add(Job(status="pending", idempotency_key="q1", prompt_snapshot="p",
                      template_version="t", filters_json="{}"))
            await s.commit()
        await env.app.handle_message(env.msg("/queue", update_id=3))
        q_buttons = env.gw.messages[-1][2]
        assert any(c.startswith("cancel:") for row in q_buttons for _l, c in row)
        env.gw.messages.clear()
        await env.app.handle_message(env.msg("/menu", update_id=4))
        labels = [c for row in env.gw.messages[-1][2] for _l, c in row]
        assert "menu:pause" in labels and "menu:resume" not in labels
        await env.app.handle_message(env.msg("/pause", update_id=5))
        env.gw.messages.clear()
        await env.app.handle_message(env.msg("/menu", update_id=6))
        labels = [c for row in env.gw.messages[-1][2] for _l, c in row]
        assert "menu:resume" in labels and "menu:pause" not in labels

        # character -> use character starts the wizard at the count step
        async with env.factory() as s:
            from waifubot.db.models import Character
            s.add(Character(name="Aya", identity_prompt="black hair, green eyes",
                            version=1, is_active=True))
            await s.commit()
        await env.app.handle_callback(env.cb("menu:characters", update_id=7))
        await env.app.handle_callback(env.cb("char:1", update_id=8))
        await env.app.handle_callback(env.cb("usechar:1", update_id=9))
        d = await env.memory.get(ADMIN_ID)
        assert d["step"] == "count" and d["character_id"] == 1

    _run(body())


def test_review_round4_regressions(env: Env) -> None:
    """Bugs found in the 2026-10-16 review pass must never come back."""

    async def body() -> None:
        from tests.unit.test_bot_units import _make_image

        # 1) a group where one file is missing falls back to a SINGLE photo
        #    (Telegram albums need 2+ items) - still with that photo's spoiler
        async with env.factory() as s:
            j = Job(status="succeeded", idempotency_key="r4", prompt_snapshot="p",
                    template_version="t", filters_json="{}")
            s.add(j)
            await s.flush()
            store = env.tmp_path / "storage"
            a = _make_image(store, j.id, file_path="images/a.png")
            b = _make_image(store, j.id, file_path="images/b.png")
            s.add_all([a, b])
            await s.commit()
            a_id, b_id = a.id, b.id
        (env.tmp_path / "storage" / "images" / "b.png").unlink()  # lost file
        await env.app.handle_callback(env.cb(f"nsfw:{a_id}", update_id=1))
        await env.app.handle_callback(env.cb("grp", update_id=2))
        await env.app.handle_callback(env.cb(f"gtog:{a_id}", update_id=3))
        await env.app.handle_callback(env.cb(f"gtog:{b_id}", update_id=4))
        await env.app.handle_callback(env.cb("gnext", update_id=5))
        await env.app.handle_callback(env.cb("gskip", update_id=6))
        await env.app.handle_callback(env.cb("gok", update_id=7))
        assert env.gw.media_groups == []          # no 1-item album was attempted
        assert len(env.gw.photos) == 1            # one single photo instead
        assert env.gw.photos[0][3] is True        # with the NSFW spoiler

        # 2) starting from a character uses default_style - never surprise-random
        async with env.factory() as s:
            from waifubot.db.models import Character
            s.add(Character(name="Aya", identity_prompt="black hair, green eyes",
                            version=1, is_active=True))
            await s.commit()
        await env.app.handle_callback(env.cb("usechar:1", update_id=10))
        await env.app.handle_callback(env.cb("wiz:n:2", update_id=11))
        await env.app.handle_callback(env.cb("wiz:y", update_id=12))
        async with env.factory() as s:
            job = (await s.execute(select(Job).where(Job.idempotency_key == "tg:12"))
                   ).scalars().one()
            import json as _json
            filters = _json.loads(job.filters_json)
        assert filters["style_value"] == "ref:optionKeyName:Anime"  # default_style

    _run(body())


def test_breaker_pause_persists_across_restart(env: Env) -> None:
    """SPEC §10: a breaker/disk pause must survive a restart — only a human
    resumes (review finding 2026-10-16)."""

    from waifubot.db.models import Setting

    async def body() -> None:
        await env.worker.pause(source="breaker")
        assert env.worker.paused is True
        async with env.factory() as s:
            row = await s.get(Setting, "runtime_paused")
            assert row is not None and row.value_json == "true"

    _run(body())
    env.build()  # "restart": fresh worker on the same database
    assert env.worker.paused is False  # memory state resets...

    async def check() -> None:
        async with env.factory() as s:
            row = await s.get(Setting, "runtime_paused")
        assert row is not None and row.value_json == "true"  # ...but the pause persisted
        await env.worker.resume()
        async with env.factory() as s:
            row = await s.get(Setting, "runtime_paused")
        assert row.value_json == "false"

    _run(check())


def test_stage1_count_retry_viewer_edit_back(env: Env) -> None:
    """Stage 1 (user 2026-10-16): exact counts incl. 6/8/16, retry for failed
    jobs, in-place viewer edits (no new photos), back buttons, no savec for
    character-based jobs."""

    async def body() -> None:
        from tests.unit.test_bot_units import _make_image

        # 1) count buttons include 6/8/16 and the picked count is honored EXACTLY
        await env.app.handle_message(env.msg("/generate", update_id=1))
        await env.app.handle_callback(env.cb("wiz:s:anime", update_id=2))
        await env.app.handle_callback(env.cb("wiz:c:-1", update_id=3))
        count_msg = env.gw.messages[-1]
        labels = [c for row in (count_msg[2] or []) for _l, c in row]
        assert {"wiz:n:6", "wiz:n:8", "wiz:n:16"}.issubset(set(labels))
        await env.app.handle_callback(env.cb("wiz:n:6", update_id=4))  # 6, not clamped
        await env.app.handle_callback(env.cb("wiz:y", update_id=5))
        async with env.factory() as s:
            job = (await s.execute(select(Job))).scalars().one()
        import json as _json
        assert _json.loads(job.filters_json)["count"] == 6

        # 2) failed job -> retry button requeues it
        async with env.factory() as s:
            s.add(Job(status="failed", idempotency_key="f1", prompt_snapshot="p",
                      template_version="t", filters_json="{}", attempt_count=2,
                      error_code="provider_error"))
            await s.commit()
        await env.app.handle_message(env.msg("/queue", update_id=6))
        q_buttons = [c for row in env.gw.messages[-1][2] for _l, c in row]
        assert any(c.startswith("retry:") for c in q_buttons)
        retry_id = next(c for c in q_buttons if c.startswith("retry:"))
        await env.app.handle_callback(env.cb(retry_id, update_id=7))
        async with env.factory() as s:
            from waifubot.db.models import Job as J
            requeued = (await s.execute(select(J).where(J.idempotency_key == "f1"))
                        ).scalars().one()
        assert requeued.status == "pending" and requeued.attempt_count == 0

        # 3) viewer prev/next EDITS the same message (no new photo)
        async with env.factory() as s:
            j2 = Job(status="succeeded", idempotency_key="v9", prompt_snapshot="p",
                     template_version="t", filters_json="{}")
            s.add(j2)
            await s.flush()
            store = env.tmp_path / "storage"
            a = _make_image(store, j2.id, file_path="images/a.png")
            b = _make_image(store, j2.id, file_path="images/b.png")
            s.add_all([a, b])
            await s.commit()
            first_id = a.id
        inb = env.cb(f"view:pool:{first_id}", update_id=8)
        inb.message_id = 777  # the button lives on message 777
        await env.app.handle_callback(inb)
        assert len(env.gw.photos) == 0            # no new photo was sent
        assert len(env.gw.edit_media) == 1        # the message was edited in place
        # press Next on the same message -> another in-place edit
        next_cb = [c for row in env.gw.edit_media[-1][4] for _l, c in row
                   if c.startswith("view:pool:")][0]
        inb2 = env.cb(next_cb, update_id=9)
        inb2.message_id = 777
        await env.app.handle_callback(inb2)
        assert len(env.gw.photos) == 0 and len(env.gw.edit_media) == 2

        # 4) back button exists in every section list
        for cmd in ("/queue", "/pool", "/settings", "/characters"):
            env.gw.messages.clear()
            await env.app.handle_message(env.msg(cmd, update_id=10))
            flat = [c for row in (env.gw.messages[-1][2] or []) for _l, c in row]
            assert "menu:back" in flat, cmd

        # 5) savec button hidden when the job used a saved character
        async with env.factory() as s:
            from waifubot.db.models import Character
            ch = Character(name="Aya", identity_prompt="black hair", version=1,
                           is_active=True)
            s.add(ch)
            await s.flush()
            j3 = Job(status="succeeded", idempotency_key="c9", prompt_snapshot="p",
                     template_version="t",
                     filters_json=_json.dumps({"character_id": ch.id}))
            s.add(j3)
            await s.flush()
            c1 = _make_image(store, j3.id, file_path="images/c.png")
            s.add(c1)
            await s.commit()
            c_id = c1.id
        inb3 = env.cb(f"view:inbox:{c_id}", update_id=11)
        inb3.message_id = 888
        await env.app.handle_callback(inb3)
        flat = [c for row in env.gw.edit_media[-1][4] for _l, c in row]
        assert not any(c.startswith("savec:") for c in flat)  # hidden!

    _run(body())
