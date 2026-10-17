"""Telegram /status command formatting and handler (R46)."""

import time
from typing import Any

from waifubot.bot.telegram_html import bold, code
from waifubot.services.scheduler import format_jalali_datetime


def format_status_message(status: dict[str, Any], locale: str = "fa") -> str:
    """Formats collected status metrics into an informative HTML message for Telegram."""
    uptime_str = status["uptime_formatted"]
    last_restart_str = (
        format_jalali_datetime(status["last_restart_at"])
        if locale == "fa"
        else time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(status["last_restart_at"]))
    )

    wc = status["worker_config"]
    config_summary = f"W:{wc['worker_count']} | J:{wc['max_concurrent_jobs']} | B:{wc['max_browser_contexts']} | P:{wc['provider_concurrency']}"

    if locale == "fa":
        lines = [
            f"📊 {bold('وضعیت سلامت ربات (Status)')}",
            "",
            f"⏱️ زمان فعالیت (Uptime): {code(uptime_str)}",
            f"🔄 آخرین راه‌اندازی: {last_restart_str}",
            "",
            f"📥 عمق صف پردازش: {bold(str(status['queue_depth']))} کار در صف",
            f"⚙️ کارهای در حال اجرا: {bold(str(status['running_jobs']))}",
            f"🎰 اسلات‌های فعال ارائه‌دهنده: {bold(str(status['running_slots']))}",
            f"🚀 همزمانی موثر: {bold(str(status['effective_concurrency']))}",
            f"💾 رم آزاد سیستم: {code(f'{status["free_ram_mb"]} MB')}",
            f"🛠️ پیکربندی کارگرها: {code(config_summary)}",
        ]

        errors = status.get("last_errors", [])
        if errors:
            lines.append("")
            lines.append(f"⚠️ {bold('آخرین خطاهای ثبت‌شده:')}")
            for err in errors[-3:]:
                msg = err.get("msg", "Unknown error")[:60]
                lines.append(f"• [{err.get('logger', 'root')}] {code(msg)}")
        else:
            lines.append("")
            lines.append("✅ هیچ خطای فعالی در بافر ثبت نشده است.")
    else:
        lines = [
            f"📊 {bold('Bot Health & Status')}",
            "",
            f"⏱️ Uptime: {code(uptime_str)}",
            f"🔄 Last Restart: {last_restart_str}",
            "",
            f"📥 Queue depth: {bold(str(status['queue_depth']))} jobs",
            f"⚙️ Running jobs: {bold(str(status['running_jobs']))}",
            f"🎰 Active provider slots: {bold(str(status['running_slots']))}",
            f"🚀 Effective concurrency: {bold(str(status['effective_concurrency']))}",
            f"💾 Free RAM: {code(f'{status["free_ram_mb"]} MB')}",
            f"🛠️ Worker configuration: {code(config_summary)}",
        ]

        errors = status.get("last_errors", [])
        if errors:
            lines.append("")
            lines.append(f"⚠️ {bold('Recent errors:')}")
            for err in errors[-3:]:
                msg = err.get("msg", "Unknown error")[:60]
                lines.append(f"• [{err.get('logger', 'root')}] {code(msg)}")
        else:
            lines.append("")
            lines.append("✅ No recent errors logged.")

    return "\n".join(lines)
