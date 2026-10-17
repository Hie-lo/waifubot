"""User management panel handlers (R44, R50).

Contract:
- Accessible by all administrators.
- Overview: total, active today, new this week, blocked.
- Paged user list with filters (all, active, blocked, new) and search.
- User card: details, block/unblock, note, language, delete record.
- Promote to admin available for superadmins.
- Admins cannot be blocked from here (R50).
- All actions are audited (R44).
"""

from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import NavCallback, UserCallback
from waifubot.bot.telegram_html import bold, code, escape_html
from waifubot.services.admins import is_superadmin
from waifubot.services.scheduler import format_jalali_datetime
from waifubot.services.users import (
    CannotBlockAdminError,
    block_user,
    delete_user_record,
    get_user,
    get_users_overview,
    list_users,
    set_user_note,
    unblock_user,
)


class UserManagementHandler:
    """Handles Settings -> Users navigation and user card operations."""

    async def render_overview(
        self,
        session: AsyncSession,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders the overview statistics screen."""
        stats = await get_users_overview(session)

        lines = [
            f"👥 {bold('نمای کلی کاربران' if locale == 'fa' else 'Users Overview')}",
            "",
            f"• کل کاربران: {bold(str(stats['total']))}" if locale == "fa" else f"• Total users: {bold(str(stats['total']))}",
            f"• فعال در ۲۴ ساعت گذشته: {bold(str(stats['active_today']))}" if locale == "fa" else f"• Active today: {bold(str(stats['active_today']))}",
            f"• کاربران جدید این هفته: {bold(str(stats['new_this_week']))}" if locale == "fa" else f"• New this week: {bold(str(stats['new_this_week']))}",
            f"• مسدود شده‌ها: {bold(str(stats['blocked']))}" if locale == "fa" else f"• Blocked: {bold(str(stats['blocked']))}",
        ]

        keyboard: list[list[dict[str, Any]]] = [
            [
                {"text": "📋 همه" if locale == "fa" else "📋 All", "callback_data": UserCallback(action="list", extra="all:0").pack()},
                {"text": "✅ فعال" if locale == "fa" else "✅ Active", "callback_data": UserCallback(action="list", extra="active:0").pack()},
            ],
            [
                {"text": "⛔ مسدود" if locale == "fa" else "⛔ Blocked", "callback_data": UserCallback(action="list", extra="blocked:0").pack()},
                {"text": "✨ جدید" if locale == "fa" else "✨ New", "callback_data": UserCallback(action="list", extra="new:0").pack()},
            ],
            [
                {"text": "🔙 بازگشت" if locale == "fa" else "🔙 Back", "callback_data": NavCallback(action="back", target="settings").pack()}
            ],
        ]

        return "\n".join(lines), keyboard

    async def render_user_list(
        self,
        session: AsyncSession,
        filter_type: str = "all",
        page: int = 0,
        limit: int = 5,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders paged list of users with navigation controls."""
        offset = page * limit
        users, total_count = await list_users(
            session=session,
            filter_type=filter_type,
            limit=limit,
            offset=offset,
        )

        total_pages = max(1, (total_count + limit - 1) // limit)
        current_page = page + 1

        title = f"لیست کاربران ({filter_type})" if locale == "fa" else f"Users List ({filter_type})"
        page_indicator = f"صفحه {current_page} از {total_pages}" if locale == "fa" else f"Page {current_page} of {total_pages}"

        lines = [
            f"👥 {bold(title)} — {page_indicator}",
            f"تعداد کل: {total_count}" if locale == "fa" else f"Total matching: {total_count}",
            "",
        ]

        keyboard: list[list[dict[str, Any]]] = []

        for u in users:
            status_icon = "⛔" if u.status == "blocked" else "✅"
            uname = f"@{u.username}" if u.username else u.name
            lines.append(f"{status_icon} {uname} | ID: {code(str(u.telegram_id))}")
            cb = UserCallback(action="card", target_id=u.telegram_id)
            keyboard.append([{"text": f"{status_icon} {uname}", "callback_data": cb.pack()}])

        # Pagination row
        nav_row: list[dict[str, Any]] = []
        if page > 0:
            prev_cb = UserCallback(action="list", extra=f"{filter_type}:{page - 1}")
            nav_row.append({"text": "⬅️ قبلی" if locale == "fa" else "⬅️ Prev", "callback_data": prev_cb.pack()})
        if current_page < total_pages:
            next_cb = UserCallback(action="list", extra=f"{filter_type}:{page + 1}")
            nav_row.append({"text": "بعدی ➡️" if locale == "fa" else "Next ➡️", "callback_data": next_cb.pack()})

        if nav_row:
            keyboard.append(nav_row)

        back_cb = UserCallback(action="ovw", target_id=0)
        keyboard.append([{"text": "🔙 بازگشت به آمار" if locale == "fa" else "🔙 Back to Overview", "callback_data": back_cb.pack()}])

        return "\n".join(lines), keyboard

    async def render_user_card(
        self,
        session: AsyncSession,
        target_tg_id: int,
        viewer_is_superadmin: bool = False,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders comprehensive user card details and actions."""
        user = await get_user(session, target_tg_id)
        if user is None:
            text = "کاربر یافت نشد." if locale == "fa" else "User not found."
            back_cb = UserCallback(action="ovw", target_id=0)
            return text, [[{"text": "🔙 بازگشت" if locale == "fa" else "🔙 Back", "callback_data": back_cb.pack()}]]

        first_str = format_jalali_datetime(user.first_seen) if locale == "fa" else str(user.first_seen)
        last_str = format_jalali_datetime(user.last_seen) if locale == "fa" else str(user.last_seen)

        status_text = "⛔ مسدود" if user.status == "blocked" else "✅ فعال"
        if locale != "fa":
            status_text = "⛔ Blocked" if user.status == "blocked" else "✅ Active"

        lines = [
            f"👤 {bold('پروفایل کاربر' if locale == 'fa' else 'User Profile')}",
            f"• شناسه: {code(str(user.telegram_id))}",
            f"• نام: {escape_html(user.name)}",
            f"• نام کاربری: @{escape_html(user.username)}" if user.username else "• نام کاربری: -",
            f"• وضعیت: {bold(status_text)}",
            f"• اولین بازدید: {first_str}",
            f"• آخرین فعالیت: {last_str}",
            f"• زبان: {user.locale}",
        ]
        if user.block_reason:
            lines.append(f"• علت مسدودسازی: {escape_html(user.block_reason)}")
        if user.note:
            lines.append(f"• یادداشت مدیر: {escape_html(user.note)}")

        keyboard: list[list[dict[str, Any]]] = []

        # Block/Unblock button
        if user.status == "blocked":
            unblock_cb = UserCallback(action="unblock", target_id=user.telegram_id)
            keyboard.append([{"text": "🔓 رفع مسدودیت" if locale == "fa" else "🔓 Unblock", "callback_data": unblock_cb.pack()}])
        else:
            block_cb = UserCallback(action="block", target_id=user.telegram_id)
            keyboard.append([{"text": "⛔ مسدود کردن کاربر" if locale == "fa" else "⛔ Block User", "callback_data": block_cb.pack()}])

        # Delete user record button
        del_cb = UserCallback(action="del", target_id=user.telegram_id)
        keyboard.append([{"text": "🗑️ حذف رکورد کاربر" if locale == "fa" else "🗑️ Delete Record", "callback_data": del_cb.pack()}])

        # Superadmin promote button (R50: promote to admin hands over to R48 flow)
        if viewer_is_superadmin:
            promote_cb = UserCallback(action="promote", target_id=user.telegram_id)
            keyboard.append([{"text": "👑 ارتقا به مدیر (R48)" if locale == "fa" else "👑 Promote to Admin", "callback_data": promote_cb.pack()}])

        # Back button
        back_cb = UserCallback(action="list", extra="all:0")
        keyboard.append([{"text": "🔙 بازگشت به لیست" if locale == "fa" else "🔙 Back to List", "callback_data": back_cb.pack()}])

        return "\n".join(lines), keyboard

    async def handle_block_toggle(
        self,
        session: AsyncSession,
        actor_id: int,
        target_tg_id: int,
        reason: str | None = None,
    ) -> tuple[bool, str]:
        """Toggles user block status, preventing admin blocking (R50)."""
        user = await get_user(session, target_tg_id)
        if user is None:
            return False, "کاربر یافت نشد."

        try:
            if user.status == "blocked":
                await unblock_user(session, target_tg_id, admin_id=actor_id)
                return True, "کاربر با موفقیت رفع مسدودیت شد."
            else:
                await block_user(session, target_tg_id, reason=reason, admin_id=actor_id)
                return True, "کاربر با موفقیت مسدود شد."
        except CannotBlockAdminError:
            return False, "امکان مسدودسازی مدیر فعال وجود ندارد! ابتدا او را از بخش ادمین‌ها عزل کنید."
        except Exception as e:
            return False, f"خطا در تغییر وضعیت کاربر: {e}"

    async def handle_delete_user(
        self,
        session: AsyncSession,
        actor_id: int,
        target_tg_id: int,
    ) -> tuple[bool, str]:
        """Deletes user record profile."""
        success = await delete_user_record(session, target_tg_id, admin_id=actor_id)
        if success:
            return True, "رکورد کاربر با موفقیت حذف گردید."
        return False, "کاربر یافت نشد."
