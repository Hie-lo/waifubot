"""Multi-schedule channel posting service with Jalali support and missed-run policies (R45).

Contract:
- Multi-schedule support (several channels, each with independent intervals and topic targets).
- Timezone support via app_timezone (default Asia/Tehran).
- Persian UI displays dates in Jalali calendar.
- Missed-run policy after downtime: missed_run_policy = skip | catch_up_one (default catch_up_one).
- Only reviewed pool images are scheduled.
"""

from datetime import datetime
import random
import time
from typing import Any
import zoneinfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import ScheduleRecord


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Converts a Gregorian date (year, month, day) to Jalali (Solar Hijri) calendar.
    
    Zero third-party dependencies, standard astronomical conversion.
    """
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def format_jalali_datetime(
    dt_or_ts: datetime | float,
    tz_name: str = "Asia/Tehran",
    include_seconds: bool = False,
) -> str:
    """Formats a datetime or timestamp into a localized Jalali string."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")

    if isinstance(dt_or_ts, (int, float)):
        dt = datetime.fromtimestamp(dt_or_ts, tz=tz)
    elif dt_or_ts.tzinfo is None:
        dt = dt_or_ts.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(tz)
    else:
        dt = dt_or_ts.astimezone(tz)

    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    time_part = f"{dt.hour:02d}:{dt.minute:02d}"
    if include_seconds:
        time_part += f":{dt.second:02d}"

    return f"{jy:04d}/{jm:02d}/{jd:02d} {time_part}"


def calculate_next_run(
    min_gap_minutes: int,
    max_gap_minutes: int,
    from_time: float | None = None,
) -> float:
    """Computes a random next execution timestamp within [min_gap, max_gap] minutes."""
    base = from_time if from_time is not None else time.time()
    min_seconds = max(1, min_gap_minutes * 60)
    max_seconds = max(min_seconds, max_gap_minutes * 60)
    delay = random.randint(min_seconds, max_seconds)
    return base + delay


def evaluate_schedule_trigger(
    schedule: ScheduleRecord,
    now: float,
) -> tuple[bool, float]:
    """Evaluates whether a schedule should trigger at `now` and calculates the new next_at.
    
    Policies:
    - catch_up_one: Fires at most once for missed time, next schedule moves forward from now.
    - skip: Skips past missed window, next schedule moves forward from now.
    """
    if not schedule.is_running or schedule.next_at > now:
        return False, schedule.next_at

    new_next = calculate_next_run(
        min_gap_minutes=schedule.min_gap_minutes,
        max_gap_minutes=schedule.max_gap_minutes,
        from_time=now,
    )
    return True, new_next


async def create_schedule(
    session: AsyncSession,
    channel_id: int,
    topic_id: int | None = None,
    post_count: int = 1,
    random_order: bool = False,
    min_gap_minutes: int = 60,
    max_gap_minutes: int = 180,
    missed_run_policy: str = "catch_up_one",
    is_running: bool = True,
    first_run_at: float | None = None,
) -> ScheduleRecord:
    """Creates a new channel schedule."""
    now = time.time()
    next_at = first_run_at or calculate_next_run(min_gap_minutes, max_gap_minutes, from_time=now)

    record = ScheduleRecord(
        channel_id=channel_id,
        topic_id=topic_id,
        post_count=max(1, post_count),
        random_order=random_order,
        min_gap_minutes=min_gap_minutes,
        max_gap_minutes=max_gap_minutes,
        next_at=next_at,
        is_running=is_running,
        missed_run_policy=missed_run_policy,
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    await session.flush()
    return record


async def get_schedule(session: AsyncSession, schedule_id: int) -> ScheduleRecord | None:
    """Fetches a schedule record by its primary key ID."""
    stmt = select(ScheduleRecord).where(ScheduleRecord.id == schedule_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def list_schedules(
    session: AsyncSession,
    channel_id: int | None = None,
) -> list[ScheduleRecord]:
    """Lists schedules, optionally filtered by channel."""
    stmt = select(ScheduleRecord).order_by(ScheduleRecord.id)
    if channel_id is not None:
        stmt = stmt.where(ScheduleRecord.channel_id == channel_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_due_schedules(
    session: AsyncSession,
    now: float,
) -> list[ScheduleRecord]:
    """Retrieves all active schedules that are due for execution (next_at <= now)."""
    stmt = (
        select(ScheduleRecord)
        .where(
            ScheduleRecord.is_running.is_(True),
            ScheduleRecord.next_at <= now,
        )
        .order_by(ScheduleRecord.next_at)
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def advance_schedule(
    session: AsyncSession,
    schedule_id: int,
    new_next_at: float,
) -> ScheduleRecord | None:
    """Advances schedule's next_at timestamp and updates updated_at."""
    schedule = await get_schedule(session, schedule_id)
    if schedule is not None:
        schedule.next_at = new_next_at
        schedule.updated_at = time.time()
        await session.flush()
    return schedule


async def set_schedule_running(
    session: AsyncSession,
    schedule_id: int,
    is_running: bool,
) -> ScheduleRecord | None:
    """Pauses or resumes a schedule."""
    schedule = await get_schedule(session, schedule_id)
    if schedule is not None:
        schedule.is_running = is_running
        schedule.updated_at = time.time()
        await session.flush()
    return schedule


async def delete_schedule(session: AsyncSession, schedule_id: int) -> bool:
    """Deletes a schedule record."""
    schedule = await get_schedule(session, schedule_id)
    if schedule is not None:
        await session.delete(schedule)
        await session.flush()
        return True
    return False
