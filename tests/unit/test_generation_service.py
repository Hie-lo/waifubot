"""End-to-end generation service tests with the mock provider (SPEC §10 lifecycle)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from waifubot.core.content_safety import load_blocklist
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.core.providers.mock import MockProvider
from waifubot.core.tags_pool import load_tags_pool
from waifubot.db import models  # noqa: F401
from waifubot.db.base import Base
from waifubot.db.session import make_engine, make_session_factory
from waifubot.services.generation import run_generation_job

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY = "adult woman, 25 years old, long blue hair, red eyes"


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


@pytest.fixture()
def env(tmp_path: Path):  # noqa: ANN201
    pool = load_tags_pool(REPO_ROOT / "config" / "tags_pool.yaml")
    blocklist = load_blocklist(REPO_ROOT / "config" / "blocklist.yaml")

    async def setup():  # noqa: ANN202
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, make_session_factory(engine)

    engine, factory = _run(setup())
    yield factory, PromptBuilder(pool, blocklist), ImagePipeline(tmp_path / "storage")
    _run(engine.dispose())


def _generate(factory, builder, pipeline, mode, **kwargs):  # noqa: ANN001, ANN202
    async def body():  # noqa: ANN202
        async with factory() as session:
            result = await run_generation_job(
                session,
                provider=MockProvider({"mode": mode, "partial_count": 2}),
                pipeline=pipeline,
                builder=builder,
                identity=IDENTITY,
                style_lock=None,
                count=4,
                seed=42,
                **kwargs,
            )
            await session.commit()
            return result

    return _run(body())


def test_ok_job_succeeds_and_stores_images(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    job, images, result = _generate(factory, builder, pipeline, "ok")
    assert job.status == "succeeded"
    assert len(images) == 4
    assert all(img.status == "pending_review" for img in images)
    assert job.prompt_snapshot.startswith(IDENTITY)
    filters = json.loads(job.filters_json)
    assert filters["seed"] == 42
    assert filters["preset_ids"]
    assert result.status == "ok"


def test_blocked_job_fails_permanently(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "blocked")
    assert job.status == "failed"
    assert job.error_code == "blocked_by_provider"
    assert images == []


def test_timeout_job_fails(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "timeout")
    assert job.status == "failed"
    assert job.error_code == "provider_timeout"
    assert images == []


def test_partial_still_succeeds_with_images(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "partial")
    assert job.status == "succeeded"
    assert len(images) == 2


def test_malformed_candidates_filtered(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "malformed")
    assert job.status == "succeeded"
    assert len(images) == 2  # 2 of 4 are corrupt and rejected


def test_empty_fails_with_no_valid_images(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "empty")
    assert job.status == "failed"
    assert job.error_code == "no_valid_images"
    assert images == []


def test_idempotency_key_unique(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    _generate(factory, builder, pipeline, "ok", idempotency_key="tg:100")

    async def second() -> None:
        async with factory() as session:
            with pytest.raises(IntegrityError):
                await run_generation_job(
                    session,
                    provider=MockProvider({"mode": "ok"}),
                    pipeline=pipeline,
                    builder=builder,
                    identity=IDENTITY,
                    style_lock=None,
                    count=2,
                    seed=1,
                    idempotency_key="tg:100",
                )

    _run(second())


def test_image_rows_carry_validation_metadata(env) -> None:  # noqa: ANN001
    factory, builder, pipeline = env
    _job, images, _ = _generate(factory, builder, pipeline, "ok")
    for img in images:
        assert img.sha256 and len(img.sha256) == 64
        assert img.width == 512 and img.height == 512
        assert img.mime_type == "image/png"
        assert img.seed and img.seed.startswith("mock-")
        assert img.file_path.startswith("images/")


def test_hard_limits_are_enforced(env) -> None:  # noqa: ANN001
    # Review 2026-10-16: these caps were enforced NOWHERE before (latent bug).
    from waifubot.errors import ValidationError

    factory, builder, pipeline = env

    async def body() -> None:
        async with factory() as session:
            with pytest.raises(ValidationError, match="count"):
                await run_generation_job(
                    session,
                    provider=MockProvider({"mode": "ok"}),
                    pipeline=pipeline,
                    builder=builder,
                    identity=IDENTITY,
                    style_lock=None,
                    count=21,
                    seed=42,
                )
        async with factory() as session:
            with pytest.raises(ValidationError, match="daily job cap"):
                await run_generation_job(
                    session,
                    provider=MockProvider({"mode": "ok"}),
                    pipeline=pipeline,
                    builder=builder,
                    identity=IDENTITY,
                    style_lock=None,
                    count=2,
                    seed=42,
                    max_jobs_per_day=0,
                )

    _run(body())


def test_same_bytes_stored_once_across_jobs(env) -> None:  # noqa: ANN001
    # Review 2026-10-16: cross-job SHA-256 dedupe (SPEC §3.3) was missing.
    from io import BytesIO

    from PIL import Image as PILImage

    from waifubot.core.image_provider import (
        STATUS_OK,
        GenerationRequest,
        GenerationResult,
        ImageCandidate,
    )

    buf = BytesIO()
    PILImage.new("RGB", (300, 300), (10, 20, 30)).save(buf, format="PNG")
    fixed = buf.getvalue()

    class FixedProvider:
        name = "fixed"

        async def health_check(self) -> bool:
            return True

        async def generate(self, request: GenerationRequest) -> GenerationResult:
            return GenerationResult(
                status=STATUS_OK,
                candidates=[ImageCandidate(data=fixed, claimed_mime="image/png")],
                elapsed_s=0.0,
                provider=self.name,
            )

        async def close(self) -> None:
            return None

    factory, builder, pipeline = env

    async def body():  # noqa: ANN202
        out = []
        for _ in range(2):
            async with factory() as session:
                job, images, _ = await run_generation_job(
                    session,
                    provider=FixedProvider(),
                    pipeline=pipeline,
                    builder=builder,
                    identity=IDENTITY,
                    style_lock=None,
                    count=1,
                    seed=42,
                )
                await session.commit()
                out.append((job, images))
        return out

    (job1, imgs1), (job2, imgs2) = _run(body())
    assert job1.status == "succeeded" and len(imgs1) == 1
    assert len(imgs2) == 0
    assert job2.status == "failed"
    assert job2.error_summary and "duplicate" in job2.error_summary
