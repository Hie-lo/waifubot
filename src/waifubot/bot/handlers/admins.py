"""Admin management panel handlers (R44, R48).

Contract:
- Accessible exclusively by superadmins.
- In-place panel navigation with back/close (R3, R4).
- Preserves the last-superadmin invariant.
- One-time expiring invite link generation.
- Audits all actions (R44).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import AdminCallback, NavCallback
from waifubot.bot.telegram_html import bold, code
from waifubot.services.admins import (
    LastSuperadminError,
    create_invite,
    get_admin,
    list_active_admins,
    remove_admin,
    set_admin_role,
)
from waifubot.services.scheduler import format_jalali_datetime


class AdminManagementHandler:
    """Manages the Settings -> Admins panel interactions."""

    def __init__(self, bot_username: str = "waifu_bot") -> None:
        self.bot_username = bot_username

    async def render_admin_list(
        self,
        session: AsyncSession,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders the list of active administrators with role badges."""
        admins = await list_active_admins(session)

        lines = [
            f"👑 {bold('مدیریت ادمین‌ها' if locale == 'fa' else 'Admin Management')}",
            "",
            "لیست ادمین‌های فعال:" if locale == "fa" else "Active administrators:",
        ]

        keyboard: list[list[dict[str, Any]]] = []

        for adm in admins:
            badge = "👑 سوپرادمین" if adm.role == "superadmin" else "🛡️ ادمین"
            if locale != "fa":
                badge = "👑 Superadmin" if adm.role == "superadmin" else "🛡️ Admin"

            lines.append(f"• {badge} | ID: {code(str(adm.telegram_id))}")
            cb = AdminCallback(action="card", target_id=adm.telegram_id)
            keyboard.append([{"text": f"{badge} - {adm.telegram_id}", "callback_data": cb.pack()}])

        # Action buttons
        invite_cb = AdminCallback(action="invite_menu", target_id=0)
        keyboard.append(
            [
                {
                    "text": "➕ ساخت لینک دعوت" if locale == "fa" else "➕ Generate Invite Link",
                    "callback_data": invite_cb.pack(),
                }
            ]
        )

        back_cb = NavCallback(action="back", target="settings")
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                    "callback_data": back_cb.pack(),
                }
            ]
        )

        return "\n".join(lines), keyboard

    async def render_admin_card(
        self,
        session: AsyncSession,
        target_tg_id: int,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders details and management actions for a specific administrator."""
        admin = await get_admin(session, target_tg_id)
        if admin is None:
            text = (
                "ادمین یافت نشد یا حذف شده است."
                if locale == "fa"
                else "Admin not found or removed."
            )
            back_cb = AdminCallback(action="list", target_id=0)
            return text, [
                [
                    {
                        "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        created_str = (
            format_jalali_datetime(admin.created_at) if locale == "fa" else str(admin.created_at)
        )
        role_title = "سوپرادمین (دسترسی نامحدود)" if admin.role == "superadmin" else "ادمین"
        if locale != "fa":
            role_title = "Superadmin (unlimited access)" if admin.role == "superadmin" else "Admin"

        lines = [
            f"👤 {bold('کارت ادمین' if locale == 'fa' else 'Admin Card')}",
            f"• شناسه: {code(str(admin.telegram_id))}",
            f"• نقش: {bold(role_title)}",
            f"• افزوده شده توسط: {code(str(admin.added_by or 'سیستم/پیکربندی'))}",
            f"• تاریخ ایجاد: {created_str}",
        ]

        keyboard: list[list[dict[str, Any]]] = []

        # Role toggle button (promote/demote)
        if admin.role == "admin":
            promote_cb = AdminCallback(
                action="role", target_id=admin.telegram_id, extra="superadmin"
            )
            keyboard.append(
                [
                    {
                        "text": "👑 ارتقا به سوپرادمین"
                        if locale == "fa"
                        else "👑 Promote to Superadmin",
                        "callback_data": promote_cb.pack(),
                    }
                ]
            )
        else:
            demote_cb = AdminCallback(action="role", target_id=admin.telegram_id, extra="admin")
            keyboard.append(
                [
                    {
                        "text": "🛡️ تنزل به ادمین عادی" if locale == "fa" else "🛡️ Demote to Admin",
                        "callback_data": demote_cb.pack(),
                    }
                ]
            )

        # Remove button
        remove_cb = AdminCallback(action="remove", target_id=admin.telegram_id)
        keyboard.append(
            [
                {
                    "text": "❌ حذف ادمین" if locale == "fa" else "❌ Remove Admin",
                    "callback_data": remove_cb.pack(),
                }
            ]
        )

        # Back button
        back_cb = AdminCallback(action="list", target_id=0)
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت به لیست" if locale == "fa" else "🔙 Back to List",
                    "callback_data": back_cb.pack(),
                }
            ]
        )

        return "\n".join(lines), keyboard

    async def handle_role_change(
        self,
        session: AsyncSession,
        actor_id: int,
        target_tg_id: int,
        new_role: str,
    ) -> tuple[bool, str]:
        """Executes promote or demote while enforcing the last-superadmin invariant."""
        try:
            await set_admin_role(
                session=session,
                telegram_id=target_tg_id,
                new_role=new_role,
                actor_id=actor_id,
            )
            return True, "نقش ادمین با موفقیت تغییر یافت."
        except LastSuperadminError:
            return False, "امکان تنزل آخرین سوپرادمین فعال وجود ندارد!"
        except Exception as e:
            return False, f"خطا در تغییر نقش: {e}"

    async def handle_remove_admin(
        self,
        session: AsyncSession,
        actor_id: int,
        target_tg_id: int,
    ) -> tuple[bool, str]:
        """Soft-removes an admin while enforcing the last-superadmin invariant."""
        try:
            await remove_admin(
                session=session,
                telegram_id=target_tg_id,
                actor_id=actor_id,
            )
            return True, "ادمین با موفقیت حذف شد."
        except LastSuperadminError:
            return False, "امکان حذف آخرین سوپرادمین فعال وجود ندارد!"
        except Exception as e:
            return False, f"خطا در حذف ادمین: {e}"

    async def create_invite_link(
        self,
        session: AsyncSession,
        actor_id: int,
        role: str = "admin",
        ttl_hours: float = 48.0,
    ) -> tuple[str, str]:
        """Generates a single-use expiring invite link."""
        raw_token, _ = await create_invite(
            session=session,
            created_by=actor_id,
            role=role,
            ttl_hours=ttl_hours,
        )
        link = f"https://t.me/{self.bot_username}?start=adm_{raw_token}"
        return raw_token, link
