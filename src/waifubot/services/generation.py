"""Generation service: one job end-to-end (SPEC §10 lifecycle).

Transitions (validated here): pending → running → succeeded | failed.
A job that produced at least one image is `succeeded` (review state lives on
`images`). `blocked_by_provider` is permanent — never retried.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

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

_MAX_SUMMARY = 200


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
) -> tuple[Job, list[Image], GenerationResult | None]:
    """Build the prompt, create the job row, generate, validate, and store.

    Returns (job, image rows, provider result). The caller commits.
    """
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

    stored, rejected = pipeline.process(result.candidates)
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
