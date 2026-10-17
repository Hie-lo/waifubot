"""Unit tests for graceful shutdown and lease release without consuming attempts (R37)."""

import os
import shutil
import tempfile
import unittest

from sqlalchemy import select

from waifubot.db.models import JobRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.shutdown import GracefulShutdownCoordinator, release_running_leases


class TestShutdownService(unittest.IsolatedAsyncioTestCase):
    """Test suite for graceful shutdown (R37)."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_shutdown_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_release_leases_without_consuming_attempts(self) -> None:
        """Graceful shutdown resets running jobs to pending without incrementing attempts_done (R37)."""
        now = 1000.0
        async with self.session_factory() as session, session.begin():
            session.add(
                JobRecord(
                    job_id="job-interrupted-shutdown",
                    requester_user_id=101,
                    admin_id=505,
                    prompt="anime scene",
                    status="running",
                    lease_owner="worker-sigterm",
                    lease_expires_at=now + 55.0,
                    attempts_done=1,  # previously had 1 attempt
                    max_attempts=3,
                    created_at=now - 20.0,
                    updated_at=now,
                )
            )

        # Release leases on shutdown
        async with self.session_factory() as session, session.begin():
            released = await release_running_leases(session=session, now=now + 5.0)

        self.assertEqual(released, ["job-interrupted-shutdown"])

        # Check DB: status is pending, attempts_done is STILL 1
        async with self.session_factory() as session:
            res = await session.execute(
                select(JobRecord).where(JobRecord.job_id == "job-interrupted-shutdown")
            )
            job = res.scalar_one()
            self.assertEqual(job.status, "pending")
            self.assertEqual(
                job.attempts_done, 1, "R37 invariant violated: attempts_done was consumed!"
            )
            self.assertIsNone(job.lease_owner)
            self.assertIsNone(job.lease_expires_at)

    async def test_shutdown_coordinator(self) -> None:
        """Verify GracefulShutdownCoordinator signals stop and releases database leases."""
        coordinator = GracefulShutdownCoordinator(
            session_factory=self.session_factory,
            grace_period_seconds=1.0,
        )
        self.assertFalse(coordinator.is_stopping)
        coordinator.request_stop()
        self.assertTrue(coordinator.is_stopping)

        now = 1000.0
        async with self.session_factory() as session, session.begin():
            session.add(
                JobRecord(
                    job_id="job-coord",
                    requester_user_id=101,
                    admin_id=505,
                    prompt="anime scene",
                    status="running",
                    lease_owner="worker-1",
                    lease_expires_at=now + 60.0,
                    attempts_done=0,
                    max_attempts=3,
                    created_at=now,
                    updated_at=now,
                )
            )

        released = await coordinator.execute_shutdown(active_jobs_checker=lambda: False)
        self.assertEqual(released, ["job-coord"])


if __name__ == "__main__":
    unittest.main()
