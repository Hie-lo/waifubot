"""The generation worker (SPEC §4/§10): ONE asyncio task, exactly one generation
at a time. Handlers only write jobs; this worker claims and executes them.

- Claim rule: conditional pending->running update; exactly one claimer wins.
- Order: priority ascending, then created time, then ID.
- Retries: transient failures requeue with backoff 30 s / 120 s (max_attempts=2);
  `blocked_by_provider` and page errors are permanent.
- Circuit breaker: 3 consecutive provider failures -> pause + notify admins;
  only a person resumes (/resume).
- The browser restarts after N jobs (default 10) and after any error.
- Disk floor: below the floor, generation pauses and admins are notified.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waifubot.bot.gateway import TelegramGateway
from waifubot.bot.messages import Messages
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.image_provider import ImageProvider
from waifubot.db.models import Image, Job
from waifubot.services.generation import claim_job, execute_job

log = logging.getLogger(__name__)

BREAKER_THRESHOLD = 3
RESTART_AFTER_JOBS = 10


class GenerationWorker:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        provider: ImageProvider,
        pipeline: ImagePipeline,
        gateway: TelegramGateway,
        msgs: Messages,
        admin_ids: tuple[int, ...],
        storage_root: Path,
        disk_floor_bytes: int = 2 * 1024**3,
        poll_interval_s: float = 2.0,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._pipeline = pipeline
        self._gateway = gateway
        self._msgs = msgs
        self._admins = admin_ids
        self._storage_root = storage_root
        self._disk_floor = disk_floor_bytes
        self._poll_s = poll_interval_s
        self._paused = False
        self._stopping = False
        self._task: asyncio.Task | None = None
        self._consecutive_failures = 0
        self._jobs_since_launch = 0

    # ---- control ----------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    async def resume(self) -> None:
        self._paused = False
        self._consecutive_failures = 0
        await self._notify(self._msgs.t("resumed"))

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self, timeout_s: float = 300.0) -> None:
        """Graceful shutdown (SPEC §12): stop claiming; let the active job finish
        or time out; close the browser."""
        self._stopping = True
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_s)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        await self._provider.close()

    async def _notify(self, text: str) -> None:
        for admin_id in self._admins:
            try:
                await self._gateway.send_message(admin_id, text)
            except Exception:  # noqa: BLE001 — never break the worker on notify
                log.warning("notify failed (admin_id=%s)", admin_id)

    # ---- main loop --------------------------------------------------------
    async def _loop(self) -> None:
        while not self._stopping:
            try:
                job = await self._next_due_job()
                if job is None or self._paused:
                    await asyncio.sleep(self._poll_s)
                    continue
                if not self._disk_ok():
                    self._paused = True
                    free_gb = shutil.disk_usage(self._storage_root).free // 1024**3
                    await self._notify(self._msgs.t("disk_low", free=f"{free_gb}GB"))
                    continue
                await self._run_one(job.id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the loop must survive anything
                log.exception("worker loop error")
                await self._close_browser()
                await asyncio.sleep(self._poll_s)

    def _disk_ok(self) -> bool:
        try:
            return shutil.disk_usage(self._storage_root).free >= self._disk_floor
        except OSError:
            return True

    async def _next_due_job(self) -> Job | None:
        now = datetime.now(UTC)
        async with self._sessions() as s:
            return (
                await s.execute(
                    select(Job)
                    .where(
                        Job.status == "pending",
                        (Job.next_attempt_at.is_(None)) | (Job.next_attempt_at <= now),
                    )
                    .order_by(Job.priority.asc(), Job.created_at.asc(), Job.id.asc())
                    .limit(1)
                )
            ).scalars().first()

    async def _run_one(self, job_id: int) -> None:
        async with self._sessions() as s:
            job = await claim_job(s, job_id)
            if job is None:
                await s.commit()
                return
            await s.commit()
        try:
            async with self._sessions() as s:
                job = await s.get(Job, job_id)
                job, images, result = await execute_job(
                    s, provider=self._provider, pipeline=self._pipeline, job=job,
                    allow_retry=True,
                )
                await s.commit()
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", job_id)
            await self._close_browser()
            return

        self._jobs_since_launch += 1
        ok = job.status == "succeeded"
        if ok:
            self._consecutive_failures = 0
            await self._push_previews(job, images)
        elif job.status == "failed":
            self._consecutive_failures += 1
        elif job.status == "pending":
            log.info("job %s requeued (attempt %s)", job.id, job.attempt_count)

        if result is not None and result.status not in ("ok", "partial"):
            await self._close_browser()  # restart the browser after any error

        if self._jobs_since_launch >= RESTART_AFTER_JOBS:
            log.info("browser restart after %s jobs", self._jobs_since_launch)
            await self._close_browser()

        if self._consecutive_failures >= BREAKER_THRESHOLD and not self._paused:
            self._paused = True
            await self._notify(self._msgs.t("breaker_tripped", count=self._consecutive_failures))

    async def _close_browser(self) -> None:
        try:
            await self._provider.close()
        except Exception:  # noqa: BLE001
            pass
        self._jobs_since_launch = 0

    async def _push_previews(self, job: Job, images: list[Image]) -> None:
        """Review inbox push (SPEC §11): each finished image is sent to the admin
        as a preview with Approve / Reject / Save Character buttons."""
        if not images:
            return
        text = self._msgs.t("generation_done", count=len(images))
        for admin_id in self._admins:
            try:
                await self._gateway.send_message(admin_id, text)
                for img in images:
                    path = self._storage_root / img.file_path
                    if not path.is_file():
                        continue
                    caption = self._msgs.t("inbox_item", image_id=img.id, job_id=job.id)
                    buttons = [
                        [
                            (self._msgs.t("review_approve"), f"appr:{img.id}"),
                            (self._msgs.t("review_reject"), f"rej:{img.id}"),
                        ],
                        [(self._msgs.t("save_character"), f"savec:{job.id}")],
                        [(self._msgs.t("nsfw_mark"), f"nsfw:{img.id}")],
                    ]
                    await self._gateway.send_photo(
                        admin_id, path, caption=caption, buttons=buttons
                    )
            except Exception:  # noqa: BLE001
                log.warning("preview push failed (admin_id=%s)", admin_id)

    async def queue_stats(self) -> dict[str, int]:
        async with self._sessions() as s:
            rows = (
                await s.execute(select(Job.status, func.count()).group_by(Job.status))
            ).all()
        return {status: count for status, count in rows}
