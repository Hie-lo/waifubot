"""System control panel handler (R49).

Contract:
- Accessible exclusively by superadmins.
- In-place panel navigation (R3, R4).
- Provides worker restart and whole-bot restart with confirmation screen.
- Audits all actions (R44).
"""

from typing import Any

from waifubot.bot.callbacks import NavCallback, SystemCallback
from waifubot.bot.telegram_html import bold


class SystemManagementHandler:
    """Handles Settings -> System panel navigation and confirmation flows."""

    def render_system_menu(self, locale: str = "fa") -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders the Settings -> System main controls menu."""
        if locale == "fa":
            lines = [
                f"⚙️ {bold('کنترل‌های سیستم (System Controls)')}",
                "",
                "عملیات در دسترس برای سوپرادمین:",
                "• 🔄 راه‌اندازی مجدد کارگرها: بدون قطعی ربات، استخر پردازش و مرورگر را بازسازی می‌کند.",
                "• 🔁 راه‌اندازی مجدد ربات: پس از تخلیه امن کارهای جاری، پروسه بات را با کد ۷۵ ریستارت می‌کند.",
            ]

            keyboard = [
                [
                    {
                        "text": "🔄 راه‌اندازی مجدد کارگرها",
                        "callback_data": SystemCallback(action="restart_workers").pack(),
                    }
                ],
                [
                    {
                        "text": "🔁 راه‌اندازی مجدد کامل ربات",
                        "callback_data": SystemCallback(action="confirm_bot_restart").pack(),
                    }
                ],
                [
                    {
                        "text": "🔙 بازگشت",
                        "callback_data": NavCallback(action="back", target="settings").pack(),
                    }
                ],
            ]
        else:
            lines = [
                f"⚙️ {bold('System Controls')}",
                "",
                "Superadmin operational controls:",
                "• 🔄 Restart Workers: Gracefully re-creates worker pool and browser contexts without bot downtime.",
                "• 🔁 Restart Bot: Drains running jobs and exits with code 75 for supervisor restart.",
            ]

            keyboard = [
                [
                    {
                        "text": "🔄 Restart Workers",
                        "callback_data": SystemCallback(action="restart_workers").pack(),
                    }
                ],
                [
                    {
                        "text": "🔁 Restart Bot",
                        "callback_data": SystemCallback(action="confirm_bot_restart").pack(),
                    }
                ],
                [
                    {
                        "text": "🔙 Back",
                        "callback_data": NavCallback(action="back", target="settings").pack(),
                    }
                ],
            ]

        return "\n".join(lines), keyboard

    def render_restart_confirm(self, locale: str = "fa") -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders confirmation screen prior to executing whole-bot restart."""
        if locale == "fa":
            lines = [
                f"⚠️ {bold('تأیید راه‌اندازی مجدد ربات')}",
                "",
                "آیا از ریستارت کامل ربات اطمینان دارید؟",
                "• ربات کار‌های در حال اجرا را به پایان رسانده یا متوقف می‌کند.",
                "• پروسه با کد خروج ۷۵ خاتمه یافته و ناظر (supervisor) آن را مجدداً اجرا خواهد کرد.",
                "• پس از بازگشت، این پیام به وضعیت جدید ویرایش خواهد شد.",
            ]

            keyboard = [
                [
                    {
                        "text": "✅ بله، ربات ریستارت شود",
                        "callback_data": SystemCallback(action="exec_bot_restart").pack(),
                    }
                ],
                [
                    {
                        "text": "❌ انصراف",
                        "callback_data": SystemCallback(action="menu").pack(),
                    }
                ],
            ]
        else:
            lines = [
                f"⚠️ {bold('Confirm Bot Restart')}",
                "",
                "Are you sure you want to restart the bot process?",
                "• Running jobs will be gracefully drained or paused.",
                "• Process will exit with code 75 and the supervisor will restart it.",
                "• After restart, this panel will update to 'restarted ✅'.",
            ]

            keyboard = [
                [
                    {
                        "text": "✅ Yes, restart bot",
                        "callback_data": SystemCallback(action="exec_bot_restart").pack(),
                    }
                ],
                [
                    {
                        "text": "❌ Cancel",
                        "callback_data": SystemCallback(action="menu").pack(),
                    }
                ],
            ]

        return "\n".join(lines), keyboard
