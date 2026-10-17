"""Database models: persistent JobRecord and schema entities."""

import time
from sqlalchemy import BigInteger, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from waifubot.db.base import Base


class JobRecord(Base):
    """Persistent generation job model with durable lease fields (ADR-0002, ADR-0005)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    requester_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    job_scope: Mapped[str] = mapped_column(String(16), default="staff", nullable=False)

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    needed: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Progress and round counters (R38)
    run_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rounds_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_rounds: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    zero_progress_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    zero_progress_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    surplus_dropped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Retry and attempt tracking (R12, R36)
    attempts_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_attempt_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)

    # Priority and fairness (R34, R55)
    queue_priority: Mapped[int] = mapped_column(Integer, default=0, index=True, nullable=False)

    # Durable claiming lease fields (ADR-0002)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)

    # Timestamps
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
