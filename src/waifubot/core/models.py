"""Domain data models for generation jobs and pipeline assets (R9, R34, R38)."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobErrorCode(StrEnum):
    PARTIAL_COUNT = "partial_count"
    NO_VALID_IMAGES = "no_valid_images"
    SAFETY_REJECTED = "safety_rejected"
    LEASE_EXPIRED = "lease_expired"
    PROVIDER_ERROR = "provider_error"


SITE_COUNTS = (2, 4, 6, 8, 16)


@dataclass
class ImageAsset:
    content: bytes
    sha256: str
    mime_type: str = "image/png"
    has_spoiler: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationJob:
    job_id: str
    requester_user_id: int
    prompt: str
    target_count: int
    negative_prompt: str = ""
    job_scope: str = "user"  # staff | user
    status: JobStatus = JobStatus.PENDING
    error_code: JobErrorCode | None = None

    # Tracking & Exact-count gate counters (R9, R38)
    needed: int = 0
    kept_images: list[ImageAsset] = field(default_factory=list)
    surplus_dropped: int = 0

    # Top-up budget & progress guard (R38)
    rounds_done: int = 0
    zero_progress_streak: int = 0
    run_no: int = 1
    max_rounds: int = 6
    zero_progress_limit: int = 3

    # Lease info (R36)
    lease_owner: str | None = None
    lease_expires_at: float | None = None
    heartbeat_at: float | None = None
    next_attempt_at: float | None = None

    def __post_init__(self):
        if self.needed == 0:
            self.needed = self.target_count
