"""Relationship and constraint tests for the 5 tables (SPEC §5, Sprint 1a exit)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from waifubot.db import models  # noqa: F401 — registers tables
from waifubot.db.base import Base
from waifubot.db.session import make_engine, make_session_factory


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


@pytest.fixture()
def session_factory(tmp_path: Path):  # noqa: ANN201
    async def setup():  # noqa: ANN202
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, make_session_factory(engine)

    engine, factory = _run(setup())
    yield factory
    _run(engine.dispose())


def _add_character(session):  # noqa: ANN001, ANN202
    return models.Character(name="Aki", identity_prompt="adult woman, 25 years old")


def test_job_idempotency_key_unique(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            s.add(_add_character(s))
            await s.flush()
            s.add(models.Job(idempotency_key="tg:100"))
            s.add(models.Job(idempotency_key="tg:100"))
            with pytest.raises(IntegrityError):
                await s.flush()

    _run(body())


def test_post_unique_per_image_and_chat(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            job = models.Job(idempotency_key="k1")
            s.add(job)
            await s.flush()
            img = models.Image(job_id=job.id, file_path="images/a.png", sha256="ab" * 32)
            s.add(img)
            await s.flush()
            s.add(models.Post(image_id=img.id, chat_id="-1001"))
            s.add(models.Post(image_id=img.id, chat_id="-1001"))
            with pytest.raises(IntegrityError):
                await s.flush()

    _run(body())


def test_image_sha_unique_per_job(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            job = models.Job(idempotency_key="k2")
            s.add(job)
            await s.flush()
            s.add(models.Image(job_id=job.id, file_path="images/a.png", sha256="cd" * 32))
            s.add(models.Image(job_id=job.id, file_path="images/b.png", sha256="cd" * 32))
            with pytest.raises(IntegrityError):
                await s.flush()

    _run(body())


@pytest.mark.parametrize(
    ("model_cls", "kwargs", "bad_status"),
    [
        (models.Job, {"idempotency_key": "k3"}, "running-ish"),
        (models.Image, {"job_id": 1, "file_path": "x", "sha256": "ef" * 32}, "approved?"),
        (models.Post, {"image_id": 1, "chat_id": "-1"}, "sent!"),
    ],
)
def test_status_check_constraints(session_factory, model_cls, kwargs, bad_status) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            row_kwargs = dict(kwargs)
            if model_cls is models.Image or model_cls is models.Post:
                job = models.Job(idempotency_key=f"seed-{bad_status}")
                s.add(job)
                await s.flush()
                if model_cls is models.Post:
                    img = models.Image(job_id=job.id, file_path="x", sha256="12" * 32)
                    s.add(img)
                    await s.flush()
                    row_kwargs["image_id"] = img.id
                else:
                    row_kwargs["job_id"] = job.id
            s.add(model_cls(status=bad_status, **row_kwargs))
            with pytest.raises(IntegrityError):
                await s.flush()

    _run(body())


def test_foreign_keys_enforced(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            s.add(models.Job(idempotency_key="k4", character_id=999))
            with pytest.raises(IntegrityError):
                await s.flush()

    _run(body())


def test_character_versioning_relationship(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            v1 = _add_character(s)
            s.add(v1)
            await s.flush()
            v2 = models.Character(name="Aki", identity_prompt="adult woman, 25", parent_id=v1.id)
            s.add(v2)
            await s.commit()

        async with session_factory() as s:
            loaded = await s.get(models.Character, v2.id)
            assert loaded is not None
            parent = await loaded.awaitable_attrs.parent
            assert parent is not None
            assert parent.id == v1.id
            children = await parent.awaitable_attrs.children
            assert [c.id for c in children] == [v2.id]

    _run(body())


def test_job_image_relationship(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            job = models.Job(idempotency_key="k5")
            s.add(job)
            await s.flush()
            s.add(models.Image(job_id=job.id, file_path="images/a.png", sha256="34" * 32))
            await s.commit()

        async with session_factory() as s:
            loaded = await s.get(models.Job, job.id)
            assert loaded is not None
            assert len(loaded.images) == 1
            assert loaded.images[0].job.id == job.id

    _run(body())


def test_schema_has_expected_tables_and_indexes(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        from sqlalchemy import text

        async with session_factory() as s:
            rows = await s.execute(
                text(
                    "select name from sqlite_master "
                    "where type='table' and name not like 'sqlite_%'"
                )
            )
            tables = {r[0] for r in rows}
            assert {"characters", "jobs", "images", "posts", "settings"} <= tables

    _run(body())
    assert "ix_jobs_status_priority_created_id" in {
        ix.name for ix in models.Job.__table__.indexes
    }
    assert "ix_images_sha256" in {ix.name for ix in models.Image.__table__.indexes}


def test_settings_row_roundtrip(session_factory) -> None:  # noqa: ANN001
    async def body() -> None:
        async with session_factory() as s:
            s.add(models.Setting(key="posting_interval_s", value_json="3600", updated_by="admin"))
            await s.commit()
        async with session_factory() as s:
            row = await s.get(models.Setting, "posting_interval_s")
            assert row is not None
            assert row.value_json == "3600"

    _run(body())
