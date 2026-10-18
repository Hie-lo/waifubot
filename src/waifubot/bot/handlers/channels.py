"""Channel management panel handler for Settings -> Channels and My Channels (R56–R59).

Contract:
- Staff view all shared and private channels in Settings -> Channels.
- Users view their own verified channels in My Channels (R59).
- Single channel card: re-verify now, toggle scope (private <-> shared), enable/disable, delete.
- Active schedule guard on channel removal (R58).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import ChannelCallback, NavCallback
from waifubot.bot.telegram_html import bold, code, escape_html
from waifubot.core.limits import Limits
from waifubot.core.ports import ChannelProbe
from waifubot.db.models import ChannelRecord
from waifubot.services.channels import (
    ChannelVerificationError,
    delete_channel,
    get_channel,
    list_channels,
    verify_channel,
)
from waifubot.services.scheduler import format_jalali_datetime


class ChannelsHandler:
    """Handles channel registration, card operations, and UI rendering."""

    async def render_channels_list(
        self,
        session: AsyncSession,
        requester_user_id: int,
        requester_role: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders list of channels available to the requester."""
        is_staff = requester_role in ("admin", "superadmin")

        if is_staff:
            channels = await list_channels(session)
            title = "مدیریت کانال‌ها و گروه‌ها" if locale == "fa" else "Channel & Group Management"
            back_target = "settings"
        else:
            channels = await list_channels(session, owner_user_id=requester_user_id)
            title = "کانال‌های من" if locale == "fa" else "My Channels"
            back_target = "account"

        lines = [f"📢 {bold(title)}", ""]

        if not channels:
            empty_msg = (
                "هیچ کانال یا گروهی ثبت نشده است."
                if locale == "fa"
                else "No channels or groups registered yet."
            )
            lines.append(empty_msg)

        keyboard: list[list[dict[str, Any]]] = []

        for ch in channels:
            status_icon = (
                "✅" if ch.status == "active" else ("⚠️" if ch.status == "lost_access" else "⏸")
            )
            scope_icon = "🌐" if ch.scope == "shared" else "🔒"
            btn_text = f"{status_icon} {scope_icon} {ch.title}"
            cb = ChannelCallback(action="view", channel_id=ch.id)
            keyboard.append([{"text": btn_text, "callback_data": cb.pack()}])

        # Add channel button
        add_cb = ChannelCallback(action="add")
        keyboard.append(
            [
                {
                    "text": "➕ ثبت کانال جدید" if locale == "fa" else "➕ Add New Channel",
                    "callback_data": add_cb.pack(),
                }
            ]
        )

        # Back button
        back_cb = NavCallback(action="back", target=back_target)
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                    "callback_data": back_cb.pack(),
                }
            ]
        )

        return "\n".join(lines), keyboard

    async def render_channel_card(
        self,
        session: AsyncSession,
        channel_id: int,
        requester_user_id: int,
        requester_role: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders detailed card for a single channel."""
        channel = await get_channel(session, channel_id)
        if channel is None:
            text = "کانال یافت نشد." if locale == "fa" else "Channel not found."
            back_cb = ChannelCallback(action="list")
            return text, [
                [
                    {
                        "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        is_owner_or_super = (
            requester_role == "superadmin" or channel.owner_user_id == requester_user_id
        )

        # Status badge
        status_map = {
            "active": "✅ فعال" if locale == "fa" else "✅ Active",
            "disabled": "⏸ غیرفعال" if locale == "fa" else "⏸ Disabled",
            "lost_access": "⚠️ قطعی دسترسی" if locale == "fa" else "⚠️ Lost Access",
            "legacy_unverified": "❓ قدیمی (نیازمند بررسی)"
            if locale == "fa"
            else "❓ Legacy Unverified",
        }
        status_str = status_map.get(channel.status, channel.status)
        scope_str = "🌐 عمومی/اشتراکی" if channel.scope == "shared" else "🔒 خصوصی"
        if locale != "fa":
            scope_str = "🌐 Shared" if channel.scope == "shared" else "🔒 Private"

        ver_date = (
            format_jalali_datetime(channel.verified_at)
            if channel.verified_at and locale == "fa"
            else str(channel.verified_at or "-")
        )

        lines = [
            f"📢 {bold(channel.title)}",
            f"• شناسه عددی: {code(str(channel.tg_chat_id))}",
            f"• یوزرنیم: @{escape_html(channel.username)}" if channel.username else "• یوزرنیم: -",
            f"• وضعیت: {bold(status_str)}",
            f"• دسترسی: {scope_str}",
            f"• نقش ثبت‌کننده در چت: {channel.adder_role_in_chat}",
            f"• تاریخ راستی‌آزمایی: {ver_date}",
        ]

        keyboard: list[list[dict[str, Any]]] = []

        # Re-verify button (R58)
        reverify_cb = ChannelCallback(action="reverify", channel_id=channel.id)
        keyboard.append(
            [
                {
                    "text": "🔄 راستی‌آزمایی مجدد" if locale == "fa" else "🔄 Re-verify Now",
                    "callback_data": reverify_cb.pack(),
                }
            ]
        )

        # Scope toggle button (R59: only owner or superadmin)
        if is_owner_or_super:
            new_scope = "private" if channel.scope == "shared" else "shared"
            scope_btn_label = (
                ("🔒 تغییر به خصوصی" if new_scope == "private" else "🌐 تغییر به اشتراکی")
                if locale == "fa"
                else ("🔒 Make Private" if new_scope == "private" else "🌐 Make Shared")
            )
            scope_cb = ChannelCallback(action="scope", channel_id=channel.id, extra=new_scope)
            keyboard.append([{"text": scope_btn_label, "callback_data": scope_cb.pack()}])

        # Toggle Active / Disabled
        new_status = "active" if channel.status == "disabled" else "disabled"
        status_btn_label = (
            ("▶️ فعال‌سازی" if new_status == "active" else "⏸ غیرفعال‌سازی")
            if locale == "fa"
            else ("▶️ Enable" if new_status == "active" else "⏸ Disable")
        )
        toggle_cb = ChannelCallback(action="toggle", channel_id=channel.id, extra=new_status)
        keyboard.append([{"text": status_btn_label, "callback_data": toggle_cb.pack()}])

        # Delete button (R58: schedule guard)
        if is_owner_or_super:
            del_cb = ChannelCallback(action="del", channel_id=channel.id)
            keyboard.append(
                [
                    {
                        "text": "🗑️ حذف کانال" if locale == "fa" else "🗑️ Delete Channel",
                        "callback_data": del_cb.pack(),
                    }
                ]
            )

        # Back button
        back_cb = ChannelCallback(action="list")
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                    "callback_data": back_cb.pack(),
                }
            ]
        )

        return "\n".join(lines), keyboard

    async def execute_add_channel(
        self,
        session: AsyncSession,
        probe: ChannelProbe,
        chat_ref: str | int,
        adder_user_id: int,
        adder_role: str,
        limits: Limits | None = None,
    ) -> tuple[bool, str, ChannelRecord | None]:
        """Validates and registers a new channel (R56, R57)."""
        try:
            channel = await verify_channel(
                session=session,
                probe=probe,
                chat_ref=chat_ref,
                adder_user_id=adder_user_id,
                adder_role=adder_role,
                limits=limits,
            )
            return True, "success", channel
        except ChannelVerificationError as e:
            return False, e.reason, None
        except Exception as e:
            return False, str(e), None

    async def execute_delete_channel(
        self,
        session: AsyncSession,
        channel_id: int,
        requester_user_id: int,
        requester_role: str,
        force: bool = False,
    ) -> tuple[bool, str]:
        """Deletes a channel with schedule safety guard (R58)."""
        try:
            await delete_channel(
                session=session,
                channel_id=channel_id,
                requester_user_id=requester_user_id,
                requester_role=requester_role,
                force=force,
            )
            return True, "deleted"
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)
