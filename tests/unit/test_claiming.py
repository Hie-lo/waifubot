"""Unit tests for atomic claiming, lease acquisition, and fair round-robin scheduling (ADR-0002, R34, R36, R55)."""

import os
import shutil
import tempfile
import time
import unittest

from sqlalchemy import select, text

from waifubot.db.models import JobRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.claiming import claim_next_job, renew_job_lease


class TestClaimingService(unittest.IsolatedAsyncioTestCase):
    """Test suite for atomic claiming service and SQLite WAL pragmas."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_claim_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_sqlite_wal_pragmas(self) -> None:
        """Verify SQLite WAL and busy timeout pragmas are configured (ADR-0005)."""
        async with self.session_factory() as session:
            res_wal = await session.execute(text("PRAGMA journal_mode"))
            wal_mode = res_wal.scalar()
            self.assertEqual(str(wal_mode).lower(), "wal")

            res_timeout = await session.execute(text("PRAGMA busy_timeout"))
            busy_timeout = res_timeout.scalar()
            self.assertEqual(busy_timeout, 5000)

    async def test_claim_single_job(self) -> None:
        """Verify single pending job is claimed with lease attributes."""
        now = 1000.0
        async with self.session_factory() as session:
            async with session.begin():
                job = JobRecord(
                    job_id="job-1",
                    requester_user_id=101,
                    admin_id=101,
                    prompt="anime girl",
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)

        async with self.session_factory() as session:
            async with session.begin():
                claimed, next_req = await claim_next_job(
                    session=session,
                    worker_id="worker-a",
                    lease_duration_seconds=60.0,
                    now=now + 5.0,
                )
                self.assertIsNotNone(claimed)
                assert claimed is not None
                self.assertEqual(claimed.job_id, "job-1")
                self.assertEqual(claimed.status, "running")
                self.assertEqual(claimed.lease_owner, "worker-a")
                self.assertEqual(claimed.lease_expires_at, now + 5.0 + 60.0)
                self.assertEqual(next_req, 101)

        # Claiming again finds nothing eligible
        async with self.session_factory() as session:
            async with session.begin():
                second_claim, _ = await claim_next_job(
                    session=session,
                    worker_id="worker-b",
                    now=now + 6.0,
                )
                self.assertIsNone(second_claim)

    async def test_priority_ordering(self) -> None:
        """Higher queue_priority jobs are claimed before lower priority ones (R55)."""
        now = 1000.0
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    JobRecord(
                        job_id="job-low",
                        requester_user_id=101,
                        admin_id=101,
                        prompt="low priority",
                        status="pending",
                        queue_priority=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    JobRecord(
                        job_id="job-high",
                        requester_user_id=102,
                        admin_id=102,
                        prompt="high priority",
                        status="pending",
                        queue_priority=10,
                        created_at=now + 1.0,
                        updated_at=now + 1.0,
                    )
                )

        async with self.session_factory() as session:
            async with session.begin():
                claimed, _ = await claim_next_job(
                    session=session,
                    worker_id="worker-a",
                    now=now + 5.0,
                )
                self.assertIsNotNone(claimed)
                assert claimed is not None
                self.assertEqual(claimed.job_id, "job-high")

    async def test_fair_claim_round_robin(self) -> None:
        """Verify fair_claim interleaves multiple requesters to avoid starvation (R34, R55)."""
        now = 1000.0
        async with self.session_factory() as session:
            async with session.begin():
                # User 1 queues 2 jobs, User 2 queues 1 job
                session.add(
                    JobRecord(
                        job_id="u1-j1",
                        requester_user_id=1,
                        admin_id=1,
                        prompt="u1 prompt 1",
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    JobRecord(
                        job_id="u1-j2",
                        requester_user_id=1,
                        admin_id=1,
                        prompt="u1 prompt 2",
                        status="pending",
                        created_at=now + 1.0,
                        updated_at=now + 1.0,
                    )
                )
                session.add(
                    JobRecord(
                        job_id="u2-j1",
                        requester_user_id=2,
                        admin_id=2,
                        prompt="u2 prompt 1",
                        status="pending",
                        created_at=now + 2.0,
                        updated_at=now + 2.0,
                    )
                )

        # First claim: picks user 1 (oldest)
        last_req = None
        async with self.session_factory() as session:
            async with session.begin():
                j1, last_req = await claim_next_job(
                    session=session,
                    worker_id="w1",
                    fair_claim=True,
                    now=now + 10.0,
                    last_served_requester_id=last_req,
                )
                self.assertIsNotNone(j1)
                assert j1 is not None
                self.assertEqual(j1.job_id, "u1-j1")
                self.assertEqual(last_req, 1)

        # Second claim: picks user 2 due to round-robin, NOT u1-j2
        async with self.session_factory() as session:
            async with session.begin():
                j2, last_req = await claim_next_job(
                    session=session,
                    worker_id="w2",
                    fair_claim=True,
                    now=now + 11.0,
                    last_served_requester_id=last_req,
                )
                self.assertIsNotNone(j2)
                assert j2 is not None
                self.assertEqual(j2.job_id, "u2-j1")
                self.assertEqual(last_req, 2)

        # Third claim: picks user 1's remaining job
        async with self.session_factory() as session:
            async with session.begin():
                j3, last_req = await claim_next_job(
                    session=session,
                    worker_id="w3",
                    fair_claim=True,
                    now=now + 12.0,
                    last_served_requester_id=last_req,
                )
                self.assertIsNotNone(j3)
                assert j3 is not None
                self.assertEqual(j3.job_id, "u1-j2")

    async def test_retry_wait_eligibility(self) -> None:
        """retry_wait jobs are not claimed until next_attempt_at is reached (R9, R38)."""
        now = 1000.0
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    JobRecord(
                        job_id="job-retry",
                        requester_user_id=101,
                        admin_id=101,
                        prompt="retry job",
                        status="retry_wait",
                        next_attempt_at=now + 20.0,
                        created_at=now,
                        updated_at=now,
                    )
                )

        # Before next_attempt_at: not claimed
        async with self.session_factory() as session:
            async with session.begin():
                claimed, _ = await claim_next_job(
                    session=session,
                    worker_id="worker-a",
                    now=now + 10.0,
                )
                self.assertIsNone(claimed)

        # After next_attempt_at: claimed
        async with self.session_factory() as session:
            async with session.begin():
                claimed, _ = await claim_next_job(
                    session=session,
                    worker_id="worker-a",
                    now=now + 21.0,
                )
                self.assertIsNotNone(claimed)
                assert claimed is not None
                self.assertEqual(claimed.job_id, "job-retry")
                self.assertEqual(claimed.status, "running")

    async def test_renew_job_lease(self) -> None:
        """Verify lease renewal succeeds for owner and fails for others."""
        now = 1000.0
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    JobRecord(
                        job_id="job-running",
                        requester_user_id=101,
                        admin_id=101,
                        prompt="running",
                        status="running",
                        lease_owner="worker-owner",
                        lease_expires_at=now + 30.0,
                        created_at=now,
                        updated_at=now,
                    )
                )

        # Wrong worker fails to renew
        async with self.session_factory() as session:
            async with session.begin():
                renewed = await renew_job_lease(
                    session=session,
                    job_id="job-running",
                    worker_id="worker-wrong",
                    lease_duration_seconds=60.0,
                    now=now + 10.0,
                )
                self.assertFalse(renewed)

        # Correct worker successfully renews
        async with self.session_factory() as session:
            async with session.begin():
                renewed = await renew_job_lease(
                    session=session,
                    job_id="job-running",
                    worker_id="worker-owner",
                    lease_duration_seconds=60.0,
                    now=now + 10.0,
                )
                self.assertTrue(renewed)

        # Check DB reflects updated expiration
        async with self.session_factory() as session:
            res = await session.execute(
                select(JobRecord).where(JobRecord.job_id == "job-running")
            )
            job = res.scalar_one()
            self.assertEqual(job.lease_expires_at, now + 10.0 + 60.0)


if __name__ == "__main__":
    unittest.main()
