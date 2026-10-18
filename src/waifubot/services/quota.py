"""Quota accounting and reservation service (R54, R55, §3.6).

Contract:
- Limits are checked and reserved atomically at action start.
- Superadmin has no limits (R53 short-circuit).
- Generation: reserves images against daily_image_budget, max_images_per_job,
  max_concurrent_jobs, and max_stored_images.
- Exact-count gate: settles to kept count, refunds unused images.
- Failed or cancelled jobs refund the entire reservation.
- Days are cut in app_timezone (default Asia/Tehran).
"""

import zoneinfo
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.limits import Limits
from waifubot.db.base import Base
from waifubot.db.models import ImageRecord, JobRecord, UsageCounterRecord


class QuotaExceededError(Exception):
    """Raised when an operation exceeds user's effective limits (R54)."""

    def __init__(
        self,
        limit_name: str,
        current: int,
        limit: int,
        reset_at: float | None = None,
    ) -> None:
        self.limit_name = limit_name
        self.current = current
        self.limit = limit
        self.reset_at = reset_at
        super().__init__(
            f"Quota exceeded for '{limit_name}': current={current}, limit={limit}, reset_at={reset_at}"
        )


def get_current_period_key(
    tz_name: str = "Asia/Tehran",
    now: float | None = None,
) -> tuple[str, float]:
    """Returns (period_key, next_reset_timestamp) for the given timezone."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")

    dt = datetime.fromtimestamp(now, tz=tz) if now is not None else datetime.now(tz=tz)
    period_key = dt.strftime("%Y-%m-%d")

    next_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    reset_at = next_midnight.timestamp()
    return period_key, reset_at


async def get_or_create_usage_counter(
    session: AsyncSession,
    user_id: int,
    period_key: str,
) -> UsageCounterRecord:
    """Gets or creates daily usage counter row in usage_counters table."""
    stmt = select(UsageCounterRecord).where(
        UsageCounterRecord.user_id == user_id,
        UsageCounterRecord.period_key == period_key,
    )
    res = await session.execute(stmt)
    counter = res.scalar_one_or_none()
    if counter is None:
        counter = UsageCounterRecord(
            user_id=user_id,
            period_key=period_key,
            images=0,
            jobs=0,
            chat_messages=0,
            tokens_in=0,
            tokens_out=0,
        )
        session.add(counter)
        await session.flush()
    return counter


async def count_user_stored_images(session: AsyncSession, user_id: int) -> int:
    """Counts current stored images belonging to the user."""
    stmt = (
        select(func.count(ImageRecord.id))
        .join(JobRecord, ImageRecord.job_id == JobRecord.job_id)
        .where(JobRecord.requester_user_id == user_id)
    )
    res = await session.execute(stmt)
    return int(res.scalar_one() or 0)


async def count_user_owned_characters(session: AsyncSession, user_id: int) -> int:
    """Counts characters owned by user (safe check whether table exists or not)."""
    if "character_ownership" in Base.metadata.tables:
        from sqlalchemy import text

        res = await session.execute(
            text(
                "SELECT count(*) FROM character_ownership WHERE owner_user_id = :u AND status IN ('claimed', 'private')"
            ),
            {"u": user_id},
        )
        return int(res.scalar_one() or 0)
    return 0


async def count_user_channels(session: AsyncSession, user_id: int) -> int:
    """Counts channels owned by user (safe check whether channels table exists)."""
    if "channels" in Base.metadata.tables:
        from sqlalchemy import text

        res = await session.execute(
            text("SELECT count(*) FROM channels WHERE owner_user_id = :u AND status = 'active'"),
            {"u": user_id},
        )
        return int(res.scalar_one() or 0)
    return 0


async def check_and_reserve(
    session: AsyncSession,
    user_id: int,
    role: str,
    limits: Limits,
    kind: str,
    amount: int = 1,
    app_timezone: str = "Asia/Tehran",
    now: float | None = None,
) -> bool:
    """Checks and reserves quota atomically for an action (R54).

    Short-circuits for superadmin (R53).
    Raises QuotaExceededError if limit is breached.
    """
    if role == "superadmin":
        return True

    period_key, reset_at = get_current_period_key(tz_name=app_timezone, now=now)

    if kind == "generation":
        # 1. max_images_per_job
        if limits.max_images_per_job is not None and amount > limits.max_images_per_job:
            raise QuotaExceededError(
                limit_name="max_images_per_job",
                current=amount,
                limit=limits.max_images_per_job,
            )

        # 2. max_concurrent_jobs
        active_stmt = select(func.count(JobRecord.id)).where(
            JobRecord.requester_user_id == user_id,
            JobRecord.status.in_(["pending", "running", "retry_wait"]),
        )
        active_res = await session.execute(active_stmt)
        active_jobs = int(active_res.scalar_one() or 0)
        if limits.max_concurrent_jobs is not None and active_jobs >= limits.max_concurrent_jobs:
            raise QuotaExceededError(
                limit_name="max_concurrent_jobs",
                current=active_jobs,
                limit=limits.max_concurrent_jobs,
            )

        # 3. max_stored_images
        stored_images = await count_user_stored_images(session, user_id)
        if (
            limits.max_stored_images is not None
            and (stored_images + amount) > limits.max_stored_images
        ):
            raise QuotaExceededError(
                limit_name="max_stored_images",
                current=stored_images,
                limit=limits.max_stored_images,
            )

        # 4. daily_image_budget
        counter = await get_or_create_usage_counter(session, user_id, period_key)
        if (
            limits.daily_image_budget is not None
            and (counter.images + amount) > limits.daily_image_budget
        ):
            raise QuotaExceededError(
                limit_name="daily_image_budget",
                current=counter.images,
                limit=limits.daily_image_budget,
                reset_at=reset_at,
            )

        counter.images += amount
        counter.jobs += 1
        await session.flush()
        return True

    elif kind == "chat":
        counter = await get_or_create_usage_counter(session, user_id, period_key)
        if (
            limits.daily_chat_messages is not None
            and (counter.chat_messages + amount) > limits.daily_chat_messages
        ):
            raise QuotaExceededError(
                limit_name="daily_chat_messages",
                current=counter.chat_messages,
                limit=limits.daily_chat_messages,
                reset_at=reset_at,
            )
        counter.chat_messages += amount
        await session.flush()
        return True

    elif kind == "character":
        owned_count = await count_user_owned_characters(session, user_id)
        if (
            limits.max_owned_characters is not None
            and (owned_count + amount) > limits.max_owned_characters
        ):
            raise QuotaExceededError(
                limit_name="max_owned_characters",
                current=owned_count,
                limit=limits.max_owned_characters,
            )
        return True

    elif kind == "channel":
        channels_count = await count_user_channels(session, user_id)
        if limits.max_channels is not None and (channels_count + amount) > limits.max_channels:
            raise QuotaExceededError(
                limit_name="max_channels",
                current=channels_count,
                limit=limits.max_channels,
            )
        return True

    return True


async def settle_reservation(
    session: AsyncSession,
    user_id: int,
    role: str,
    reserved_amount: int,
    actual_amount: int,
    app_timezone: str = "Asia/Tehran",
    now: float | None = None,
) -> None:
    """Settles generation quota: refunds difference between reserved and actual kept images (R54)."""
    if role == "superadmin":
        return

    diff = reserved_amount - actual_amount
    if diff > 0:
        period_key, _ = get_current_period_key(tz_name=app_timezone, now=now)
        counter = await get_or_create_usage_counter(session, user_id, period_key)
        counter.images = max(0, counter.images - diff)
        await session.flush()


async def refund_reservation(
    session: AsyncSession,
    user_id: int,
    role: str,
    amount: int,
    app_timezone: str = "Asia/Tehran",
    now: float | None = None,
) -> None:
    """Refunds entire reservation for failed or cancelled jobs (R54)."""
    if role == "superadmin":
        return

    period_key, _ = get_current_period_key(tz_name=app_timezone, now=now)
    counter = await get_or_create_usage_counter(session, user_id, period_key)
    counter.images = max(0, counter.images - amount)
    counter.jobs = max(0, counter.jobs - 1)
    await session.flush()


async def record_chat_tokens(
    session: AsyncSession,
    user_id: int,
    tokens_in: int,
    tokens_out: int,
    app_timezone: str = "Asia/Tehran",
    now: float | None = None,
) -> None:
    """Records input and output token consumption into usage_counters (R65)."""
    period_key, _ = get_current_period_key(tz_name=app_timezone, now=now)
    counter = await get_or_create_usage_counter(session, user_id, period_key)
    counter.tokens_in += tokens_in
    counter.tokens_out += tokens_out
    await session.flush()


async def get_user_usage_summary(
    session: AsyncSession,
    user_id: int,
    app_timezone: str = "Asia/Tehran",
    now: float | None = None,
) -> dict[str, int | float]:
    """Returns today's usage summary and reset time for My Account screen (R51)."""
    period_key, reset_at = get_current_period_key(tz_name=app_timezone, now=now)
    counter = await get_or_create_usage_counter(session, user_id, period_key)
    stored_images = await count_user_stored_images(session, user_id)
    owned_chars = await count_user_owned_characters(session, user_id)
    channels_count = await count_user_channels(session, user_id)

    return {
        "images_today": counter.images,
        "jobs_today": counter.jobs,
        "chat_messages_today": counter.chat_messages,
        "stored_images": stored_images,
        "owned_characters": owned_chars,
        "channels_count": channels_count,
        "reset_at": reset_at,
    }
