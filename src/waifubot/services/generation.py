"""Generation service: Exact-count gate and Top-up progress accounting (R9, R38)."""

from waifubot.core.models import GenerationJob, ImageAsset, JobErrorCode, JobStatus


def process_job_batch(job: GenerationJob, batch_images: list[ImageAsset]) -> GenerationJob:
    """Evaluate batch at the exact-count gate.

    Contract:
    - Target count N must be exactly satisfied (R9).
    - If provider returns surplus (> N - kept), truncate surplus and log/record surplus_dropped.
    - Zero-progress streak incremented if no new images were added in this round.
    - If streak reaches zero_progress_limit or attempts run out: mark FAILED.
    - Otherwise set RETRY_WAIT to top-up only missing images (needed = N - kept).
    """
    # Exclude duplicates against already kept images
    existing_hashes = {img.sha256 for img in job.kept_images}
    unique_new_images = [img for img in batch_images if img.sha256 not in existing_hashes]

    needed_now = job.target_count - len(job.kept_images)
    to_keep = unique_new_images[:needed_now]
    surplus = max(0, len(unique_new_images) - needed_now)

    job.kept_images.extend(to_keep)
    job.surplus_dropped += surplus

    # Accounting progress streak (R38)
    if len(to_keep) == 0:
        job.zero_progress_streak += 1
    else:
        job.zero_progress_streak = 0

    job.rounds_done += 1

    # Exact-count gate decision
    if len(job.kept_images) == job.target_count:
        job.status = JobStatus.SUCCEEDED
        job.needed = 0
        job.error_code = None
    else:
        job.needed = job.target_count - len(job.kept_images)
        if job.zero_progress_streak >= job.zero_progress_limit or job.rounds_done >= job.max_rounds:
            job.status = JobStatus.FAILED
            job.error_code = (
                JobErrorCode.PARTIAL_COUNT
                if len(job.kept_images) > 0
                else JobErrorCode.NO_VALID_IMAGES
            )
        else:
            job.status = JobStatus.RETRY_WAIT

    return job


def manual_retry_job(job: GenerationJob) -> GenerationJob:
    """Manual retry starts a fresh run with reset streaks (R38)."""
    job.run_no += 1
    job.rounds_done = 0
    job.zero_progress_streak = 0
    job.error_code = None
    job.needed = job.target_count - len(job.kept_images)
    job.status = JobStatus.PENDING
    return job


class GenerationService:
    """Orchestrates job lifecycle, exact-count gate, and top-up progress (R9, R38)."""

    def __init__(self, clock: object | None = None) -> None:
        self.clock = clock

    def process_batch(self, job: GenerationJob, batch_images: list[ImageAsset]) -> GenerationJob:
        return process_job_batch(job, batch_images)

    def manual_retry(self, job: GenerationJob) -> GenerationJob:
        return manual_retry_job(job)

