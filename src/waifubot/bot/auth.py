"""Authorization, blocked-user middleware, and RequestContext (R44, R48, R50, R51, R52, R53).

Contract:
- Role, block status, plan, limits, and locale are derived once into RequestContext (AGENT.md §2).
- Blocked users receive a single `blocked_notice` per session/restart and are otherwise ignored (R50).
- Admins cannot be blocked from user management (R50).
- Superadmins have role='superadmin', admins have role='admin', everyone else is role='user'.
- Role-based reply keyboards: user never sees staff sections (R51).
- Staff-only callbacks answer not_authorized for non-staff users (R27, R51).
"""

import contextlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.i18n import t
from waifubot.core.limits import Limits
from waifubot.services.admins import get_admin
from waifubot.services.plans import get_effective_user_limits
from waifubot.services.users import record_user_seen

_BLOCKED_NOTICED_USERS: set[int] = set()

STAFF_ONLY_CALLBACK_PREFIXES = {
    "adm",  # Admins management
    "usr",  # Users management
    "pln",  # Plans management
    "sys",  # System controls
    "sch",  # Schedules
    "rvw",  # Review
    "pol",  # Pool
    "set",  # Settings
}


@dataclass
class RequestContext:
    """Consolidated request context derived once per Telegram interaction."""

    telegram_id: int
    role: str  # "superadmin" | "admin" | "user"
    is_blocked: bool
    locale: str = "fa"
    username: str | None = None
    name: str | None = None
    plan_id: str = "free"
    limits: Limits | None = None

    @property
    def is_superadmin(self) -> bool:
        """True if requester has superadmin privileges."""
        return self.role == "superadmin"

    @property
    def is_staff(self) -> bool:
        """True if requester is staff (admin or superadmin)."""
        return self.role in ("superadmin", "admin")


def reset_blocked_notices() -> None:
    """Clears in-memory set of noticed blocked users (useful for test resets)."""
    _BLOCKED_NOTICED_USERS.clear()


def is_staff_callback(callback_data: str) -> bool:
    """Returns True if the callback is restricted to staff (R27, R51)."""
    parts = callback_data.split(":", 2)
    if len(parts) >= 2:
        prefix = parts[1]
        return prefix in STAFF_ONLY_CALLBACK_PREFIXES
    return False


def verify_callback_authorization(ctx: RequestContext, callback_data: str) -> bool:
    """Checks whether the requester has permission to execute the given callback (R27, R51)."""
    return not (is_staff_callback(callback_data) and not ctx.is_staff)


def get_role_reply_keyboard_buttons(ctx: RequestContext) -> list[list[str]]:
    """Returns the matrix of reply keyboard button labels for the requester's role (R2, R51)."""
    loc = ctx.locale
    if ctx.is_staff:
        return [
            [t("btn_generate", locale=loc), t("btn_characters", locale=loc)],
            [t("btn_review", locale=loc), t("btn_pool", locale=loc)],
            [t("btn_schedules", locale=loc), t("btn_settings", locale=loc)],
        ]
    else:
        return [
            [t("btn_generate", locale=loc), t("btn_my_images", locale=loc)],
            [t("btn_my_characters", locale=loc), t("btn_chat", locale=loc)],
            [t("btn_my_account", locale=loc)],
        ]


async def resolve_request_context(
    session: AsyncSession,
    telegram_id: int,
    name: str = "",
    username: str | None = None,
    default_locale: str = "fa",
) -> tuple[bool, RequestContext]:
    """Derives RequestContext and checks whether the user is authorized to interact.

    Returns:
        (is_allowed: bool, context: RequestContext)
    """
    user_record, _ = await record_user_seen(
        session=session,
        telegram_id=telegram_id,
        name=name or f"User_{telegram_id}",
        username=username,
        locale=default_locale,
    )

    admin_record = await get_admin(session, telegram_id)
    if admin_record is not None:
        role = admin_record.role
        is_blocked = False
    else:
        role = "user"
        is_blocked = user_record.status == "blocked"

    # Derive effective limits and active plan (R52, R53)
    limits, plan_id = await get_effective_user_limits(
        session=session,
        user_id=telegram_id,
        role=role,
    )

    ctx = RequestContext(
        telegram_id=telegram_id,
        role=role,
        is_blocked=is_blocked,
        locale=user_record.locale,
        username=user_record.username,
        name=user_record.name,
        plan_id=plan_id,
        limits=limits,
    )

    is_allowed = not is_blocked
    return is_allowed, ctx


async def check_access_and_notify(
    session: AsyncSession,
    telegram_id: int,
    name: str = "",
    username: str | None = None,
    bot: Any = None,
) -> tuple[bool, RequestContext]:
    """Evaluates access. If blocked, sends blocked_notice exactly once per session (R50)."""
    is_allowed, ctx = await resolve_request_context(
        session=session,
        telegram_id=telegram_id,
        name=name,
        username=username,
    )

    if not is_allowed:
        if telegram_id not in _BLOCKED_NOTICED_USERS:
            _BLOCKED_NOTICED_USERS.add(telegram_id)
            if bot is not None and hasattr(bot, "send_message"):
                reason = " مسدود شده توسط ادمین"
                msg_text = t("blocked_notice", locale=ctx.locale, reason=reason)
                with contextlib.suppress(Exception):
                    await bot.send_message(chat_id=telegram_id, text=msg_text)

        return False, ctx

    return True, ctx
