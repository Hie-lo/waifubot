"""Bot unit tests: auth gate, 24h dialog memory, posting, recovery (SPEC §13)."""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from waifubot.bot.auth import AdminGate
from waifubot.bot.memory import DialogMemory
from waifubot.bot.posting import publish_image, resolve_unknown_send
from waifubot.bot.recovery import startup_recover
from waifubot.db import models  # noqa: F401
from waifubot.db.base import Base
from waifubot.db.models import Image, Job, Post, Setting
from waifubot.db.session import make_engine, make_session_factory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fakes import FakeGateway

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


@pytest.fixture()
def sessions(tmp_path: Path):  # noqa: ANN201
    async def setup():  # noqa: ANN202
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, make_session_factory(engine)

    engine, factory = _run(setup())
    yield factory, tmp_path
    _run(engine.dispose())


def test_admin_gate() -> None:
    gate = AdminGate((1, 2))
    assert gate.allow(1) is True
    assert gate.allow(2) is True
    assert gate.allow(3) is False


def test_dialog_memory_roundtrip_and_expiry(sessions) -> None:  # noqa: ANN001
    factory, _ = sessions
    memory = DialogMemory(factory)

    async def body() -> None:
        await memory.set(7, {"kind": "generate", "step": "style"})
        assert (await memory.get(7))["step"] == "style"
        assert await memory.get(8) is None
        await memory.clear(7)
        assert await memory.get(7) is None
        # 24h expiry: age the row beyond the TTL.
        await memory.set(7, {"kind": "generate", "step": "style"})
        async with factory() as s:
            await s.execute(
                update(Setting).where(Setting.key == "dialog:7").values(
                    updated_at=datetime.now(UTC) - timedelta(hours=25)
                )
            )
            await s.commit()
        assert await memory.get(7) is None  # expired -> gone

    _run(body())


def _make_image(tmp_path: Path, job_id: int, **kw) -> Image:  # noqa: ANN001
    rel = kw.pop("file_path", "images/test.png")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return Image(
        job_id=job_id,
        provider="mock",
        file_path=rel,
        mime_type="image/png",
        width=kw.get("width", 512),
        height=kw.get("height", 512),
        file_size=kw.get("file_size", 10),
        sha256="a" * 64,
        status=kw.get("status", "approved"),
    )


def test_publish_success_fails_and_ambiguous(sessions) -> None:  # noqa: ANN001
    factory, tmp_path = sessions
    gw = FakeGateway()

    async def body() -> None:
        async with factory() as s:
            job = Job(status="succeeded", idempotency_key="k1", prompt_snapshot="p",
                      template_version="t", filters_json="{}")
            s.add(job)
            await s.flush()
            img = _make_image(tmp_path, job.id)
            s.add(img)
            await s.commit()
            img_id = img.id

        # success
        async with factory() as s:
            img = await s.get(Image, img_id)
            out = await publish_image(s, gw, image=img, chat_id="-100", caption="hi",
                                      storage_root=tmp_path)
            await s.commit()
            assert out.status == "sent" and out.message_id == 1
            assert img.status == "published"
        # already sent -> refuse
        async with factory() as s:
            img = await s.get(Image, img_id)
            img.status = "approved"  # even if someone resets the image
            out = await publish_image(s, gw, image=img, chat_id="-100", caption="hi",
                                      storage_root=tmp_path)
            await s.commit()
            assert out.status == "already"

        # failure never regenerates and keeps the image approved
        async with factory() as s:
            job2 = Job(status="succeeded", idempotency_key="k2", prompt_snapshot="p",
                       template_version="t", filters_json="{}")
            s.add(job2)
            await s.flush()
            img2 = _make_image(tmp_path, job2.id, file_path="images/t2.png")
            s.add(img2)
            await s.commit()
            img2_id = img2.id
        gw.fail_mode = "error"
        async with factory() as s:
            img2 = await s.get(Image, img2_id)
            out = await publish_image(s, gw, image=img2, chat_id="-100", caption="hi",
                                      storage_root=tmp_path)
            await s.commit()
            assert out.status == "failed" and img2.status == "approved"

        # ambiguous timeout -> unknown -> both resolution buttons work
        gw.fail_mode = "timeout"
        async with factory() as s:
            img2 = await s.get(Image, img2_id)
            out = await publish_image(s, gw, image=img2, chat_id="-100", caption="hi",
                                      storage_root=tmp_path)
            await s.commit()
            assert out.status == "unknown"
            unknown_id = out.post.id
        gw.fail_mode = None
        async with factory() as s:
            post = await s.get(Post, unknown_id)
            await resolve_unknown_send(s, post=post, was_sent=True, message_id=42)
            await s.commit()
            assert post.status == "sent" and post.telegram_message_id == 42
        # resolve again from unknown only: make a new unknown and resolve "not sent"
        gw.fail_mode = "timeout"
        async with factory() as s:
            img2 = await s.get(Image, img2_id)
            img2.status = "approved"
            out = await publish_image(s, gw, image=img2, chat_id="-200", caption="hi",
                                      storage_root=tmp_path)
            await s.commit()
            nid2 = out.post.id
        gw.fail_mode = None
        async with factory() as s:
            post = await s.get(Post, nid2)
            await resolve_unknown_send(s, post=post, was_sent=False)
            await s.commit()
            assert post.status == "pending"

    _run(body())


def test_publish_rejects_oversized(sessions) -> None:  # noqa: ANN001
    factory, tmp_path = sessions
    gw = FakeGateway()

    async def body() -> None:
        async with factory() as s:
            job = Job(status="succeeded", idempotency_key="k3", prompt_snapshot="p",
                      template_version="t", filters_json="{}")
            s.add(job)
            await s.flush()
            img = _make_image(tmp_path, job.id, file_size=11 * 1024 * 1024,
                              file_path="images/big.png")
            s.add(img)
            await s.commit()
            out = await publish_image(s, gw, image=img, chat_id="-100", caption="hi",
                                      storage_root=tmp_path)
            await s.commit()
            assert out.status == "failed" and out.error == "too_large"
            assert img.status == "approved"

    _run(body())


def test_startup_recovery(sessions) -> None:  # noqa: ANN001
    factory, tmp_path = sessions

    async def body() -> None:
        async with factory() as s:
            s.add(Job(status="running", idempotency_key="r1", prompt_snapshot="p",
                      template_version="t", filters_json="{}"))
            s.add(Job(status="pending", idempotency_key="r2", prompt_snapshot="p",
                      template_version="t", filters_json="{}"))
            await s.flush()
            job = (await s.execute(
                select(Job).where(Job.status == "running")
            )).scalars().one()
            img = _make_image(tmp_path, job.id, file_path="images/gone.png")
            (tmp_path / "images" / "gone.png").unlink()  # simulate lost file
            s.add(img)
            await s.commit()
            job_id, img_id = job.id, img.id
        (tmp_path / "images").mkdir(parents=True, exist_ok=True)
        (tmp_path / "images" / "x.part").write_bytes(b"partial")
        stats = await startup_recover(factory, tmp_path)
        assert stats["interrupted_jobs"] == 1
        assert stats["part_files_removed"] == 1
        assert stats["missing_images"] == 1
        async with factory() as s:
            job = await s.get(Job, job_id)
            assert job.status == "failed" and job.error_code == "interrupted"
            img = await s.get(Image, img_id)
            assert img.status == "missing"
            other = (await s.execute(
                select(Job).where(Job.status == "pending")
            )).scalars().one()
            assert other.status == "pending"  # untouched

    _run(body())
