"""Hypothesis property-based tests for the exact-count gate and surplus accounting (R9, R38)."""

import hashlib
import unittest

try:
    from hypothesis import given
    from hypothesis import strategies as st
except ImportError:
    # Graceful fallback decorator for environments without hypothesis installed
    def given(*d_args, **d_kwargs):
        def decorator(f):
            def wrapper(self):
                for target_count in [2, 4, 8, 16]:
                    for batch_size in [0, 1, target_count, target_count + 3]:
                        if f.__name__ == "test_single_batch_exact_count_invariants":
                            f(self, target_count=target_count, batch_size=batch_size)
                        else:
                            f(self, target_count=target_count, batch_sizes=[batch_size, 2, 0])

            return wrapper

        return decorator

    class DummyStrategies:
        def sampled_from(self, *a, **k):
            return None

        def integers(self, *a, **k):
            return None

        def lists(self, *a, **k):
            return None

    st = DummyStrategies()

from waifubot.core.models import GenerationJob, ImageAsset, JobErrorCode, JobStatus
from waifubot.services.generation import process_job_batch


def create_mock_image(identifier: str) -> ImageAsset:
    """Helper creating unique ImageAsset with predictable hash."""
    h = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return ImageAsset(content=identifier.encode("utf-8"), sha256=h)


class TestExactCountGateHypothesis(unittest.TestCase):
    """Property-based verification of exact-count gate invariants across arbitrary batches."""

    @given(
        target_count=st.sampled_from([2, 4, 6, 8, 16]),
        batch_size=st.integers(min_value=0, max_value=32),
    )
    def test_single_batch_exact_count_invariants(self, target_count: int, batch_size: int) -> None:
        """Core invariant: kept_count NEVER exceeds target_count, and SUCCEEDED iff kept == N."""
        job = GenerationJob(
            job_id="hyp-job-1",
            requester_user_id=101,
            prompt="anime girl",
            target_count=target_count,
            needed=target_count,
        )

        batch = [create_mock_image(f"batch1_img_{i}") for i in range(batch_size)]
        job = process_job_batch(job, batch)

        # Invariant 1: Kept count never exceeds target_count
        self.assertLessEqual(len(job.kept_images), target_count)

        # Invariant 2: Surplus dropped is correctly accounted
        if batch_size > target_count:
            self.assertEqual(job.surplus_dropped, batch_size - target_count)
            self.assertEqual(len(job.kept_images), target_count)

        # Invariant 3: SUCCEEDED iff kept == target_count
        if len(job.kept_images) == target_count:
            self.assertEqual(job.status, JobStatus.SUCCEEDED)
            self.assertEqual(job.needed, 0)
            self.assertIsNone(job.error_code)
        else:
            self.assertNotEqual(job.status, JobStatus.SUCCEEDED)
            self.assertEqual(job.needed, target_count - len(job.kept_images))

    @given(
        target_count=st.sampled_from([2, 4, 6, 8, 16]),
        batch_sizes=st.lists(st.integers(min_value=0, max_value=10), min_size=1, max_size=10),
    )
    def test_multi_round_topup_invariants(self, target_count: int, batch_sizes: list[int]) -> None:
        """In multi-round top-up, invariants hold and streaks trigger appropriate termination."""
        job = GenerationJob(
            job_id="hyp-job-multi",
            requester_user_id=101,
            prompt="anime girl",
            target_count=target_count,
            needed=target_count,
            max_rounds=6,
            zero_progress_limit=3,
        )

        img_counter = 0
        for size in batch_sizes:
            if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                break

            batch = []
            for _ in range(size):
                batch.append(create_mock_image(f"round_img_{img_counter}"))
                img_counter += 1

            job = process_job_batch(job, batch)

            # Invariant check at every round
            self.assertLessEqual(len(job.kept_images), target_count)
            if job.status == JobStatus.SUCCEEDED:
                self.assertEqual(len(job.kept_images), target_count)
                self.assertEqual(job.needed, 0)

        # Final state check
        if job.status == JobStatus.FAILED:
            self.assertIn(
                job.error_code, [JobErrorCode.PARTIAL_COUNT, JobErrorCode.NO_VALID_IMAGES]
            )
            self.assertLess(len(job.kept_images), target_count)


if __name__ == "__main__":
    unittest.main()
