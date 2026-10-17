"""Unit tests for WorkerPool, live resize, and concurrent job execution (R34, R35, R38)."""

import asyncio
import os
import shutil
import tempfile
import time
import unittest

from sqlalchemy import select

from waifubot.adapters.providers.mock import MockGenerationProvider, MockSlotPool
from waifubot.db.models import JobRecord
from waifubot.db.session import create_engine_and_sessionmaker, init_db
from waifubot.services.generation import GenerationService
from waifubot.services.settings import SettingsService
from waifubot.services.workers import WorkerPool


class TestWorkerPool(unittest.IsolatedAsyncioTestCase):
    """Test suite covering WorkerPool lifecycle, job processing, and dynamic resize."""

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="waifu_worker_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine, self.session_factory = create_engine_and_sessionmaker(self.db_url)
        await init_db(self.engine)

        self.slot_pool = MockSlotPool(size=4)
        self.provider = MockGenerationProvider(name="mock")
        self.gen_service = GenerationService()
        self.settings = SettingsService(
            {
                "worker_count": 2,
                "max_concurrent_jobs": 2,
                "max_browser_contexts": 4,
                "provider_concurrency": 4,
            }
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_effective_concurrency_calculation(self) -> None:
        """Effective concurrency equals min of all four concurrency knobs (R34)."""
        self.settings.set("worker_count", 8)
        self.settings.set("max_concurrent_jobs", 3)
        self.settings.set("max_browser_contexts", 4)
        self.settings.set("provider_concurrency", 4)
        self.assertEqual(self.settings.effective_concurrency(), 3)

        self.settings.set("max_browser_contexts", 2)
        self.assertEqual(self.settings.effective_concurrency(), 2)

    async def test_worker_pool_executes_pending_jobs(self) -> None:
        """WorkerPool concurrently claims and satisfies pending jobs to completion (R9, R34)."""
        now = time.time()
        async with self.session_factory() as session:
            async with session.begin():
                session.add(
                    JobRecord(
                        job_id="pool-job-1",
                        requester_user_id=101,
                        admin_id=101,
                        prompt="girl 1",
                        target_count=2,
                        needed=2,
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    JobRecord(
                        job_id="pool-job-2",
                        requester_user_id=102,
                        admin_id=102,
                        prompt="girl 2",
                        target_count=2,
                        needed=2,
                        status="pending",
                        created_at=now + 1.0,
                        updated_at=now + 1.0,
                    )
                )

        pool = WorkerPool(
            session_factory=self.session_factory,
            slot_pool=self.slot_pool,
            provider=self.provider,
            generation_service=self.gen_service,
            settings_service=self.settings,
            idle_sleep_seconds=0.05,
        )

        pool.start()
        try:
            # Await completion of both jobs
            deadline = time.time() + 5.0
            done = False
            while time.time() < deadline:
                async with self.session_factory() as session:
                    res = await session.execute(
                        select(JobRecord).where(JobRecord.status == "succeeded")
                    )
                    succeeded = list(res.scalars().all())
                    if len(succeeded) == 2:
                        done = True
                        break
                await asyncio.sleep(0.1)

            self.assertTrue(done, "Jobs did not succeed within deadline")
        finally:
            await pool.stop()

    async def test_live_resize(self) -> None:
        """Changing worker_count setting adjusts active worker tasks without restart (R34)."""
        self.settings.set("worker_count", 2)
        pool = WorkerPool(
            session_factory=self.session_factory,
            slot_pool=self.slot_pool,
            provider=self.provider,
            generation_service=self.gen_service,
            settings_service=self.settings,
            idle_sleep_seconds=0.05,
        )

        pool.start()
        try:
            await asyncio.sleep(0.1)
            self.assertEqual(pool.active_worker_count, 2)

            # Live increase to 4
            self.settings.set("worker_count", 4)
            pool._sync_workers()
            await asyncio.sleep(0.1)
            self.assertEqual(pool.active_worker_count, 4)

            # Live decrease to 1 (surplus workers exit on their next loop)
            self.settings.set("worker_count", 1)
            await asyncio.sleep(0.2)
            self.assertLessEqual(pool.active_worker_count, 2)
        finally:
            await pool.stop()


if __name__ == "__main__":
    unittest.main()
