"""Reaper service: cleans up expired leases and announces interruptions (R13, R36)."""

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import JobRecord


@dataclass
class InterruptedNotice:
    """Interruption announcement notification payload (R13)."""

    job_id: str
    admin_id: int
    requester_user_id: int
    attempts_done: int
    max_attempts: int
    status: str
    reaped_at: float
    next_attempt_at: float | None = None


async def reap_expired_jobs(
    session: AsyncSession,
    now: float | None = None,
    retry_backoff_seconds: float = 30.0,
) -> list[InterruptedNotice]:
    """Scans for running jobs whose lease expired, requeues or fails them, and produces R13 notices."""
    current_time = now if now is not None else time.time()

    query = select(JobRecord).where(
        and_(
            JobRecord.status == "running",
            JobRecord.lease_expires_at.is_not(None),
            JobRecord.lease_expires_at < current_time,
        )
    )

    result = await session.execute(query)
    expired_jobs = list(result.scalars().all())
    notices: list[InterruptedNotice] = []

    for job in expired_jobs:
        job.attempts_done += 1
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = current_time

        if job.attempts_done >= job.max_attempts:
            job.status = "failed"
            job.error_code = "lease_expired"
            job.next_attempt_at = None
        else:
            job.status = "retry_wait"
            job.next_attempt_at = current_time + retry_backoff_seconds

        notice = InterruptedNotice(
            job_id=job.job_id,
            admin_id=job.admin_id,
            requester_user_id=job.requester_user_id,
            attempts_done=job.attempts_done,
            max_attempts=job.max_attempts,
            status=job.status,
            reaped_at=current_time,
            next_attempt_at=job.next_attempt_at,
        )
        notices.append(notice)

    if expired_jobs:
        await session.flush()

    return notices


class ReaperLoop:
    """Periodic background loop checking for expired leases."""

    def __init__(
        self,
        session_factory: Any,
        check_interval_seconds: float = 10.0,
        retry_backoff_seconds: float = 30.0,
        on_interrupted_notice: Callable[[InterruptedNotice], Any] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.check_interval = check_interval_seconds
        self.retry_backoff = retry_backoff_seconds
        self.on_interrupted_notice = on_interrupted_notice
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                async with self.session_factory() as session, session.begin():
                    notices = await reap_expired_jobs(
                        session=session,
                        retry_backoff_seconds=self.retry_backoff,
                    )
                    if self.on_interrupted_notice:
                        for notice in notices:
                            try:
                                res = self.on_interrupted_notice(notice)
                                if asyncio.iscoroutine(res):
                                    await res
                            except Exception:
                                pass
            except asyncio.CancelledError:
                break
            except Exception:
                pass
