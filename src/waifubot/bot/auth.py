"""Authorization, blocked-user middleware, and RequestContext (R44, R48, R50, R51).

Contract:
- Role, block status, and locale are derived once into RequestContext (AGENT.md §2).
- Blocked users receive a single `blocked_notice` per session/restart and are otherwise ignored (R50).
- Admins cannot be blocked from user management (R50).
- Superadmins have role='superadmin', admins have role='admin', everyone else is role='user'.
"""

import contextlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.i18n import t
from waifubot.services.admins import get_admin
from waifubot.services.users import record_user_seen

_BLOCKED_NOTICED_USERS: set[int] = set()


@dataclass
class RequestContext:
    """Consolidated request context derived once per Telegram interaction."""

    telegram_id: int
    role: str  # "superadmin" | "admin" | "user"
    is_blocked: bool
    locale: str = "fa"
    username: str | None = None
    name: str | None = None

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

    ctx = RequestContext(
        telegram_id=telegram_id,
        role=role,
        is_blocked=is_blocked,
        locale=user_record.locale,
        username=user_record.username,
        name=user_record.name,
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
