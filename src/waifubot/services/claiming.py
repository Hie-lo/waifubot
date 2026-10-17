"""Atomic claiming service with durable lease and fair round-robin scheduling (ADR-0002, R34, R36, R55)."""

import asyncio
import random
import time
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import JobRecord


async def claim_next_job(
    session: AsyncSession,
    worker_id: str,
    lease_duration_seconds: float = 60.0,
    fair_claim: bool = True,
    now: float | None = None,
    last_served_requester_id: int | None = None,
) -> tuple[JobRecord | None, int | None]:
    """Atomically claims the next eligible job with lease under BEGIN IMMEDIATE (R34, R36).

    Returns:
        (claimed_job, new_last_served_requester_id)
    """
    current_time = now if now is not None else time.time()

    # Query eligible jobs:
    # 1. status == 'pending'
    # 2. status == 'retry_wait' AND (next_attempt_at IS NULL OR next_attempt_at <= current_time)
    eligibility_filter = or_(
        JobRecord.status == "pending",
        and_(
            JobRecord.status == "retry_wait",
            or_(
                JobRecord.next_attempt_at.is_(None),
                JobRecord.next_attempt_at <= current_time,
            ),
        ),
    )

    query = (
        select(JobRecord)
        .where(eligibility_filter)
        .order_by(
            JobRecord.queue_priority.desc(),
            JobRecord.created_at.asc(),
        )
    )

    result = await session.execute(query)
    candidates = list(result.scalars().all())

    if not candidates:
        return None, last_served_requester_id

    # If fair_claim is active, interleave requesters round-robin
    chosen_job: JobRecord | None = None
    if fair_claim and len(candidates) > 1:
        # Group highest priority candidates
        top_priority = candidates[0].queue_priority
        top_candidates = [c for c in candidates if c.queue_priority == top_priority]

        # Distinct requesters at this priority in arrival order
        requester_ids: list[int] = []
        for c in top_candidates:
            if c.requester_user_id not in requester_ids:
                requester_ids.append(c.requester_user_id)

        # Pick the next requester after last_served_requester_id
        target_requester = requester_ids[0]
        if last_served_requester_id in requester_ids:
            idx = requester_ids.index(last_served_requester_id)
            next_idx = (idx + 1) % len(requester_ids)
            target_requester = requester_ids[next_idx]

        # Select the oldest job belonging to that target requester
        for c in top_candidates:
            if c.requester_user_id == target_requester:
                chosen_job = c
                break

    if chosen_job is None:
        chosen_job = candidates[0]

    # Atomically lock and update the claimed job
    chosen_job.status = "running"
    chosen_job.lease_owner = worker_id
    chosen_job.lease_expires_at = current_time + lease_duration_seconds
    chosen_job.updated_at = current_time

    await session.flush()
    return chosen_job, chosen_job.requester_user_id


async def renew_job_lease(
    session: AsyncSession,
    job_id: str,
    worker_id: str,
    lease_duration_seconds: float = 60.0,
    now: float | None = None,
) -> bool:
    """Renews the lease for a running job if still owned by worker_id."""
    current_time = now if now is not None else time.time()
    new_expires_at = current_time + lease_duration_seconds

    stmt = (
        update(JobRecord)
        .where(
            and_(
                JobRecord.job_id == job_id,
                JobRecord.lease_owner == worker_id,
                JobRecord.status == "running",
            )
        )
        .values(
            lease_expires_at=new_expires_at,
            updated_at=current_time,
        )
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


class HeartbeatTask:
    """Background task renewing job lease with ±20% jitter (ADR-0002, ADR-0005)."""

    def __init__(
        self,
        session_factory: Any,
        job_id: str,
        worker_id: str,
        heartbeat_interval_seconds: float = 20.0,
        lease_duration_seconds: float = 60.0,
    ) -> None:
        self.session_factory = session_factory
        self.job_id = job_id
        self.worker_id = worker_id
        self.base_interval = heartbeat_interval_seconds
        self.lease_duration = lease_duration_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while self._running:
            # ±20% jitter
            jitter = random.uniform(0.8, 1.2)
            delay = self.base_interval * jitter
            try:
                await asyncio.sleep(delay)
                async with self.session_factory() as session:
                    async with session.begin():
                        renewed = await renew_job_lease(
                            session=session,
                            job_id=self.job_id,
                            worker_id=self.worker_id,
                            lease_duration_seconds=self.lease_duration,
                        )
                        if not renewed:
                            # Lost lease or job status changed
                            break
            except asyncio.CancelledError:
                break
            except Exception:
                # Log or tolerate transient connection retry
                pass
