"""Graceful shutdown service: stops claiming, awaits grace period, and safely releases leases (R37)."""

import asyncio
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import JobRecord


async def release_running_leases(
    session: AsyncSession,
    now: float | None = None,
    worker_id: str | None = None,
) -> list[str]:
    """Releases active leases back to 'pending' WITHOUT consuming an attempt (R37)."""
    current_time = now if now is not None else time.time()

    query = select(JobRecord).where(JobRecord.status == "running")
    if worker_id is not None:
        query = query.where(JobRecord.lease_owner == worker_id)

    result = await session.execute(query)
    running_jobs = list(result.scalars().all())
    released_ids: list[str] = []

    for job in running_jobs:
        # R37 invariant: attempts_done is NOT consumed on graceful shutdown
        job.status = "pending"
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = current_time
        released_ids.append(job.job_id)

    if running_jobs:
        await session.flush()

    return released_ids


class GracefulShutdownCoordinator:
    """Coordinates graceful shutdown across workers and database leases (R37)."""

    def __init__(
        self,
        session_factory: Any,
        grace_period_seconds: float = 30.0,
    ) -> None:
        self.session_factory = session_factory
        self.grace_period_seconds = grace_period_seconds
        self.stop_requested = asyncio.Event()

    def request_stop(self) -> None:
        """Signals all workers to stop claiming new jobs."""
        self.stop_requested.set()

    @property
    def is_stopping(self) -> bool:
        return self.stop_requested.is_set()

    async def execute_shutdown(
        self,
        active_jobs_checker: Callable[[], bool] | None = None,
    ) -> list[str]:
        """Awaits active jobs within grace period, then releases any remaining leases to pending."""
        self.request_stop()

        # Await running jobs up to grace period
        if active_jobs_checker is not None:
            deadline = time.time() + self.grace_period_seconds
            while time.time() < deadline:
                if not active_jobs_checker():
                    break
                await asyncio.sleep(0.5)

        # Release any uncompleted leases back to pending without consuming attempts
        async with self.session_factory() as session, session.begin():
            return await release_running_leases(session)
