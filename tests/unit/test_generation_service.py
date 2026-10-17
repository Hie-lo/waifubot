"""Unit tests for generation service and exact-count gate (R9, R38)."""

import unittest

from waifubot.core.models import GenerationJob, ImageAsset, JobErrorCode, JobStatus
from waifubot.services.generation import manual_retry_job, process_job_batch


class TestGenerationService(unittest.TestCase):
    def test_exact_count_gate_succeeded_on_exact_match(self):
        """Job succeeds only when kept count exactly equals target (R9)."""
        job = GenerationJob(job_id="j1", requester_user_id=1, prompt="test", target_count=4)
        batch = [
            ImageAsset(content=b"1", sha256="h1"),
            ImageAsset(content=b"2", sha256="h2"),
            ImageAsset(content=b"3", sha256="h3"),
            ImageAsset(content=b"4", sha256="h4"),
        ]
        result = process_job_batch(job, batch)
        self.assertEqual(result.status, JobStatus.SUCCEEDED)
        self.assertEqual(len(result.kept_images), 4)
        self.assertEqual(result.needed, 0)
        self.assertEqual(result.surplus_dropped, 0)

    def test_exact_count_gate_truncates_surplus(self):
        """Surplus images beyond target count are dropped and recorded (R9)."""
        job = GenerationJob(job_id="j2", requester_user_id=1, prompt="test", target_count=2)
        batch = [
            ImageAsset(content=b"1", sha256="h1"),
            ImageAsset(content=b"2", sha256="h2"),
            ImageAsset(content=b"3", sha256="h3"),
        ]
        result = process_job_batch(job, batch)
        self.assertEqual(result.status, JobStatus.SUCCEEDED)
        self.assertEqual(len(result.kept_images), 2)
        self.assertEqual(result.surplus_dropped, 1)

    def test_undercount_triggers_retry_wait_topup(self):
        """Undercount requests only missing images (needed = N - kept) (R9, R38)."""
        job = GenerationJob(job_id="j3", requester_user_id=1, prompt="test", target_count=4)
        batch = [
            ImageAsset(content=b"1", sha256="h1"),
            ImageAsset(content=b"2", sha256="h2"),
        ]
        result = process_job_batch(job, batch)
        self.assertEqual(result.status, JobStatus.RETRY_WAIT)
        self.assertEqual(len(result.kept_images), 2)
        self.assertEqual(result.needed, 2)
        self.assertEqual(result.rounds_done, 1)
        self.assertEqual(result.zero_progress_streak, 0)

    def test_zero_progress_streak_fails_when_exhausted(self):
        """Zero progress consecutive streak stops job when reaching limit (R38)."""
        job = GenerationJob(
            job_id="j4",
            requester_user_id=1,
            prompt="test",
            target_count=4,
            zero_progress_limit=2,
        )
        # Round 1: adds 2 images
        process_job_batch(
            job, [ImageAsset(content=b"1", sha256="h1"), ImageAsset(content=b"2", sha256="h2")]
        )
        self.assertEqual(job.zero_progress_streak, 0)

        # Round 2: zero new images
        process_job_batch(job, [])
        self.assertEqual(job.zero_progress_streak, 1)
        self.assertEqual(job.status, JobStatus.RETRY_WAIT)

        # Round 3: zero new images again -> hits zero_progress_limit
        process_job_batch(job, [])
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.error_code, JobErrorCode.PARTIAL_COUNT)

    def test_manual_retry_resets_streaks_and_increments_run_no(self):
        """Manual retry starts fresh run (R38)."""
        job = GenerationJob(job_id="j5", requester_user_id=1, prompt="test", target_count=4)
        job.rounds_done = 3
        job.zero_progress_streak = 3
        job.status = JobStatus.FAILED

        retried = manual_retry_job(job)
        self.assertEqual(retried.status, JobStatus.PENDING)
        self.assertEqual(retried.run_no, 2)
        self.assertEqual(retried.rounds_done, 0)
        self.assertEqual(retried.zero_progress_streak, 0)


if __name__ == "__main__":
    unittest.main()
