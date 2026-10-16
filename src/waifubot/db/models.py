"""The 5 MVP tables (SPEC §5). UTC timestamps; foreign keys enabled.

Provider-dependent fields (seed, dimensions, formats) are nullable or derived
from validated files — the schema does not assume what the provider returns.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from waifubot.db.base import Base

JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
IMAGE_STATUSES = ("pending_review", "approved", "rejected", "published", "missing")
POST_STATUSES = ("pending", "sent", "failed", "unknown")


class Character(Base):
    """A stored character. Identity text is immutable per row; a change creates
    a new version row pointing at its parent."""

    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    identity_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    style_lock: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Self-referential: cannot use lazy="selectin"; load via awaitable_attrs in async code.
    parent: Mapped[Character | None] = relationship(
        "Character", remote_side=[id], back_populates="children"
    )
    children: Mapped[list[Character]] = relationship("Character", back_populates="parent")


class Job(Base):
    """Durable generation queue."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_priority_created_id", "status", "priority", "created_at", "id"),
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','cancelled')",
            name="ck_jobs_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    filters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    character_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True
    )
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    character: Mapped[Character | None] = relationship("Character", lazy="selectin")
    images: Mapped[list[Image]] = relationship("Image", back_populates="job", lazy="selectin")


class Image(Base):
    """A validated, stored image. `seed` only if the provider genuinely returns one."""

    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("job_id", "sha256", name="uq_images_job_sha256"),
        Index("ix_images_sha256", "sha256"),
        CheckConstraint(
            "status IN ('pending_review','approved','rejected','published','missing')",
            name="ck_images_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    character_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_path: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending_review")
    seed: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # User request 2026-10-16: admin marks images NSFW; such posts go out with a
    # spoiler cover (per-image, including inside media groups).
    is_nsfw: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped[Job] = relationship("Job", back_populates="images", lazy="selectin")
    character: Mapped[Character | None] = relationship("Character", lazy="selectin")
    posts: Mapped[list[Post]] = relationship("Post", back_populates="image", lazy="selectin")


class Post(Base):
    """A publication attempt of one image to one destination chat."""

    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("image_id", "chat_id", name="uq_posts_image_chat"),
        CheckConstraint(
            "status IN ('pending','sent','failed','unknown')",
            name="ck_posts_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id"), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    image: Mapped[Image] = relationship("Image", back_populates="posts", lazy="selectin")


class Setting(Base):
    """Admin-tunable values. A value here can never override a hard limit."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
