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


def test_partial_never_silently_undercounts(env) -> None:  # noqa: ANN001
    """EXACT-count rule (user 2026-10-16): a short batch is not 'success'.
    The CLI one-shot never retries -> failed/partial_count, images kept."""
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "partial")
    assert job.status == "failed"
    assert job.error_code == "partial_count"
    assert len(images) == 2  # the good images are kept (never delete work)


def test_malformed_candidates_filtered(env) -> None:  # noqa: ANN001
    """Corrupt candidates shrinking the batch below the requested count is
    an UNDERCOUNT (exact-count rule) — kept images, visible failure."""
    factory, builder, pipeline = env
    job, images, _ = _generate(factory, builder, pipeline, "malformed")
    assert job.status == "failed"
    assert job.error_code == "partial_count"
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


def test_actionable_error_code_is_preserved(env) -> None:  # noqa: ANN001
    # 2026-10-16 headless test: 'selector_timeout' surfaced as generic provider_error.
    from waifubot.core.image_provider import (
        STATUS_ERROR,
        GenerationRequest,
        GenerationResult,
    )

    class FailingProvider:
        name = "failing"

        async def health_check(self) -> bool:
            return True

        async def generate(self, request: GenerationRequest) -> GenerationResult:
            return GenerationResult(
                status=STATUS_ERROR,
                candidates=[],
                elapsed_s=0.1,
                provider=self.name,
                error_code="selector_timeout",
                error_summary="prompt textarea not visible",
            )

        async def close(self) -> None:
            return None

    factory, builder, pipeline = env

    async def body():  # noqa: ANN202
        async with factory() as session:
            job, images, result = await run_generation_job(
                session,
                provider=FailingProvider(),
                pipeline=pipeline,
                builder=builder,
                identity=IDENTITY,
                style_lock=None,
                count=1,
                seed=42,
            )
            await session.commit()
            return job, images, result

    job, images, result = _run(body())
    assert job.status == "failed"
    assert job.error_code == "selector_timeout"  # actionable code kept
    assert images == []


def test_undercount_requeue_tops_up_to_exact_count(env) -> None:  # noqa: ANN001
    """A retry does NOT run a second full batch: it generates only the
    MISSING images so the job ends with EXACTLY the requested count."""
    from sqlalchemy import select as sa_select

    from waifubot.db.models import Image, Job
    from waifubot.services.generation import create_job, execute_job

    factory, builder, pipeline = env

    class UniqueMock(MockProvider):
        """Like MockProvider but every call makes fresh bytes (like the real
        site) so cross-attempt dedupe does not eat the top-up batch."""

        async def generate(self, request):  # noqa: ANN001, ANN202
            self._calls = getattr(self, "_calls", 0) + 1
            meta = dict(request.metadata or {})
            meta["seed"] = str(1000 * self._calls)
            from dataclasses import replace as dc_replace

            return await super().generate(dc_replace(request, metadata=meta))

    async def body() -> None:  # noqa: ANN202
        async with factory() as session:
            job = await create_job(
                session, builder=builder, identity=IDENTITY, style_lock=None,
                count=4, seed=1, idempotency_key="topup1",
            )
            await session.commit()
            job_id = job.id
        provider = UniqueMock({"mode": "partial", "partial_count": 2})

        # attempt 1: 2 of 4 -> requeued (pending), the 2 images stay
        async with factory() as session:
            job = await session.get(Job, job_id)
            job, imgs1, _ = await execute_job(
                session, provider=provider, pipeline=pipeline, job=job,
                allow_retry=True,
            )
            await session.commit()
        assert job.status == "pending" and job.error_code == "partial_count"
        assert len(imgs1) == 2

        # attempt 2: only the MISSING 2 are requested -> EXACTLY 4 total
        async with factory() as session:
            job = await session.get(Job, job_id)
            job, imgs2, _ = await execute_job(
                session, provider=provider, pipeline=pipeline, job=job,
                allow_retry=True,
            )
            await session.commit()
        assert job.status == "succeeded"
        assert len(imgs2) == 2  # top-up requested 2, not 4
        async with factory() as session:
            rows = (await session.execute(
                sa_select(Image).where(Image.job_id == job_id)
            )).scalars().all()
        assert len(rows) == 4  # EXACTLY the requested count

    _run(body())


def test_duplicate_bytes_shrinking_batch_is_undercount(env) -> None:  # noqa: ANN001
    """Dedupe may never deliver fewer than the requested count silently —
    identical bytes collapse to 1 image -> undercount gate fires."""
    from io import BytesIO

    from PIL import Image as PILImage

    from waifubot.core.image_pipeline import ImageCandidate
    from waifubot.core.image_provider import GenerationRequest, GenerationResult

    factory, builder, pipeline = env

    buf = BytesIO()
    PILImage.new("RGB", (256, 256), (10, 20, 30)).save(buf, format="PNG")
    same = buf.getvalue()

    class DupProvider:
        name = "dup"

        async def health_check(self) -> bool:
            return True

        async def generate(self, request: GenerationRequest) -> GenerationResult:
            cand = ImageCandidate(
                data=same, claimed_mime="image/png", source="dup"
            )
            return GenerationResult(
                status="ok", candidates=[cand] * request.count, elapsed_s=0.0,
                provider="dup",
            )

    async def body() -> None:  # noqa: ANN202
        async with factory() as session:
            job, images, _ = await run_generation_job(
                session,
                provider=DupProvider(),
                pipeline=pipeline,
                builder=builder,
                identity=IDENTITY,
                style_lock=None,
                count=4,
                seed=7,
            )
            await session.commit()
            return job, images

    job, images = _run(body())
    assert job.status == "failed"
    assert job.error_code == "partial_count"  # 1 unique of 4 requested
    assert len(images) == 1  # the one unique image is kept

