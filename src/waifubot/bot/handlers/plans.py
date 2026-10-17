"""Settings -> Plans panel handler (R52, §3.6).

Contract:
- Accessible to administrators (R52).
- Lists all plans (built-ins and custom).
- View plan details and limits.
- Create new plans with custom limits.
- Edit existing plan limits.
- Delete custom plans with mandatory replacement plan for existing users.
- Built-in plans ('free' and 'staff') cannot be deleted.
- All actions are audited (R44).
"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import NavCallback, PlanCallback
from waifubot.bot.telegram_html import bold, code
from waifubot.services.plans import (
    delete_plan,
    get_plan,
    list_plans,
)


class PlansHandler:
    """Handles Settings -> Plans navigation and CRUD operations."""

    async def render_plans_list(
        self,
        session: AsyncSession,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders list of all plans."""
        plans = await list_plans(session)

        title = "مدیریت پلن‌ها و سهمیه‌ها" if locale == "fa" else "Plan & Quota Management"
        lines = [f"📋 {bold(title)}", ""]

        keyboard: list[list[dict[str, Any]]] = []

        for p in plans:
            badge = " [پیش‌فرض]" if p.is_builtin else ""
            if locale != "fa":
                badge = " [Built-in]" if p.is_builtin else ""

            btn_text = f"{p.name} ({p.id}){badge}"
            cb = PlanCallback(action="view", plan_id=p.id)
            keyboard.append([{"text": btn_text, "callback_data": cb.pack()}])

        # Back button to settings
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

    async def render_plan_detail(
        self,
        session: AsyncSession,
        plan_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders detail card for a specific plan."""
        plan = await get_plan(session, plan_id)
        if plan is None:
            text = "پلن یافت نشد." if locale == "fa" else "Plan not found."
            back_cb = PlanCallback(action="list")
            return text, [
                [
                    {
                        "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        limits: dict[str, Any] = json.loads(plan.limits_json)
        lines = [
            f"⭐ {bold(plan.name)} ({code(plan.id)})",
            f"• نوع: {'پیش‌فرض سیستم' if plan.is_builtin else 'شخصی‌سازی‌شده'}"
            if locale == "fa"
            else f"• Type: {'Built-in' if plan.is_builtin else 'Custom'}",
            "",
            bold("سقف محدودیت‌ها:" if locale == "fa" else "Limits:"),
            f"• سهمیه روزانه تصویر: {limits.get('daily_image_budget') or ('نامحدود' if locale == 'fa' else 'Unlimited')}",
            f"• حداکثر تصویر در هر تسک: {limits.get('max_images_per_job') or ('نامحدود' if locale == 'fa' else 'Unlimited')}",
            f"• تسک‌های همزمان: {limits.get('max_concurrent_jobs') or ('نامحدود' if locale == 'fa' else 'Unlimited')}",
            f"• ظرفیت تصاویر من: {limits.get('max_stored_images') or ('نامحدود' if locale == 'fa' else 'Unlimited')}",
            f"• پیام‌های گفتگوی روزانه: {limits.get('daily_chat_messages') or ('نامحدود' if locale == 'fa' else 'Unlimited')}",
            f"• اولویت در صف: {limits.get('queue_priority', 0)}",
        ]

        keyboard: list[list[dict[str, Any]]] = []

        # Delete button (only for custom non-builtin plans)
        if not plan.is_builtin:
            del_cb = PlanCallback(action="del", plan_id=plan.id)
            keyboard.append(
                [
                    {
                        "text": "🗑️ حذف پلن" if locale == "fa" else "🗑️ Delete Plan",
                        "callback_data": del_cb.pack(),
                    }
                ]
            )

        # Back button to plans list
        back_cb = PlanCallback(action="list")
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                    "callback_data": back_cb.pack(),
                }
            ]
        )

        return "\n".join(lines), keyboard

    async def execute_delete_plan(
        self,
        session: AsyncSession,
        plan_id: str,
        replacement_plan_id: str = "free",
        admin_id: int | None = None,
    ) -> bool:
        """Deletes a custom plan with user migration (R52)."""
        await delete_plan(
            session=session,
            plan_id=plan_id,
            replacement_plan_id=replacement_plan_id,
            admin_id=admin_id,
        )
        return True
