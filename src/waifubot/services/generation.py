"""Generation service: one job end-to-end (SPEC §10 lifecycle).

Transitions (validated here): pending → running → succeeded | failed.
A job that produced at least one image is `succeeded` (review state lives on
`images`). `blocked_by_provider` is permanent — never retried.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func as sa_func
from sqlalchemy import select as sa_select
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
    """Build the prompt, create the job row, generate, validate, and store.

    Returns (job, image rows, provider result). The caller commits.
    """
    # Hard limits first (SPEC §3): count bounds + daily cap (UTC day).
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
        priority=1,  # manual jobs (SPEC §10)
        filters_json=json.dumps(
            {
                "seed": prompt_result.seed,
                "preset_ids": list(prompt_result.preset_ids),
                "count": count,
                "admin_text": admin_text[:200],
            },
            ensure_ascii=False,
        ),
        prompt_snapshot=prompt_result.prompt,
        template_version=prompt_result.template_version,
        requested_by=requested_by,
        idempotency_key=idempotency_key or f"cli:{uuid.uuid4().hex}",
        attempt_count=1,
        max_attempts=2,
    )
    session.add(job)
    await session.flush()

    job.status = "running"
    job.started_at = _now()

    result = await provider.generate(
        GenerationRequest(
            prompt=prompt_result.prompt,
            count=count,
            style_value=style_value,
            timeout_s=timeout_s,
            job_id=job.id,
            metadata={"seed": str(prompt_result.seed)},
        )
    )

    if result.status in (STATUS_BLOCKED, STATUS_ERROR, STATUS_TIMEOUT):
        job.status = "failed"
        job.finished_at = _now()
        if result.status == STATUS_BLOCKED:
            job.error_code = ERROR_BLOCKED  # permanent — never retried (SPEC §10)
        elif result.status == STATUS_TIMEOUT:
            job.error_code = ERROR_TIMEOUT
        else:
            job.error_code = ERROR_PROVIDER
        job.error_summary = _summary(result.error_summary)
        return job, [], result

    # Cross-job SHA-256 dedupe (SPEC §3.3): one stored copy per digest.
    existing_shas = set(
        (await session.execute(sa_select(Image.sha256))).scalars().all()
    )
    stored, rejected = pipeline.process(result.candidates, seen_sha256=existing_shas)
    image_rows: list[Image] = []
    for item in stored:
        row = Image(
            job_id=job.id,
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
