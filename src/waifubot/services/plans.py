"""Plan management and user limit resolution service (R52, R53, §3.6).

Contract:
- Plans are named bundles of limits.
- Admins create, edit and delete plans in Settings -> Plans.
- Built-in plans ('free' and 'staff') cannot be deleted.
- Deleting a plan with users requires specifying a replacement plan.
- User limit overrides on the user card take precedence over plan limits.
- Superadmin has no limits (short-circuited in core/limits.py via effective_limits).
- Plan expiry reverts user to default_plan_id ('free').
- All plan operations are audited (R44).
"""

import json
import time
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.limits import Limits, effective_limits
from waifubot.db.models import PlanRecord, UserRecord
from waifubot.services.audit import record_audit


class PlanNotFoundError(Exception):
    """Raised when a plan is not found."""


class BuiltinPlanError(Exception):
    """Raised when attempting to delete a built-in plan."""


class InvalidPlanLimitsError(Exception):
    """Raised when plan limits contain invalid keys or values."""


VALID_LIMIT_KEYS = {
    "max_owned_characters",
    "daily_image_budget",
    "max_images_per_job",
    "max_concurrent_jobs",
    "max_stored_images",
    "daily_chat_messages",
    "chat_memory_tokens",
    "max_channels",
    "queue_priority",
}


def validate_limits_dict(limits: dict[str, Any]) -> dict[str, Any]:
    """Validates and sanitizes plan limits dict."""
    cleaned: dict[str, Any] = {}
    for key in VALID_LIMIT_KEYS:
        val = limits.get(key)
        if val is None:
            cleaned[key] = None
        else:
            try:
                cleaned[key] = int(val)
            except (ValueError, TypeError) as err:
                raise InvalidPlanLimitsError(
                    f"Limit '{key}' must be an integer or None, got {val!r}"
                ) from err

    # queue_priority must be an integer (cannot be None)
    if cleaned["queue_priority"] is None:
        cleaned["queue_priority"] = 0

    return cleaned


async def get_plan(session: AsyncSession, plan_id: str) -> PlanRecord | None:
    """Retrieves a plan by its ID."""
    stmt = select(PlanRecord).where(PlanRecord.id == plan_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def list_plans(session: AsyncSession) -> list[PlanRecord]:
    """Lists all available plans, ordered with builtins first."""
    stmt = select(PlanRecord).order_by(PlanRecord.is_builtin.desc(), PlanRecord.id.asc())
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def create_plan(
    session: AsyncSession,
    plan_id: str,
    name: str,
    limits: dict[str, Any],
    created_by: int | None = None,
) -> PlanRecord:
    """Creates a new custom plan (R52)."""
    clean_id = plan_id.strip().lower()
    if not clean_id or not clean_id.replace("_", "").isalnum():
        raise ValueError("Plan ID must be alphanumeric/underscore string")

    existing = await get_plan(session, clean_id)
    if existing is not None:
        raise ValueError(f"Plan with ID '{clean_id}' already exists")

    clean_limits = validate_limits_dict(limits)
    plan = PlanRecord(
        id=clean_id,
        name=name.strip() or clean_id.capitalize(),
        limits_json=json.dumps(clean_limits),
        is_builtin=False,
        created_by=created_by,
        created_at=time.time(),
    )
    session.add(plan)
    await session.flush()

    await record_audit(
        session=session,
        admin_id=created_by or 0,
        action="create_plan",
        target=f"plan:{clean_id}",
        new_val=json.dumps({"name": plan.name, "limits": clean_limits}),
    )
    return plan


async def update_plan(
    session: AsyncSession,
    plan_id: str,
    name: str | None = None,
    limits: dict[str, Any] | None = None,
    admin_id: int | None = None,
) -> PlanRecord:
    """Updates an existing plan's name or limits (R52)."""
    plan = await get_plan(session, plan_id)
    if plan is None:
        raise PlanNotFoundError(f"Plan '{plan_id}' not found")

    old_val = {"name": plan.name, "limits": json.loads(plan.limits_json)}
    if name is not None:
        plan.name = name.strip() or plan.name

    if limits is not None:
        existing_limits = json.loads(plan.limits_json)
        existing_limits.update(limits)
        clean_limits = validate_limits_dict(existing_limits)
        plan.limits_json = json.dumps(clean_limits)

    await session.flush()

    new_val_dict = {"name": plan.name, "limits": json.loads(plan.limits_json)}
    await record_audit(
        session=session,
        admin_id=admin_id or 0,
        action="update_plan",
        target=f"plan:{plan_id}",
        old_val=json.dumps(old_val),
        new_val=json.dumps(new_val_dict),
    )
    return plan


async def delete_plan(
    session: AsyncSession,
    plan_id: str,
    replacement_plan_id: str,
    admin_id: int | None = None,
) -> None:
    """Deletes a custom plan and migrates its users to a replacement plan (R52)."""
    plan = await get_plan(session, plan_id)
    if plan is None:
        raise PlanNotFoundError(f"Plan '{plan_id}' not found")

    if plan.is_builtin:
        raise BuiltinPlanError(f"Built-in plan '{plan_id}' cannot be deleted")

    if replacement_plan_id == plan_id:
        raise ValueError("Replacement plan cannot be the plan being deleted")

    replacement = await get_plan(session, replacement_plan_id)
    if replacement is None:
        raise PlanNotFoundError(f"Replacement plan '{replacement_plan_id}' does not exist")

    # Migrate any users currently on this plan
    reassign_stmt = (
        update(UserRecord).where(UserRecord.plan_id == plan_id).values(plan_id=replacement_plan_id)
    )
    await session.execute(reassign_stmt)

    # Delete plan
    del_stmt = delete(PlanRecord).where(PlanRecord.id == plan_id)
    await session.execute(del_stmt)
    await session.flush()

    await record_audit(
        session=session,
        admin_id=admin_id or 0,
        action="delete_plan",
        target=f"plan:{plan_id}",
        old_val=json.dumps({"name": plan.name, "replacement": replacement_plan_id}),
    )


async def assign_user_plan(
    session: AsyncSession,
    user_id: int,
    plan_id: str,
    expires_at: float | None = None,
    admin_id: int | None = None,
) -> None:
    """Assigns a plan to a user with optional expiry (R52)."""
    plan = await get_plan(session, plan_id)
    if plan is None:
        raise PlanNotFoundError(f"Plan '{plan_id}' does not exist")

    stmt = select(UserRecord).where(UserRecord.telegram_id == user_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User {user_id} not found")

    old_val = {"plan_id": user.plan_id, "expires_at": user.plan_expires_at}
    user.plan_id = plan_id
    user.plan_expires_at = expires_at
    await session.flush()

    await record_audit(
        session=session,
        admin_id=admin_id or 0,
        action="assign_plan",
        target=f"user:{user_id}",
        old_val=json.dumps(old_val),
        new_val=json.dumps({"plan_id": plan_id, "expires_at": expires_at}),
    )


async def set_user_limit_overrides(
    session: AsyncSession,
    user_id: int,
    overrides: dict[str, Any] | None,
    admin_id: int | None = None,
) -> None:
    """Sets per-user limit overrides on the user card (R52)."""
    stmt = select(UserRecord).where(UserRecord.telegram_id == user_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User {user_id} not found")

    old_overrides = user.limit_overrides_json
    clean_overrides = (
        {k: v for k, v in overrides.items() if k in VALID_LIMIT_KEYS} if overrides else None
    )
    user.limit_overrides_json = json.dumps(clean_overrides) if clean_overrides else None
    await session.flush()

    await record_audit(
        session=session,
        admin_id=admin_id or 0,
        action="override_limits",
        target=f"user:{user_id}",
        old_val=old_overrides,
        new_val=user.limit_overrides_json,
    )


async def get_effective_user_limits(
    session: AsyncSession,
    user_id: int,
    role: str,
    default_plan_id: str = "free",
    now: float | None = None,
) -> tuple[Limits, str]:
    """Calculates effective limits for a user, handling plan expiry and overrides (R52, R53).

    Returns:
        (effective_limits, active_plan_id)
    """
    if role == "superadmin":
        return effective_limits("superadmin"), "staff"

    current_time = now if now is not None else time.time()
    stmt = select(UserRecord).where(UserRecord.telegram_id == user_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()

    plan_id = "staff" if role == "admin" else default_plan_id
    overrides: dict[str, Any] | None = None

    if user is not None:
        plan_id = user.plan_id or plan_id
        # Check plan expiry
        if user.plan_expires_at is not None and user.plan_expires_at < current_time:
            # Plan expired -> revert to default_plan_id
            user.plan_id = default_plan_id
            user.plan_expires_at = None
            plan_id = default_plan_id
            await session.flush()

        if user.limit_overrides_json:
            try:
                overrides = json.loads(user.limit_overrides_json)
            except Exception:
                overrides = None

    # Load plan record
    plan = await get_plan(session, plan_id)
    plan_limits: dict[str, Any] = {}
    if plan is not None:
        try:
            plan_limits = json.loads(plan.limits_json)
        except Exception:
            plan_limits = {}

    return effective_limits(role, plan_limits, overrides), plan_id
