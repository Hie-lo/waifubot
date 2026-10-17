"""Admin registry and management service (R44, R48).

Contract:
- Admin seeding from ADMIN_IDS inserts missing admins as superadmin without re-promoting or demoting existing records.
- Soft-removed admins (removed_at is not None) are not re-seeded.
- Last-superadmin invariant: at least one active superadmin must exist; the last superadmin cannot be removed or demoted.
- Single-use expiring invite tokens for onboarding admins.
- Every action is audited (R44).
"""

import hashlib
import secrets
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import AdminInviteRecord, AdminRecord
from waifubot.services.audit import record_audit


class LastSuperadminError(Exception):
    """Raised when an operation would violate the last-superadmin invariant."""


class AdminNotFoundError(Exception):
    """Raised when the specified admin is not found or is inactive."""


class AdminAlreadyExistsError(Exception):
    """Raised when an active admin already exists."""


class InviteInvalidError(Exception):
    """Raised when an invite token is invalid or already consumed."""


class InviteExpiredError(Exception):
    """Raised when an invite token has expired."""


async def seed_admins(session: AsyncSession, admin_ids: list[int]) -> int:
    """Inserts admins from ADMIN_IDS that are missing from the admins table.

    Invariants:
    - Inserts as superadmin.
    - Never changes roles of existing admins.
    - Soft-removed admins (removed_at is not None) are NOT re-seeded.
    """
    if not admin_ids:
        return 0

    now = time.time()
    inserted_count = 0

    for tg_id in admin_ids:
        stmt = select(AdminRecord).where(AdminRecord.telegram_id == tg_id)
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing is None:
            new_admin = AdminRecord(
                telegram_id=tg_id,
                role="superadmin",
                added_by=None,
                created_at=now,
                removed_at=None,
            )
            session.add(new_admin)
            await record_audit(
                session=session,
                admin_id=tg_id,
                action="admin_seed",
                target=f"admin:{tg_id}",
                new_val={"role": "superadmin"},
            )
            inserted_count += 1

    await session.flush()
    return inserted_count


async def add_or_restore_admin(
    session: AsyncSession,
    telegram_id: int,
    role: str = "admin",
    added_by: int | None = None,
) -> AdminRecord:
    """Adds a new admin or restores a soft-removed admin."""
    now = time.time()
    stmt = select(AdminRecord).where(AdminRecord.telegram_id == telegram_id)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing is not None:
        if existing.removed_at is None:
            raise AdminAlreadyExistsError(f"Admin with telegram_id {telegram_id} is already active")

        # Restore soft-removed admin
        old_role = existing.role
        existing.removed_at = None
        existing.role = role
        existing.added_by = added_by
        await record_audit(
            session=session,
            admin_id=added_by or 0,
            action="admin_restore",
            target=f"admin:{telegram_id}",
            old_val={"role": old_role, "removed": True},
            new_val={"role": role, "removed": False},
        )
        await session.flush()
        return existing

    record = AdminRecord(
        telegram_id=telegram_id,
        role=role,
        added_by=added_by,
        created_at=now,
        removed_at=None,
    )
    session.add(record)
    await record_audit(
        session=session,
        admin_id=added_by or 0,
        action="admin_add",
        target=f"admin:{telegram_id}",
        new_val={"role": role},
    )
    await session.flush()
    return record


async def count_active_superadmins(session: AsyncSession) -> int:
    """Counts currently active superadmins."""
    stmt = select(func.count(AdminRecord.id)).where(
        AdminRecord.removed_at.is_(None),
        AdminRecord.role == "superadmin",
    )
    res = await session.execute(stmt)
    return res.scalar_one() or 0


async def remove_admin(
    session: AsyncSession,
    telegram_id: int,
    actor_id: int,
) -> AdminRecord:
    """Soft-removes an admin, preserving the last-superadmin invariant."""
    stmt = select(AdminRecord).where(
        AdminRecord.telegram_id == telegram_id,
        AdminRecord.removed_at.is_(None),
    )
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record is None:
        raise AdminNotFoundError(f"Active admin {telegram_id} not found")

    if record.role == "superadmin":
        active_count = await count_active_superadmins(session)
        if active_count <= 1:
            raise LastSuperadminError("Cannot remove the last active superadmin")

    now = time.time()
    record.removed_at = now
    await record_audit(
        session=session,
        admin_id=actor_id,
        action="admin_remove",
        target=f"admin:{telegram_id}",
        old_val={"role": record.role},
        new_val={"removed_at": now},
    )
    await session.flush()
    return record


async def set_admin_role(
    session: AsyncSession,
    telegram_id: int,
    new_role: str,
    actor_id: int,
) -> AdminRecord:
    """Updates an admin's role, preserving the last-superadmin invariant."""
    stmt = select(AdminRecord).where(
        AdminRecord.telegram_id == telegram_id,
        AdminRecord.removed_at.is_(None),
    )
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record is None:
        raise AdminNotFoundError(f"Active admin {telegram_id} not found")

    if record.role == "superadmin" and new_role != "superadmin":
        active_count = await count_active_superadmins(session)
        if active_count <= 1:
            raise LastSuperadminError("Cannot demote the last active superadmin")

    old_role = record.role
    record.role = new_role
    await record_audit(
        session=session,
        admin_id=actor_id,
        action="admin_role_change",
        target=f"admin:{telegram_id}",
        old_val={"role": old_role},
        new_val={"role": new_role},
    )
    await session.flush()
    return record


async def list_active_admins(session: AsyncSession) -> list[AdminRecord]:
    """Returns all active (non-removed) admins."""
    stmt = select(AdminRecord).where(AdminRecord.removed_at.is_(None)).order_by(AdminRecord.id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_admin(session: AsyncSession, telegram_id: int) -> AdminRecord | None:
    """Returns active admin record if exists."""
    stmt = select(AdminRecord).where(
        AdminRecord.telegram_id == telegram_id,
        AdminRecord.removed_at.is_(None),
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def is_superadmin(session: AsyncSession, telegram_id: int) -> bool:
    """Returns True if the user is an active superadmin."""
    admin = await get_admin(session, telegram_id)
    return admin is not None and admin.role == "superadmin"


async def is_admin_or_superadmin(session: AsyncSession, telegram_id: int) -> bool:
    """Returns True if the user is an active admin or superadmin."""
    admin = await get_admin(session, telegram_id)
    return admin is not None


# --- Invite links (R48) ---


def hash_token(token: str) -> str:
    """Computes SHA-256 hash of invite token."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


async def create_invite(
    session: AsyncSession,
    created_by: int,
    role: str = "admin",
    ttl_hours: float = 48.0,
) -> tuple[str, AdminInviteRecord]:
    """Creates a single-use expiring admin invite link."""
    raw_token = secrets.token_urlsafe(24)
    token_hash = hash_token(raw_token)
    expires_at = time.time() + (ttl_hours * 3600.0)

    invite = AdminInviteRecord(
        token_hash=token_hash,
        role=role,
        created_by=created_by,
        expires_at=expires_at,
        used_by=None,
        used_at=None,
    )
    session.add(invite)
    await record_audit(
        session=session,
        admin_id=created_by,
        action="admin_invite_create",
        target=f"role:{role}",
        new_val={"expires_at": expires_at},
    )
    await session.flush()
    return raw_token, invite


async def consume_invite(
    session: AsyncSession,
    raw_token: str,
    user_id: int,
) -> tuple[AdminRecord, AdminInviteRecord]:
    """Validates and consumes an invite token, making user an admin."""
    token_hash = hash_token(raw_token)
    stmt = select(AdminInviteRecord).where(AdminInviteRecord.token_hash == token_hash)
    res = await session.execute(stmt)
    invite = res.scalar_one_or_none()

    if invite is None or invite.used_by is not None:
        raise InviteInvalidError("Invite link is invalid or has already been used")

    now = time.time()
    if now > invite.expires_at:
        raise InviteExpiredError("Invite link has expired")

    invite.used_by = user_id
    invite.used_at = now

    admin = await add_or_restore_admin(
        session=session,
        telegram_id=user_id,
        role=invite.role,
        added_by=invite.created_by,
    )

    await record_audit(
        session=session,
        admin_id=user_id,
        action="admin_invite_consume",
        target=f"admin:{user_id}",
        new_val={"role": invite.role, "invited_by": invite.created_by},
    )
    await session.flush()
    return admin, invite
