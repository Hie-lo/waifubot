"""Generation service: durable job lifecycle (SPEC §10).

Transitions (validated here):
  create_job   -> pending
  claim_job    -> running (conditional update; exactly one claimer wins)
  execute_job  -> succeeded | failed, or back to pending for a bounded retry
A job that produced at least one image is `succeeded` (review state lives on
`images`). `blocked_by_provider`, `selector_timeout` and `page_error` are
permanent — never retried. Transient failures retry at most `max_attempts`
with backoff 30 s then 120 s (SPEC §10).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func as sa_func
from sqlalchemy import select as sa_select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.config import HARD_MAX_IMAGES_PER_JOB, HARD_MAX_JOBS_PER_DAY
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.image_provider import (
    ERROR_BLOCKED,
    ERROR_PROVIDER,
    ERROR_TIMEOUT,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_TIMEOUT,
    GenerationRequest,
    GenerationResult,
    ImageProvider,
)
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.db.models import Image, Job
from waifubot.errors import ValidationError

_MAX_SUMMARY = 200
# Transient provider failures may retry; everything else is permanent (SPEC §10).
TRANSIENT_ERROR_CODES = (ERROR_TIMEOUT, ERROR_PROVIDER)
BACKOFF_S = (30, 120)


def check_generation_limits(
    *,
    count: int,
    jobs_today: int,
    max_images_per_job: int = HARD_MAX_IMAGES_PER_JOB,
    max_jobs_per_day: int = HARD_MAX_JOBS_PER_DAY,
) -> None:
    """Hard limit enforcement (SPEC §3). Found missing in review 2026-10-16:
    nothing enforced these caps in the job path before."""
    if not 1 <= count <= max_images_per_job:
        raise ValidationError(f"count must be between 1 and {max_images_per_job}")
    if jobs_today >= max_jobs_per_day:
        raise ValidationError(
            f"daily job cap reached ({max_jobs_per_day} jobs) — try again tomorrow"
        )


def _summary(text: str | None) -> str | None:
    if not text:
        return None
    return text[:_MAX_SUMMARY]


def _now() -> datetime:
    return datetime.now(UTC)


async def create_job(
    session: AsyncSession,
    *,
    builder: PromptBuilder,
    identity: str,
    style_lock: str | None,
    count: int,
    seed: int | None = None,
    admin_text: str = "",
    style_value: str = "",
    timeout_s: float = 240.0,
    character_id: int | None = None,
    idempotency_key: str | None = None,
    requested_by: str = "cli",
    max_images_per_job: int = HARD_MAX_IMAGES_PER_JOB,
    max_jobs_per_day: int = HARD_MAX_JOBS_PER_DAY,
    priority: int = 1,
) -> Job:
    """Build the prompt and create a durable `pending` job row (the worker or the
    CLI claims and executes it). The request key is unique — a double tap from
    Telegram cannot create two jobs (SPEC §10 idempotency)."""
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    jobs_today = (
        await session.execute(
            sa_select(sa_func.count()).select_from(Job).where(Job.created_at >= today_start)
        )
    ).scalar_one()
    check_generation_limits(
        count=count,
        jobs_today=jobs_today,
        max_images_per_job=min(max_images_per_job, HARD_MAX_IMAGES_PER_JOB),
        max_jobs_per_day=min(max_jobs_per_day, HARD_MAX_JOBS_PER_DAY),
    )

    prompt_result = builder.build(
        identity=identity, style_lock=style_lock, seed=seed, admin_text=admin_text
    )
    job = Job(
        status="pending",
        priority=priority,
        filters_json=json.dumps(
            {
                "seed": prompt_result.seed,
                "preset_ids": list(prompt_result.preset_ids),
                "count": count,
                "admin_text": admin_text[:200],
                "identity": identity[:2000],
                "style_value": style_value,
                "character_id": character_id,
                "timeout_s": timeout_s,
            },
            ensure_ascii=False,
        ),
        prompt_snapshot=prompt_result.prompt,
        template_version=prompt_result.template_version,
        character_id=character_id,
        requested_by=requested_by,
        idempotency_key=idempotency_key or f"cli:{uuid.uuid4().hex}",
        attempt_count=0,
        max_attempts=2,
    )
    session.add(job)
    await session.flush()
    return job


async def claim_job(session: AsyncSession, job_id: int) -> Job | None:
    """Claim rule (SPEC §10): conditional update sets `running` only where the
    job is still `pending`; proceed only if exactly one row changed."""
    result = await session.execute(
        sa_update(Job)
        .where(Job.id == job_id, Job.status == "pending")
        .values(status="running", started_at=_now(), attempt_count=Job.attempt_count + 1)
    )
    if result.rowcount != 1:
        return None
    return await session.get(Job, job_id)


async def execute_job(
    session: AsyncSession,
    *,
    provider: ImageProvider,
    pipeline: ImagePipeline,
    job: Job,
    allow_retry: bool = True,
) -> tuple[Job, list[Image], GenerationResult | None]:
    """Run a claimed (running) job: generate, validate, store, transition.

    Transient provider failures go back to `pending` with a backoff while
    attempts remain (only when ``allow_retry`` — the CLI one-shot never retries).
    """
    filters = json.loads(job.filters_json or "{}")
    count = int(filters.get("count") or 1)
    result = await provider.generate(
        GenerationRequest(
            prompt=job.prompt_snapshot,
            count=count,
            style_value=str(filters.get("style_value") or ""),
            timeout_s=float(filters.get("timeout_s") or 240.0),
            job_id=job.id,
            metadata={"seed": str(filters.get("seed") or "")},
        )
    )

    if result.status in (STATUS_BLOCKED, STATUS_ERROR, STATUS_TIMEOUT):
        code = result.error_code or {
            STATUS_BLOCKED: ERROR_BLOCKED,
            STATUS_TIMEOUT: ERROR_TIMEOUT,
        }.get(result.status, ERROR_PROVIDER)
        job.error_summary = _summary(result.error_summary)
        transient = allow_retry and code in TRANSIENT_ERROR_CODES
        if transient and job.attempt_count < job.max_attempts:
            backoff = BACKOFF_S[job.attempt_count - 1] if job.attempt_count - 1 < len(
                BACKOFF_S
            ) else BACKOFF_S[-1]
            job.status = "pending"
            job.next_attempt_at = _now() + timedelta(seconds=backoff)
            job.error_code = code
        else:
            job.status = "failed"
            job.error_code = code  # permanent codes (blocked) never retry (SPEC §10)
        job.finished_at = _now()
        await session.flush()
        return job, [], result

    # Cross-job SHA-256 dedupe (SPEC §3.3): one stored copy per digest.
    existing_shas = set((await session.execute(sa_select(Image.sha256))).scalars().all())
    stored, rejected = pipeline.process(result.candidates, seen_sha256=existing_shas)
    image_rows: list[Image] = []
    for item in stored:
        row = Image(
            job_id=job.id,
            character_id=filters.get("character_id"),
            provider=result.provider,
            file_path=item.file_path,
            mime_type=item.mime_type,
            width=item.width,
            height=item.height,
            file_size=item.file_size,
            sha256=item.sha256,
            status="pending_review",
            seed=item.seed,
        )
        session.add(row)
        image_rows.append(row)

    if image_rows:
        job.status = "succeeded"
    else:
        job.status = "failed"
        job.error_code = "no_valid_images"
        job.error_summary = _summary(f"rejected: {','.join(sorted(set(rejected)))}")
    job.finished_at = _now()
    await session.flush()
    return job, image_rows, result


async def run_generation_job(
    session: AsyncSession,
    *,
    provider: ImageProvider,
    pipeline: ImagePipeline,
    builder: PromptBuilder,
    identity: str,
    style_lock: str | None,
    count: int,
    seed: int | None = None,
    admin_text: str = "",
    timeout_s: float = 240.0,
    style_value: str = "",
    idempotency_key: str | None = None,
    requested_by: str = "cli",
    max_images_per_job: int = HARD_MAX_IMAGES_PER_JOB,
    max_jobs_per_day: int = HARD_MAX_JOBS_PER_DAY,
) -> tuple[Job, list[Image], GenerationResult | None]:
    """One-shot end-to-end (CLI): create + claim + execute, no retries."""
    job = await create_job(
        session,
        builder=builder,
        identity=identity,
        style_lock=style_lock,
        count=count,
        seed=seed,
        admin_text=admin_text,
        style_value=style_value,
        timeout_s=timeout_s,
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        max_images_per_job=max_images_per_job,
        max_jobs_per_day=max_jobs_per_day,
    )
    claimed = await claim_job(session, job.id)
    assert claimed is not None  # fresh job must be claimable
    return await execute_job(
        session, provider=provider, pipeline=pipeline, job=claimed, allow_retry=False
    )
