"""User self-service: generation, private My Images viewer, and My Account (R5, R51, R54).

Contract:
- User generation: private results, subject to plan quotas.
- Quotas checked and reserved at job creation; settled at exact-count gate.
- My Images: private gallery with pagination and single-image deletion (R5, R51).
- Deleting an image decrements stored count, freeing quota under max_stored_images.
- My Account: displays active plan, today's usage, limits, and reset time in Tehran timezone.
"""

import contextlib
import os
import time
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import AccountCallback, MyImagesCallback, NavCallback
from waifubot.bot.telegram_html import bold, code, escape_html
from waifubot.core.limits import Limits
from waifubot.db.models import ImageRecord, JobRecord
from waifubot.services.plans import get_effective_user_limits
from waifubot.services.quota import (
    check_and_reserve,
    get_user_usage_summary,
    refund_reservation,
    settle_reservation,
)
from waifubot.services.scheduler import format_jalali_datetime


class UserGenerationHandler:
    """Handles private user generation jobs, My Images viewer, and My Account screens."""

    async def create_user_job(
        self,
        session: AsyncSession,
        user_id: int,
        prompt: str,
        target_count: int,
        role: str = "user",
        limits: Limits | None = None,
        negative_prompt: str = "",
    ) -> JobRecord:
        """Atomically checks quota and creates a private generation job (R51, R54)."""
        if limits is None:
            limits, _ = await get_effective_user_limits(session, user_id, role)

        # Check and reserve quota
        await check_and_reserve(
            session=session,
            user_id=user_id,
            role=role,
            limits=limits,
            kind="generation",
            amount=target_count,
        )

        job_id = f"job_usr_{user_id}_{int(time.time() * 1000)}"
        job = JobRecord(
            job_id=job_id,
            requester_user_id=user_id,
            admin_id=user_id,
            job_scope="user",
            prompt=prompt,
            negative_prompt=negative_prompt,
            target_count=target_count,
            needed=target_count,
            status="pending",
            queue_priority=limits.queue_priority,
            created_at=time.time(),
            updated_at=time.time(),
        )
        session.add(job)
        await session.flush()
        return job

    async def on_job_completed(
        self,
        session: AsyncSession,
        job: JobRecord,
        role: str = "user",
        kept_count: int = 0,
    ) -> None:
        """Settles or refunds generation reservation based on job outcome (R54)."""
        if job.status == "succeeded":
            await settle_reservation(
                session=session,
                user_id=job.requester_user_id,
                role=role,
                reserved_amount=job.target_count,
                actual_amount=kept_count or job.target_count,
            )
        elif job.status in ("failed", "cancelled"):
            await refund_reservation(
                session=session,
                user_id=job.requester_user_id,
                role=role,
                amount=job.target_count,
            )

    async def list_user_images(
        self,
        session: AsyncSession,
        user_id: int,
        page: int = 0,
    ) -> tuple[ImageRecord | None, int]:
        """Returns the image at offset `page` and total count of stored images."""
        count_stmt = (
            select(func.count(ImageRecord.id))
            .join(JobRecord, ImageRecord.job_id == JobRecord.job_id)
            .where(JobRecord.requester_user_id == user_id)
        )
        total = int((await session.execute(count_stmt)).scalar_one() or 0)
        if total == 0:
            return None, 0

        # Clamp page
        page = max(0, min(page, total - 1))
        stmt = (
            select(ImageRecord)
            .join(JobRecord, ImageRecord.job_id == JobRecord.job_id)
            .where(JobRecord.requester_user_id == user_id)
            .order_by(desc(ImageRecord.created_at))
            .offset(page)
            .limit(1)
        )
        res = await session.execute(stmt)
        image = res.scalar_one_or_none()
        return image, total

    async def render_my_images_viewer(
        self,
        session: AsyncSession,
        user_id: int,
        page: int = 0,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]], str | None]:
        """Renders private My Images viewer with inline navigation and deletion (R5, R51)."""
        img, total = await self.list_user_images(session, user_id, page)
        if img is None or total == 0:
            msg = (
                "شما هنوز هیچ تصویری تولید نکرده‌اید."
                if locale == "fa"
                else "You have not generated any images yet."
            )
            keyboard: list[list[dict[str, Any]]] = [
                [
                    {
                        "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                        "callback_data": NavCallback(action="close").pack(),
                    }
                ]
            ]
            return msg, keyboard, None

        date_str = format_jalali_datetime(img.created_at) if locale == "fa" else str(img.created_at)
        lines = [
            f"🖼 {bold('تصاویر من' if locale == 'fa' else 'My Images')} ({page + 1}/{total})",
            f"• تاریخ: {date_str}",
            f"• پرامپت: {escape_html(img.final_prompt[:120])}...",
        ]

        nav_row: list[dict[str, Any]] = []
        if page > 0:
            prev_cb = MyImagesCallback(action="view", img_id=img.id, page=page - 1)
            nav_row.append(
                {"text": "◀️ قبلی" if locale == "fa" else "◀️ Prev", "callback_data": prev_cb.pack()}
            )

        nav_row.append({"text": f"{page + 1} / {total}", "callback_data": "noop"})

        if page < total - 1:
            next_cb = MyImagesCallback(action="view", img_id=img.id, page=page + 1)
            nav_row.append(
                {"text": "بعدی ▶️" if locale == "fa" else "Next ▶️", "callback_data": next_cb.pack()}
            )

        del_cb = MyImagesCallback(action="del", img_id=img.id, page=page)
        del_row = [
            {
                "text": "🗑️ حذف تصویر" if locale == "fa" else "🗑️ Delete Image",
                "callback_data": del_cb.pack(),
            }
        ]
        close_row = [
            {
                "text": "❌ بستن" if locale == "fa" else "❌ Close",
                "callback_data": NavCallback(action="close").pack(),
            }
        ]

        keyboard = [nav_row, del_row, close_row]
        return "\n".join(lines), keyboard, img.file_path

    async def delete_user_image(
        self,
        session: AsyncSession,
        user_id: int,
        image_id: int,
    ) -> bool:
        """Deletes an image owned by the user, freeing stored image quota (R51)."""
        stmt = (
            select(ImageRecord)
            .join(JobRecord, ImageRecord.job_id == JobRecord.job_id)
            .where(JobRecord.requester_user_id == user_id, ImageRecord.id == image_id)
        )
        res = await session.execute(stmt)
        img = res.scalar_one_or_none()
        if img is None:
            return False

        # Attempt to unlink physical file if exists
        if img.file_path and os.path.exists(img.file_path):
            with contextlib.suppress(Exception):
                os.remove(img.file_path)

        del_stmt = delete(ImageRecord).where(ImageRecord.id == image_id)
        await session.execute(del_stmt)
        await session.flush()
        return True

    async def render_my_account(
        self,
        session: AsyncSession,
        user_id: int,
        role: str = "user",
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders My Account screen showing active plan, usage, and reset times (R51)."""
        limits, plan_id = await get_effective_user_limits(session, user_id, role)
        summary = await get_user_usage_summary(session, user_id)

        reset_str = (
            format_jalali_datetime(summary["reset_at"])
            if locale == "fa"
            else str(summary["reset_at"])
        )

        def _fmt_lim(val: int | None) -> str:
            if val is None:
                return "نامحدود" if locale == "fa" else "Unlimited"
            return str(val)

        lines = [
            f"👤 {bold('حساب کاربری من' if locale == 'fa' else 'My Account')}",
            f"• شناسه: {code(str(user_id))}",
            f"• پلن فعال: {bold(plan_id.upper())}",
            "",
            bold("میزان مصرف و سهمیه‌ها:" if locale == "fa" else "Usage & Quotas:"),
            f"• تصاویر امروز: {bold(str(summary['images_today']))} / {_fmt_lim(limits.daily_image_budget)}",
            f"• تسک‌های امروز: {bold(str(summary['jobs_today']))}",
            f"• تصاویر ذخیره‌شده: {bold(str(summary['stored_images']))} / {_fmt_lim(limits.max_stored_images)}",
            f"• پیام‌های گفتگو امروز: {bold(str(summary['chat_messages_today']))} / {_fmt_lim(limits.daily_chat_messages)}",
            f"• کاراکترهای شما: {bold(str(summary['owned_characters']))} / {_fmt_lim(limits.max_owned_characters)}",
            f"• کانال‌های شما: {bold(str(summary['channels_count']))} / {_fmt_lim(limits.max_channels)}",
            "",
            f"🔄 زمان بازنشانی سهمیه: {reset_str}"
            if locale == "fa"
            else f"🔄 Quota Reset: {reset_str}",
        ]

        refresh_cb = AccountCallback(action="view")
        close_cb = NavCallback(action="close")
        keyboard: list[list[dict[str, Any]]] = [
            [
                {
                    "text": "🔄 بروزرسانی" if locale == "fa" else "🔄 Refresh",
                    "callback_data": refresh_cb.pack(),
                },
                {
                    "text": "❌ بستن" if locale == "fa" else "❌ Close",
                    "callback_data": close_cb.pack(),
                },
            ]
        ]
        return "\n".join(lines), keyboard
