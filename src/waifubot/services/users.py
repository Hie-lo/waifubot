"""User management service (R50).

Contract:
- Every person who interacts with the bot is tracked in the `users` table.
- Overview metrics: total, active today, new this week, blocked.
- Paged list with filters (all, active, blocked, new) and search (telegram_id, username, name).
- User card operations: block/unblock, note, locale, delete record.
- Admins cannot be blocked (R50: change role in R48 first).
- Deleting a record removes the profile; if they return, they are recorded as new.
- All actions are audited (R44).
"""

import time

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import UserRecord
from waifubot.services.admins import is_admin_or_superadmin
from waifubot.services.audit import record_audit


class UserNotFoundError(Exception):
    """Raised when a user record is not found."""


class CannotBlockAdminError(Exception):
    """Raised when attempting to block an active admin (R50)."""


async def record_user_seen(
    session: AsyncSession,
    telegram_id: int,
    name: str,
    username: str | None = None,
    locale: str = "fa",
    joined_via: str | None = None,
) -> tuple[UserRecord, bool]:
    """Records user interaction (upsert). Returns (record, is_new)."""
    now = time.time()
    stmt = select(UserRecord).where(UserRecord.telegram_id == telegram_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()

    if user is not None:
        user.last_seen = now
        user.name = name.strip() or user.name
        if username is not None:
            user.username = username.strip()
        await session.flush()
        return user, False

    new_user = UserRecord(
        telegram_id=telegram_id,
        username=username.strip() if username else None,
        name=name.strip() or f"User_{telegram_id}",
        locale=locale,
        first_seen=now,
        last_seen=now,
        status="active",
        block_reason=None,
        note=None,
        joined_via=joined_via,
    )
    session.add(new_user)
    await session.flush()
    return new_user, True


async def get_user(session: AsyncSession, telegram_id: int) -> UserRecord | None:
    """Fetches user record by Telegram ID."""
    stmt = select(UserRecord).where(UserRecord.telegram_id == telegram_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> UserRecord | None:
    """Fetches user record by internal DB ID."""
    stmt = select(UserRecord).where(UserRecord.id == user_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def block_user(
    session: AsyncSession,
    telegram_id: int,
    reason: str | None = None,
    admin_id: int = 0,
) -> UserRecord:
    """Blocks a user. Fails if user is an active admin (R50)."""
    if await is_admin_or_superadmin(session, telegram_id):
        raise CannotBlockAdminError(
            f"User {telegram_id} is an active admin. Demote or remove them from Admins first."
        )

    user = await get_user(session, telegram_id)
    if user is None:
        raise UserNotFoundError(f"User {telegram_id} not found")

    old_status = user.status
    user.status = "blocked"
    user.block_reason = reason.strip() if reason else None

    await record_audit(
        session=session,
        admin_id=admin_id,
        action="user_block",
        target=f"user:{telegram_id}",
        old_val={"status": old_status},
        new_val={"status": "blocked", "reason": reason},
    )
    await session.flush()
    return user


async def unblock_user(
    session: AsyncSession,
    telegram_id: int,
    admin_id: int = 0,
) -> UserRecord:
    """Unblocks a user."""
    user = await get_user(session, telegram_id)
    if user is None:
        raise UserNotFoundError(f"User {telegram_id} not found")

    old_status = user.status
    user.status = "active"
    user.block_reason = None

    await record_audit(
        session=session,
        admin_id=admin_id,
        action="user_unblock",
        target=f"user:{telegram_id}",
        old_val={"status": old_status},
        new_val={"status": "active"},
    )
    await session.flush()
    return user


async def set_user_note(
    session: AsyncSession,
    telegram_id: int,
    note: str | None,
    admin_id: int = 0,
) -> UserRecord:
    """Sets administrative note on a user card."""
    user = await get_user(session, telegram_id)
    if user is None:
        raise UserNotFoundError(f"User {telegram_id} not found")

    old_note = user.note
    user.note = note.strip() if note else None

    await record_audit(
        session=session,
        admin_id=admin_id,
        action="user_note_edit",
        target=f"user:{telegram_id}",
        old_val={"note": old_note},
        new_val={"note": user.note},
    )
    await session.flush()
    return user


async def set_user_locale(
    session: AsyncSession,
    telegram_id: int,
    locale: str,
) -> UserRecord:
    """Sets user preferred language (fa/en)."""
    user = await get_user(session, telegram_id)
    if user is None:
        raise UserNotFoundError(f"User {telegram_id} not found")

    user.locale = locale
    await session.flush()
    return user


async def delete_user_record(
    session: AsyncSession,
    telegram_id: int,
    admin_id: int = 0,
) -> bool:
    """Deletes user profile completely (R50)."""
    user = await get_user(session, telegram_id)
    if user is None:
        return False

    await record_audit(
        session=session,
        admin_id=admin_id,
        action="user_delete",
        target=f"user:{telegram_id}",
        old_val={"telegram_id": telegram_id, "name": user.name},
        new_val=None,
    )
    await session.delete(user)
    await session.flush()
    return True


async def get_users_overview(session: AsyncSession) -> dict[str, int]:
    """Returns overview statistics: total, active_today, new_this_week, blocked."""
    now = time.time()
    day_ago = now - 86400.0
    week_ago = now - (7 * 86400.0)

    total_stmt = select(func.count(UserRecord.id))
    active_today_stmt = select(func.count(UserRecord.id)).where(UserRecord.last_seen >= day_ago)
    new_this_week_stmt = select(func.count(UserRecord.id)).where(UserRecord.first_seen >= week_ago)
    blocked_stmt = select(func.count(UserRecord.id)).where(UserRecord.status == "blocked")

    total = (await session.execute(total_stmt)).scalar_one() or 0
    active_today = (await session.execute(active_today_stmt)).scalar_one() or 0
    new_this_week = (await session.execute(new_this_week_stmt)).scalar_one() or 0
    blocked = (await session.execute(blocked_stmt)).scalar_one() or 0

    return {
        "total": total,
        "active_today": active_today,
        "new_this_week": new_this_week,
        "blocked": blocked,
    }


async def list_users(
    session: AsyncSession,
    filter_type: str = "all",
    search_query: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[UserRecord], int]:
    """Retrieves paged user records matching filter and search query."""
    now = time.time()
    week_ago = now - (7 * 86400.0)

    base_query = select(UserRecord)
    count_query = select(func.count(UserRecord.id))

    # Apply filters
    if filter_type == "active":
        base_query = base_query.where(UserRecord.status == "active")
        count_query = count_query.where(UserRecord.status == "active")
    elif filter_type == "blocked":
        base_query = base_query.where(UserRecord.status == "blocked")
        count_query = count_query.where(UserRecord.status == "blocked")
    elif filter_type == "new":
        base_query = base_query.where(UserRecord.first_seen >= week_ago)
        count_query = count_query.where(UserRecord.first_seen >= week_ago)

    # Apply search
    if search_query:
        query_str = search_query.strip()
        search_conditions = []
        if query_str.isdigit():
            tg_val = int(query_str)
            search_conditions.append(UserRecord.telegram_id == tg_val)
            search_conditions.append(UserRecord.id == tg_val)

        pattern = f"%{query_str}%"
        search_conditions.append(UserRecord.username.ilike(pattern))
        search_conditions.append(UserRecord.name.ilike(pattern))

        combined = or_(*search_conditions)
        base_query = base_query.where(combined)
        count_query = count_query.where(combined)

    total_count = (await session.execute(count_query)).scalar_one() or 0

    base_query = (
        base_query.order_by(desc(UserRecord.last_seen), desc(UserRecord.id))
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.execute(base_query)).scalars().all())

    return items, total_count
