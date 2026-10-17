"""Unit tests for reaper service, lease expiration, and R13 interruption notices."""

import os
import shutil
import tempfile
import unittest

from sqlalchemy import select

from waifubot.db.models import JobRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.reaper import reap_expired_jobs


class TestReaperService(unittest.IsolatedAsyncioTestCase):
    """Test suite for reaper service (R13, R36)."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_reaper_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_reaper_requeues_and_generates_notice(self) -> None:
        """Expired lease is requeued to retry_wait with attempt increment and R13 notice."""
        now = 1000.0
        async with self.session_factory() as session, session.begin():
            session.add(
                JobRecord(
                    job_id="job-expired-1",
                    requester_user_id=101,
                    admin_id=505,
                    prompt="anime scene",
                    status="running",
                    lease_owner="worker-crashed",
                    lease_expires_at=now - 5.0,  # expired 5 seconds ago
                    attempts_done=0,
                    max_attempts=3,
                    created_at=now - 100.0,
                    updated_at=now - 60.0,
                )
            )

        # Run reaper
        async with self.session_factory() as session, session.begin():
            notices = await reap_expired_jobs(
                session=session,
                now=now,
                retry_backoff_seconds=45.0,
            )

        # Verify notice
        self.assertEqual(len(notices), 1)
        notice = notices[0]
        self.assertEqual(notice.job_id, "job-expired-1")
        self.assertEqual(notice.admin_id, 505)
        self.assertEqual(notice.attempts_done, 1)
        self.assertEqual(notice.max_attempts, 3)
        self.assertEqual(notice.status, "retry_wait")
        self.assertEqual(notice.next_attempt_at, now + 45.0)

        # Verify DB state
        async with self.session_factory() as session:
            res = await session.execute(
                select(JobRecord).where(JobRecord.job_id == "job-expired-1")
            )
            job = res.scalar_one()
            self.assertEqual(job.status, "retry_wait")
            self.assertEqual(job.attempts_done, 1)
            self.assertIsNone(job.lease_owner)
            self.assertIsNone(job.lease_expires_at)
            self.assertEqual(job.next_attempt_at, now + 45.0)

    async def test_reaper_fails_job_when_attempts_exhausted(self) -> None:
        """When attempts_done reaches max_attempts, reaper marks job as failed with lease_expired error."""
        now = 1000.0
        async with self.session_factory() as session, session.begin():
            session.add(
                JobRecord(
                    job_id="job-exhausted",
                    requester_user_id=102,
                    admin_id=506,
                    prompt="anime scene",
                    status="running",
                    lease_owner="worker-dead",
                    lease_expires_at=now - 1.0,
                    attempts_done=2,
                    max_attempts=3,  # next attempt will reach 3
                    created_at=now - 100.0,
                    updated_at=now - 10.0,
                )
            )

        async with self.session_factory() as session, session.begin():
            notices = await reap_expired_jobs(session=session, now=now)

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].status, "failed")
        self.assertEqual(notices[0].attempts_done, 3)

        async with self.session_factory() as session:
            res = await session.execute(
                select(JobRecord).where(JobRecord.job_id == "job-exhausted")
            )
            job = res.scalar_one()
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.error_code, "lease_expired")

    async def test_active_unexpired_jobs_are_not_reaped(self) -> None:
        """Jobs with valid future leases are untouched by the reaper."""
        now = 1000.0
        async with self.session_factory() as session, session.begin():
            session.add(
                JobRecord(
                    job_id="job-active",
                    requester_user_id=101,
                    admin_id=505,
                    prompt="active running job",
                    status="running",
                    lease_owner="worker-live",
                    lease_expires_at=now + 50.0,  # 50s in future
                    attempts_done=0,
                    max_attempts=3,
                    created_at=now - 10.0,
                    updated_at=now,
                )
            )

        async with self.session_factory() as session, session.begin():
            notices = await reap_expired_jobs(session=session, now=now)

        self.assertEqual(len(notices), 0)

        async with self.session_factory() as session:
            res = await session.execute(select(JobRecord).where(JobRecord.job_id == "job-active"))
            job = res.scalar_one()
            self.assertEqual(job.status, "running")
            self.assertEqual(job.attempts_done, 0)
            self.assertEqual(job.lease_owner, "worker-live")


if __name__ == "__main__":
    unittest.main()
