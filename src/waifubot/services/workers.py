"""Worker pool: manages concurrent workers with live resize, job semaphore, and slot acquisition (R34, R35, R38)."""

import asyncio
import contextlib
import hashlib
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from waifubot.core.models import GenerationJob, ImageAsset, JobStatus
from waifubot.core.ports import GenerationProvider, ImageStore, ProviderSlotPool
from waifubot.db.models import ImageRecord, JobRecord
from waifubot.services.claiming import HeartbeatTask, claim_next_job
from waifubot.services.generation import GenerationService
from waifubot.services.image_pipeline import persist_image_with_provenance
from waifubot.services.quota import refund_reservation, settle_reservation
from waifubot.services.resources import MemoryGuard
from waifubot.services.settings import SettingsService


class WorkerPool:
    """Manages worker tasks with live dynamic resizing and bounded concurrency (R34)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[Any],
        slot_pool: ProviderSlotPool,
        provider: GenerationProvider,
        generation_service: GenerationService,
        settings_service: SettingsService,
        memory_guard: MemoryGuard | None = None,
        idle_sleep_seconds: float = 0.5,
        image_store: ImageStore | None = None,
        telegram_sender: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.slot_pool = slot_pool
        self.provider = provider
        self.generation_service = generation_service
        self.settings = settings_service
        self.memory_guard = memory_guard or MemoryGuard()
        self.idle_sleep_seconds = idle_sleep_seconds
        self.image_store = image_store
        self.telegram_sender = telegram_sender

        self._stop_event = asyncio.Event()
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(int(self.settings.get("max_concurrent_jobs", 2)))
        self._last_served_requester_id: int | None = None
        self._worker_counter = 0

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    @property
    def active_worker_count(self) -> int:
        return len([t for t in self._worker_tasks.values() if not t.done()])

    def start(self) -> None:
        """Starts worker tasks according to current settings."""
        self._stop_event.clear()
        self._sync_workers()

    def _sync_workers(self) -> None:
        """Dynamically adjusts worker task count according to settings.worker_count (live resize)."""
        target_count = int(self.settings.get("worker_count", 2))

        # Clean completed tasks
        self._worker_tasks = {wid: t for wid, t in self._worker_tasks.items() if not t.done()}

        # Spawn new workers if below target
        while len(self._worker_tasks) < target_count:
            self._worker_counter += 1
            worker_id = f"worker-{self._worker_counter}"
            task = asyncio.create_task(self._worker_loop(worker_id))
            self._worker_tasks[worker_id] = task

    async def _worker_loop(self, worker_id: str) -> None:
        """Main loop executed by an individual worker task."""
        while not self._stop_event.is_set():
            # Check for live resize shrinkage: if active workers > target, this worker can exit
            target_count = int(self.settings.get("worker_count", 2))
            if len(self._worker_tasks) > target_count:
                break

            # Memory guard soft gate (R35)
            if not self.memory_guard.can_acquire():
                await asyncio.sleep(self.idle_sleep_seconds)
                continue

            async with self._semaphore:
                # Atomically claim next job
                job_record: JobRecord | None = None
                fair_claim = bool(self.settings.get("fair_claim", True))
                lease_ttl = float(self.settings.get("lease_ttl_seconds", 60.0))

                async with self.session_factory() as session, session.begin():
                    job_record, self._last_served_requester_id = await claim_next_job(
                        session=session,
                        worker_id=worker_id,
                        lease_duration_seconds=lease_ttl,
                        fair_claim=fair_claim,
                        last_served_requester_id=self._last_served_requester_id,
                    )

                if job_record is None:
                    # No eligible jobs -> sleep briefly and re-sync
                    self._sync_workers()
                    await asyncio.sleep(self.idle_sleep_seconds)
                    continue

                # Process claimed job
                with contextlib.suppress(Exception):
                    await self._execute_claimed_job(worker_id, job_record.job_id)
                self._sync_workers()

    async def _execute_claimed_job(self, worker_id: str, job_id: str) -> None:
        """Executes a claimed job under exclusive slot and heartbeat."""
        heartbeat_interval = float(self.settings.get("heartbeat_seconds", 20.0))
        lease_ttl = float(self.settings.get("lease_ttl_seconds", 60.0))

        heartbeat = HeartbeatTask(
            session_factory=self.session_factory,
            job_id=job_id,
            worker_id=worker_id,
            heartbeat_interval_seconds=heartbeat_interval,
            lease_duration_seconds=lease_ttl,
        )
        heartbeat.start()

        try:
            # Read fresh job record
            async with self.session_factory() as session:
                res = await session.execute(select(JobRecord).where(JobRecord.job_id == job_id))
                record = res.scalar_one()
                prompt = record.prompt
                neg_prompt = record.negative_prompt
                needed = record.needed
                job_scope = record.job_scope
                requester_user_id = record.requester_user_id

            # Acquire exclusive slot (R35)
            async with self.slot_pool.acquire() as slot:
                image_bytes_list = await self.provider.generate(
                    prompt=prompt,
                    negative_prompt=neg_prompt,
                    needed_count=needed,
                    slot=slot,
                    job_scope=job_scope,
                )

            # Build ImageAsset batch with sha256 content hashes
            batch_assets = [
                ImageAsset(
                    sha256=hashlib.sha256(b).hexdigest(),
                    content=b,
                    mime_type="image/png",
                )
                for b in image_bytes_list
            ]

            # Gate evaluation in memory
            async with self.session_factory() as session, session.begin():
                res = await session.execute(select(JobRecord).where(JobRecord.job_id == job_id))
                record = res.scalar_one()

                domain_job = GenerationJob(
                    job_id=record.job_id,
                    requester_user_id=record.requester_user_id,
                    prompt=record.prompt,
                    target_count=record.target_count,
                    needed=record.needed,
                    status=JobStatus(record.status),
                    run_no=record.run_no,
                    rounds_done=record.rounds_done,
                    max_rounds=record.max_rounds,
                    zero_progress_streak=record.zero_progress_streak,
                    zero_progress_limit=record.zero_progress_limit,
                    surplus_dropped=record.surplus_dropped,
                )

                evaluated_job = self.generation_service.process_batch(domain_job, batch_assets)

                # Commit gate results back to database row
                record.status = evaluated_job.status.value
                record.needed = evaluated_job.needed
                record.rounds_done = evaluated_job.rounds_done
                record.zero_progress_streak = evaluated_job.zero_progress_streak
                record.surplus_dropped = evaluated_job.surplus_dropped
                record.error_code = (
                    evaluated_job.error_code.value if evaluated_job.error_code else None
                )
                record.lease_owner = None
                record.lease_expires_at = None
                record.updated_at = time.time()

                # Persist kept images if image_store is configured
                if self.image_store:
                    for asset in batch_assets:
                        if asset.content:
                            existing_check = await session.execute(
                                select(ImageRecord.id).where(ImageRecord.image_id == asset.sha256)
                            )
                            if existing_check.scalar_one_or_none() is None:
                                await persist_image_with_provenance(
                                    session=session,
                                    image_store=self.image_store,
                                    data=asset.content,
                                    job_id=job_id,
                                    final_prompt=prompt,
                                    negative_prompt=neg_prompt,
                                    provider=getattr(self.provider, "name", "perchance"),
                                )

                # Settle or refund quota
                if record.status in ("succeeded", "failed"):
                    with contextlib.suppress(Exception):
                        if record.status == "succeeded":
                            await settle_reservation(
                                session=session,
                                user_id=record.requester_user_id,
                                role="user",
                                reserved_amount=record.target_count,
                                actual_amount=len(evaluated_job.kept_images) or record.target_count,
                            )
                        elif record.status == "failed":
                            await refund_reservation(
                                session=session,
                                user_id=record.requester_user_id,
                                role="user",
                                amount=record.target_count,
                            )

            # Telegram photo delivery if sender callback provided
            if self.telegram_sender and batch_assets:
                total_imgs = len(batch_assets)
                for idx, asset in enumerate(batch_assets, start=1):
                    with contextlib.suppress(Exception):
                        await self.telegram_sender(
                            chat_id=requester_user_id,
                            photo=asset.content,
                            caption=f"🎨 تصویر {idx} از {total_imgs}\nپرامپت: {prompt[:100]}",
                        )
        finally:
            await heartbeat.stop()

    async def stop(self) -> None:
        """Signals stop and waits for all active workers to terminate."""
        self._stop_event.set()
        tasks = list(self._worker_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_tasks.clear()
