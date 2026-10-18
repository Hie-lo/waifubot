"""Database models: persistent JobRecord and schema entities."""

import time

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, String, Text
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


class ImageRecord(Base):
    """Stored image asset with provenance metadata and perceptual hash (R43, R47)."""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    phash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(32), default="image/png", nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Provenance columns (R43)
    job_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    character_version_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    final_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    seed: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="perchance", nullable=False)
    provider_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    is_enhanced: Mapped[bool] = mapped_column(default=False, nullable=False)
    telegram_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True, nullable=False)


class CharacterRecord(Base):
    """Character appearance record with version history (R14, R18)."""

    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(default=True, index=True, nullable=False)
    edit_of: Mapped[int | None] = mapped_column(Integer, nullable=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    appearance_facts: Mapped[str] = mapped_column(Text, nullable=False)
    traits_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extras: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="known", nullable=False)

    created_by: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class ScheduleRecord(Base):
    """Channel posting schedule with interval bounds and missed run policy (R45)."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channels.id"), index=True, nullable=False
    )
    topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    random_order: Mapped[bool] = mapped_column(default=False, nullable=False)
    min_gap_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    max_gap_minutes: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    next_at: Mapped[float] = mapped_column(Float, index=True, nullable=False)
    is_running: Mapped[bool] = mapped_column(default=True, nullable=False)
    missed_run_policy: Mapped[str] = mapped_column(
        String(32), default="catch_up_one", nullable=False
    )
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class AuditLogRecord(Base):
    """Audit log entry for settings modifications and admin management (R44)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[float] = mapped_column(Float, default=time.time, index=True, nullable=False)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    old_val: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_val: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdminRecord(Base):
    """Admin registry with soft-removal and last-superadmin invariant (R44, R48)."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), default="admin", nullable=False
    )  # admin | superadmin
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    removed_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)


class UserRecord(Base):
    """Registered Telegram user profile with status and moderation note (R50)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), default="fa", nullable=False)
    first_seen: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    last_seen: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="active", index=True, nullable=False
    )  # active | blocked
    block_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    joined_via: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Plans and overrides (R52)
    plan_id: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    plan_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_overrides_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlanRecord(Base):
    """Named bundle of limits (R52, §3.6)."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    limits_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class UsageCounterRecord(Base):
    """Daily/periodic usage tracking for quota accounting in app_timezone (R54)."""

    __tablename__ = "usage_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    period_key: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    images: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chat_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AdminInviteRecord(Base):
    """Single-use expiring admin invite link token (R48)."""

    __tablename__ = "admin_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="admin", nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, index=True, nullable=False)
    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ChannelRecord(Base):
    """Registered channel or group with verified permissions and scope (R56–R59)."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="channel", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alias: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True, nullable=False)
    bot_rights_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    verified_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    adder_role_in_chat: Mapped[str] = mapped_column(
        String(32), default="administrator", nullable=False
    )
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class ChannelTopicRecord(Base):
    """Forum group topics for topic picker and targeted publishing (R23, R56)."""

    __tablename__ = "channel_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)


class CharacterOwnershipRecord(Base):
    """Character ownership and claim state (R60, R61)."""

    __tablename__ = "character_ownership"

    character_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(32), default="draft", nullable=False
    )  # draft | listed | claimed | private
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    claimable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    released_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class ClaimPostRecord(Base):
    """Channel post with deep-link claim button for best-effort edit on claim (R61)."""

    __tablename__ = "claim_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
