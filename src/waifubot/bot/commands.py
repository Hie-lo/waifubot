"""Telegram command/callback handlers (SPEC §11) — the manual admin flow.

Handlers only write jobs and read state; they never touch the browser or the
provider (SPEC §4). Every command AND callback is re-authorized on the server;
stale or tampered callbacks do nothing. All user-visible strings come from the
locale catalog. Idempotency: the request key derives from the Telegram update or
callback ID, so a double tap cannot create two jobs.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waifubot.bot.auth import AdminGate
from waifubot.bot.gateway import Buttons, TelegramGateway
from waifubot.bot.memory import DialogMemory, PanelMemory
from waifubot.bot.messages import Messages
from waifubot.bot.posting import publish_group, publish_image, resolve_unknown_send
from waifubot.bot.worker import GenerationWorker
from waifubot.config import HARD_MAX_IMAGES_PER_JOB
from waifubot.core.admin_settings import SPECS_BY_KEY, AdminSettings
from waifubot.core.ai_enhance import AiEnhancer
from waifubot.core.content_safety import validate_adult_identity
from waifubot.core.identity import (
    TRAIT_ORDER,
    IdentityPool,
    assemble_identity,
    extract_traits,
    known_character_prompt,
    random_identity,
    validate_trait_value,
)
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.db.models import Character, Image, Job, Post
from waifubot.errors import AiError, ConfigError, ValidationError
from waifubot.services.generation import create_job

log = logging.getLogger(__name__)

OK_WORDS = ("ok", "okay", "yes", "y", "تایید", "بله", "باشه")


@dataclass
class Inbound:
    user_id: int
    chat_id: int
    text: str = ""
    data: str | None = None  # callback_data
    update_id: int = 0
    callback_id: str = ""
    message_id: int | None = None  # message the button lives on (for in-place edits)


@dataclass
class BotConfig:
    admin_ids: tuple[int, ...]
    dest_chat_id: str
    allow_nsfw: bool
    storage_root: str
    style_values: dict[str, str] = field(default_factory=dict)
    blocklist: tuple[str, ...] = ()


class BotApp:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        admin_settings: AdminSettings,
        memory: DialogMemory,
        gateway: TelegramGateway,
        msgs: Messages,
        worker: GenerationWorker,
        gate: AdminGate,
        builder: PromptBuilder,
        config: BotConfig,
        identity_pool: IdentityPool,
        enhancer: AiEnhancer | None = None,
    ) -> None:
        self._sessions = sessions
        self._settings = admin_settings
        self._memory = memory
        self._gw = gateway
        self._msgs = msgs
        self._worker = worker
        self._gate = gate
        self._builder = builder
        self._cfg = config
        self._idpool = identity_pool
        self._ai = enhancer
        self._panels = PanelMemory(sessions)

    # ---- single-message panel (user rule 2026-10-17) ----------------------
    async def _panel(
        self, inb: Inbound, text: str, buttons: Buttons | None = None
    ) -> int:
        """EVERY menu section renders into ONE message: navigation edits it
        in place instead of stacking new messages (user 2026-10-17)."""
        stored = await self._panels.get(inb.user_id)
        if stored is not None and stored[0] == inb.chat_id:
            try:
                await self._gw.edit_message(inb.chat_id, stored[1], text, buttons=buttons)
                return stored[1]
            except Exception:  # noqa: BLE001 — deleted/too old -> fresh panel
                pass
        msg_id = await self._gw.send_message(inb.chat_id, text, buttons=buttons)
        await self._panels.set(inb.user_id, inb.chat_id, msg_id)
        return msg_id

    async def _panel_close(self, inb: Inbound) -> None:
        """Close the menu entirely: hide the bottom keyboard + delete the panel."""
        await self._gw.hide_reply_keyboard(
            inb.chat_id, self._msgs.t("menu_closed")
        )
        stored = await self._panels.get(inb.user_id)
        if stored is not None and stored[0] == inb.chat_id:
            await self._gw.delete_message(inb.chat_id, stored[1])
        await self._panels.clear(inb.user_id)

    # ---- dispatch ---------------------------------------------------------
    async def handle_message(self, inb: Inbound) -> None:
        if not self._gate.allow(inb.user_id):
            return
        try:
            text = (inb.text or "").strip()
            if text.startswith("/"):
                await self._command(inb, text.split()[0].split("@")[0].lower())
                return
            # Stage 4: the bottom reply keyboard sends labels as text — route
            # them like the inline menu. A main-nav press always navigates (and
            # abandons any open dialog, like every modern bot).
            nav_cmd = self._main_label_map().get(text)
            if nav_cmd is not None:
                await self._memory.clear(inb.user_id)
                await self._command(inb, nav_cmd)
                return
            dialog = await self._memory.get(inb.user_id)
            if dialog:
                await self._dialog_input(inb, dialog)
        except Exception:  # noqa: BLE001 — a bad message must never crash the bot
            log.exception("message failed (text=%s)", (inb.text or "")[:40])
            await self._panel(inb, self._msgs.t("error_generic"))

    async def handle_callback(self, inb: Inbound) -> None:
        if not self._gate.allow(inb.user_id):
            await self._safe_answer(inb)
            return
        data = inb.data or ""
        try:
            if data.startswith("wiz:"):
                await self._cb_wizard(inb, data)
            elif data.startswith("appr:"):
                await self._cb_review(inb, data, approve=True)
            elif data.startswith("rej:"):
                await self._cb_review(inb, data, approve=False)
            elif data.startswith("savec:"):
                await self._cb_save_character(inb, data)
            elif data.startswith("menu:"):
                await self._cb_menu(inb, data)
            elif data.startswith("view:"):
                await self._cb_view(inb, data)
            elif data.startswith("nsfw:"):
                await self._cb_nsfw(inb, data)
            elif data in ("grp", "gnext", "gok") or data.startswith("gtog:"):
                await self._cb_group(inb, data)
            elif data.startswith("gskip"):
                await self._cb_group(inb, "gskip")
            elif data.startswith("cancel:"):
                await self._cb_cancel_job(inb, data)
            elif data.startswith("retry:"):
                await self._cb_retry_job(inb, data)
            elif data.startswith("usechar:"):
                await self._cb_usechar(inb, data)
            elif data.startswith("char:"):
                await self._cb_char(inb, data)
            elif data.startswith("pub:"):
                await self._cb_publish(inb, data)
            elif data.startswith("res:"):
                await self._cb_resolve(inb, data)
            elif data.startswith("set:") or data.startswith("setv:"):
                await self._cb_setting(inb, data)
            elif data.startswith("del:") or data.startswith("dely:"):
                await self._cb_delete(inb, data)
            else:
                await self._safe_answer(inb)
        except Exception:  # noqa: BLE001 — a bad callback must never crash the bot
            log.exception("callback failed (data=%s)", data[:40])
            await self._safe_answer(inb, self._msgs.t("error_generic"))

    async def _safe_answer(self, inb: Inbound, text: str = "") -> None:
        try:
            await self._gw.answer_callback(inb.callback_id, text)
        except Exception:  # noqa: BLE001
            pass

    # ---- commands ---------------------------------------------------------
    async def _command(self, inb: Inbound, cmd: str) -> None:
        if cmd == "/menu":
            await self._cmd_menu(inb)
        elif cmd in ("/start", "/help"):
            await self._panel(inb, self._msgs.t("welcome"))
            await self._panel(inb, self._msgs.t("help"))
            await self._cmd_menu(inb)  # buttons first: commands as little as possible
        elif cmd == "/status":
            await self._cmd_status(inb)
        elif cmd == "/generate":
            await self._cmd_generate(inb)
        elif cmd == "/inbox":
            await self._cmd_inbox(inb)
        elif cmd == "/queue":
            await self._cmd_queue(inb)
        elif cmd == "/pool":
            await self._cmd_pool(inb)
        elif cmd == "/publish":
            await self._cmd_publish(inb)
        elif cmd == "/settings":
            await self._cmd_settings(inb)
        elif cmd == "/pause":
            await self._worker.pause(source="admin")
            await self._panel(inb, self._msgs.t("paused"))
        elif cmd == "/resume":
            await self._worker.resume()
        elif cmd == "/characters":
            await self._cmd_characters(inb)
        else:
            await self._panel(inb, self._msgs.t("help"))

    async def _cmd_status(self, inb: Inbound) -> None:
        stats = await self._worker.queue_stats()
        paused = self._worker.paused
        buttons: Buttons | None = None
        if paused:
            buttons = [[(self._msgs.t("menu_resume"), "menu:resume")]]
        elif stats.get("pending") or stats.get("running"):
            buttons = [[(self._msgs.t("menu_pause"), "menu:pause")]]
        await self._panel(inb,
            self._msgs.t("status_paused" if paused else "status_running"),
            buttons=buttons,
        )
        await self._panel(inb,
            self._msgs.t(
                "status_queue",
                pending=stats.get("pending", 0),
                running=stats.get("running", 0),
                failed=stats.get("failed", 0),
            ),
        )
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._sessions() as s:
            jobs_today = (
                await s.execute(
                    select(func.count()).select_from(Job).where(Job.created_at >= today_start)
                )
            ).scalar_one()
            posts_today = (
                await s.execute(
                    select(func.count()).select_from(Post).where(Post.created_at >= today_start)
                )
            ).scalar_one()
            last_errors = (
                await s.execute(
                    select(Job.error_code)
                    .where(Job.error_code.is_not(None))
                    .order_by(Job.id.desc()).limit(3)
                )
            ).scalars().all()
        free = shutil.disk_usage(self._cfg.storage_root).free // 1024**3
        await self._panel(inb,
            self._msgs.t("status_counts", jobs=jobs_today, posts=posts_today, disk=f"{free}GB"),
        )
        if last_errors:
            await self._panel(inb,
                self._msgs.t("status_last_errors", errors=", ".join(str(e) for e in last_errors)),
            )

    async def _cmd_generate(self, inb: Inbound) -> None:
        style_rows: list[tuple[str, str]] = [
            (key, f"wiz:s:{key}") for key in sorted(self._cfg.style_values)
        ]
        style_rows.append((self._msgs.t("wiz_random"), "wiz:s:rnd"))
        style_rows.append((self._msgs.t("menu_back"), "menu:back"))
        await self._memory.set(inb.user_id, {"kind": "generate", "step": "style"})
        await self._panel(inb, self._msgs.t("wiz_style_ask"), buttons=_chunk(style_rows)
        )

    async def _cmd_inbox(self, inb: Inbound) -> None:
        ids = await self._scope_ids("inbox")
        if not ids:
            await self._panel(inb, self._msgs.t("inbox_empty"),
                buttons=[[(self._msgs.t("view_back"), "menu:back")]],
            )
            return
        lines = [self._msgs.t("inbox_item", image_id=i, job_id="-") for i in ids]
        buttons: Buttons = [
            [(self._msgs.t("inbox_view"), f"view:inbox:{ids[0]}")],
            [(self._msgs.t("view_back"), "menu:back")],
        ]
        await self._panel(inb,
            self._msgs.t("inbox_summary", count=len(ids)) + "\n" + "\n".join(lines),
            buttons=buttons,
        )

    async def _cmd_queue(self, inb: Inbound) -> None:
        async with self._sessions() as s:
            jobs = (
                await s.execute(select(Job).order_by(Job.id.desc()).limit(10))
            ).scalars().all()
        if not jobs:
            await self._panel(inb, self._msgs.t("queue_empty"),
                buttons=[[(self._msgs.t("view_back"), "menu:back")]],
            )
            return
        lines = [
            self._msgs.t(
                "queue_item", job_id=job.id, status=job.status,
                attempt=job.attempt_count, max=job.max_attempts,
                error=job.error_code or "-",
            )
            for job in jobs
        ]
        buttons: Buttons = []
        cancelable = [j for j in jobs if j.status == "pending"]
        if cancelable:
            buttons.append(
                [(self._msgs.t("queue_cancel") + f" #{j.id}", f"cancel:{j.id}")
                 for j in cancelable[:5]]
            )
        retryable = [j for j in jobs if j.status in ("failed", "cancelled")]
        if retryable:
            buttons.append(
                [(self._msgs.t("queue_retry") + f" #{j.id}", f"retry:{j.id}")
                 for j in retryable[:5]]
            )
        buttons.append([(self._msgs.t("queue_refresh"), "menu:queue"),
                        (self._msgs.t("view_back"), "menu:back")])
        await self._panel(inb, "\n".join(lines), buttons=buttons)

    async def _cmd_pool(self, inb: Inbound) -> None:
        images = await self._pool_images()
        if not images:
            await self._panel(inb, self._msgs.t("pool_empty"),
                buttons=[[(self._msgs.t("view_back"), "menu:back")]],
            )
            return
        lines = [
            self._msgs.t("pool_item", image_id=img.id, status=img.status)
            for img in images
        ]
        buttons: Buttons = [
            [(self._msgs.t("pool_view"), f"view:pool:{images[0].id}")],
            [(self._msgs.t("pool_group"), "grp"),
             (self._msgs.t("publish_pick_short"), "menu:publish")],
            [(self._msgs.t("view_back"), "menu:back")],
        ]
        await self._panel(inb,
            self._msgs.t("pool_summary", count=len(images)) + "\n" + "\n".join(lines),
            buttons=buttons,
        )

    async def _pool_images(self) -> list[Image]:
        async with self._sessions() as s:
            return list(
                (
                    await s.execute(
                        select(Image)
                        .where(Image.status == "approved")
                        .order_by(Image.id.asc()).limit(20)
                    )
                ).scalars().all()
            )

    async def _cmd_publish(self, inb: Inbound) -> None:
        images = await self._pool_images()
        if not images:
            await self._panel(inb, self._msgs.t("pool_empty"))
            return
        rows = [(self._msgs.t("pool_item", image_id=i.id, status=i.status), f"pub:{i.id}")
                for i in images]
        rows.append((self._msgs.t("view_back"), "menu:back"))
        await self._panel(inb, self._msgs.t("publish_pick"), buttons=_chunk(rows)
        )

    async def _cmd_settings(self, inb: Inbound) -> None:
        values = await self._settings.all_settings()
        lines = [
            self._msgs.t("settings_item", key=k, value=v)
            for k, v in sorted(values.items())
        ]
        rows = [(k, f"set:{k}") for k in sorted(values)]
        rows.append((self._msgs.t("view_back"), "menu:back"))
        await self._panel(
            inb,
            f"{self._msgs.t('settings_title')}\n\n" + "\n".join(lines),
            buttons=_chunk(rows),
        )

    async def _cmd_characters(self, inb: Inbound) -> None:
        async with self._sessions() as s:
            rows = (
                await s.execute(
                    select(Character).where(Character.is_active.is_(True))
                    .order_by(Character.id.desc()).limit(20)
                )
            ).scalars().all()
        if not rows:
            await self._panel(inb, self._msgs.t("characters_empty"),
                buttons=[[(self._msgs.t("view_back"), "menu:back")]],
            )
            return
        list_rows = [(f"👤 {r.name} (v{r.version})", f"char:{r.id}") for r in rows]
        list_rows.append((self._msgs.t("view_back"), "menu:back"))
        await self._panel(inb, self._msgs.t("characters_title"), buttons=_chunk(list_rows)
        )

    # ---- one-by-one viewer / NSFW / group post / jobs / characters ---------
    async def _scope_ids(self, scope: str) -> list[int]:
        status = "approved" if scope == "pool" else "pending_review"
        async with self._sessions() as s:
            rows = (
                await s.execute(
                    select(Image.id).where(Image.status == status)
                    .order_by(Image.id.asc()).limit(50)
                )
            ).all()
        return [r[0] for r in rows]

    async def _job_has_character(self, job_id: int) -> bool:
        async with self._sessions() as s:
            job = await s.get(Job, job_id)
            if job is None:
                return False
            filters = json.loads(job.filters_json or "{}")
        return bool(filters.get("character_id"))

    async def _viewer_payload(
        self, scope: str, image_id: int
    ) -> tuple[str, str, Buttons] | None:
        """Everything the viewer shows for one image (caption, path, buttons)."""
        ids = await self._scope_ids(scope)
        if not ids:
            return None
        if image_id not in ids:
            image_id = ids[0]
        pos = ids.index(image_id)
        async with self._sessions() as s:
            img = await s.get(Image, image_id)
            if img is None:
                await s.commit()
                return None
            name = await self._image_name(s, img)
            job_id = img.job_id
            is_nsfw = bool(img.is_nsfw)
            file_path = img.file_path
        has_character = await self._job_has_character(job_id)
        nav: list[tuple[str, str]] = []
        if pos > 0:
            nav.append((self._msgs.t("view_prev"), f"view:{scope}:{ids[pos - 1]}"))
        if pos < len(ids) - 1:
            nav.append((self._msgs.t("view_next"), f"view:{scope}:{ids[pos + 1]}"))
        buttons: Buttons = [nav] if nav else []
        buttons.append([
            (self._msgs.t("nsfw_unmark" if is_nsfw else "nsfw_mark"), f"nsfw:{image_id}"),
        ])
        if scope == "pool":
            buttons.append([
                (self._msgs.t("publish_pick_short"), f"pub:{image_id}"),
                (self._msgs.t("delete_button"), f"del:{image_id}"),
            ])
        else:
            buttons.append([
                (self._msgs.t("review_approve"), f"appr:{image_id}"),
                (self._msgs.t("review_reject"), f"rej:{image_id}"),
            ])
            if not has_character:
                # a saved character leaves nothing to save (user rule 2026-10-16)
                buttons.append([(self._msgs.t("save_character"), f"savec:{job_id}")])
        buttons.append([
            (self._msgs.t("view_back"), "menu:back"),
            (self._msgs.t("view_close"), "view:x"),
        ])
        caption = self._msgs.t(
            "view_item", image_id=image_id, job_id=job_id,
            pos=pos + 1, total=len(ids), name=name,
            nsfw=self._msgs.t("nsfw_yes" if is_nsfw else "nsfw_no"),
        )
        return caption, f"{self._cfg.storage_root}/{file_path}", buttons

    async def _render_viewer(
        self, inb: Inbound, scope: str, image_id: int
    ) -> None:
        payload = await self._viewer_payload(scope, image_id)
        if payload is None:
            if inb.message_id:
                await self._gw.edit_message_caption(
                    inb.chat_id, inb.message_id, self._msgs.t("pool_empty"),
                    buttons=[[(self._msgs.t("view_back"), "menu:back")]],
                )
            else:
                await self._panel(inb, self._msgs.t("pool_empty"))
            return
        caption, path_str, buttons = payload
        path = Path(path_str)
        # In-place edit when the press came from a message; never a new photo.
        if inb.message_id:
            try:
                await self._gw.edit_message_media(
                    inb.chat_id, inb.message_id, path, caption=caption, buttons=buttons
                )
                return
            except Exception:  # noqa: BLE001 — fall back to a fresh send
                log.warning("viewer edit failed; sending new (image=%s)", image_id)
        try:
            await self._gw.send_photo(inb.chat_id, path, caption=caption, buttons=buttons)
        except Exception:  # noqa: BLE001
            log.warning("viewer failed (image=%s)", image_id)

    async def _cb_view(self, inb: Inbound, data: str) -> None:
        parts = data.split(":")
        if parts[1] == "x":  # close: remove the viewer buttons in place
            await self._safe_answer(inb)
            if inb.message_id:
                try:
                    await self._gw.edit_message_caption(
                        inb.chat_id, inb.message_id,
                        caption=self._msgs.t("viewer_closed"), buttons=[],
                    )
                except Exception:  # noqa: BLE001
                    log.warning("viewer close failed")
            return
        scope, raw_id = parts[1], parts[2]
        await self._safe_answer(inb)
        await self._render_viewer(inb, scope, int(raw_id))

    async def _cb_nsfw(self, inb: Inbound, data: str) -> None:
        image_id = int(data.split(":")[1])
        async with self._sessions() as s:
            img = await s.get(Image, image_id)
            if img is None:
                await s.commit()
                await self._safe_answer(inb, self._msgs.t("wiz_stale"))
                return
            img.is_nsfw = not bool(img.is_nsfw)
            state = img.is_nsfw
            status = img.status
            await s.commit()
        await self._safe_answer(inb)
        if inb.message_id:
            # flip the state on the SAME message (button + caption update)
            scope = "pool" if status == "approved" else "inbox"
            await self._render_viewer(inb, scope, image_id)
            return
        await self._panel(inb,
            self._msgs.t("nsfw_set" if state else "nsfw_unset", image_id=image_id),
        )

    # ---- group post wizard -------------------------------------------------
    async def _cb_group(self, inb: Inbound, data: str) -> None:
        if data == "grp":
            ids = await self._scope_ids("pool")
            if len(ids) < 2:
                await self._safe_answer(inb)
                await self._panel(inb, self._msgs.t("group_too_few"))
                return
            dialog = {"kind": "group", "step": "pick", "selected": []}
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            msg_id = await self._send_group_picker(inb, dialog)
            dialog["msg_id"] = msg_id
            await self._memory.set(inb.user_id, dialog)
            return
        dialog = await self._memory.get(inb.user_id)
        if not dialog or dialog.get("kind") != "group":
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        if data.startswith("gtog:"):
            image_id = int(data.split(":")[1])
            selected: list[int] = list(dialog.get("selected") or [])
            if image_id in selected:
                selected.remove(image_id)
            else:
                if len(selected) >= 10:
                    await self._safe_answer(inb, self._msgs.t("group_too_many"))
                    return
                selected.append(image_id)
            dialog["selected"] = sorted(selected)
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._send_group_picker(inb, dialog, edit=True)
        elif data == "gnext":
            selected = dialog.get("selected") or []
            if len(selected) < 2:
                await self._safe_answer(inb, self._msgs.t("group_too_few"))
                return
            dialog["step"] = "caption"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            buttons: Buttons = [[(self._msgs.t("group_skip"), "gskip")]]
            await self._panel(inb, self._msgs.t("group_caption_ask"), buttons=buttons
            )
        elif data == "gskip":
            dialog["caption"] = ""
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._group_confirm(inb, dialog)
        elif data == "gok":
            await self._safe_answer(inb)
            await self._group_send(inb, dialog)

    async def _send_group_picker(
        self, inb: Inbound, dialog: dict, edit: bool = False
    ) -> int | None:
        ids = await self._scope_ids("pool")
        selected = set(dialog.get("selected") or [])
        rows: Buttons = []
        row: list[tuple[str, str]] = []
        for image_id in ids:
            mark = "✅" if image_id in selected else "○"
            row.append((f"{mark} #{image_id}", f"gtog:{image_id}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([(self._msgs.t("group_next"), "gnext"),
                     (self._msgs.t("wiz_cancel"), "wiz:x")])
        text = self._msgs.t("group_title") + "\n" + self._msgs.t(
            "group_selected", count=len(selected)
        )
        if edit and dialog.get("msg_id"):
            await self._gw.edit_message(
                inb.chat_id, dialog["msg_id"], text, buttons=rows
            )
            return dialog["msg_id"]
        return await self._panel(inb, text, buttons=rows)

    async def _group_confirm(self, inb: Inbound, dialog: dict) -> None:
        dialog["step"] = "confirm"
        await self._memory.set(inb.user_id, dialog)
        buttons: Buttons = [
            [(self._msgs.t("group_do"), "gok"),
             (self._msgs.t("wiz_cancel"), "wiz:x")],
        ]
        caption = dialog.get("caption") or self._msgs.t("group_skip")
        await self._panel(inb,
            self._msgs.t("group_confirm", count=len(dialog.get("selected") or []))
            + f"\n\n{self._msgs.t('group_caption_label')}: {caption}",
            buttons=buttons,
        )

    async def _group_send(self, inb: Inbound, dialog: dict) -> None:
        selected = dialog.get("selected") or []
        if not 2 <= len(selected) <= 10:
            await self._panel(inb, self._msgs.t("group_too_few"))
            return
        async with self._sessions() as s:
            images = (
                (await s.execute(select(Image).where(Image.id.in_(selected))))
                .scalars().all()
            )
            images = [i for i in images if i.status == "approved"]
            if len(images) < 2:
                await s.commit()
                await self._panel(inb, self._msgs.t("group_too_few"))
                return
            outcomes = await publish_group(
                s, self._gw, images=images,
                chat_id=self._cfg.dest_chat_id,
                caption=dialog.get("caption") or "",
                storage_root=Path(self._cfg.storage_root),
            )
            await s.commit()
        await self._memory.clear(inb.user_id)
        sent = sum(1 for o in outcomes if o.status == "sent")
        await self._panel(inb, self._msgs.t("group_done", count=len(images), sent=sent)
        )

    async def _cb_cancel_job(self, inb: Inbound, data: str) -> None:
        job_id = int(data.split(":")[1])
        # Conditional update: never cancel a job the worker has just claimed.
        async with self._sessions() as s:
            result = await s.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "pending")
                .values(status="cancelled", error_code="cancelled_by_admin")
            )
            await s.commit()
        if (result.rowcount or 0) != 1:
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        await self._safe_answer(inb)
        await self._panel(inb, self._msgs.t("queue_cancelled", job_id=job_id))

    async def _cb_retry_job(self, inb: Inbound, data: str) -> None:
        job_id = int(data.split(":")[1])
        async with self._sessions() as s:
            result = await s.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(("failed", "cancelled")))
                .values(status="pending", attempt_count=0, next_attempt_at=None,
                        error_code=None, error_summary=None)
            )
            await s.commit()
        if (result.rowcount or 0) != 1:
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        await self._safe_answer(inb)
        await self._panel(inb, self._msgs.t("job_requeued", job_id=job_id)
        )

    async def _cb_char(self, inb: Inbound, data: str) -> None:
        char_id = int(data.split(":")[1])
        async with self._sessions() as s:
            row = await s.get(Character, char_id)
            await s.commit()
        if row is None:
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        await self._safe_answer(inb)
        buttons: Buttons = [
            [(self._msgs.t("char_generate"), f"usechar:{char_id}")],
            [(self._msgs.t("editc_button"), f"savec:editc:{char_id}")],
            [(self._msgs.t("view_back"), "menu:characters"),
             (self._msgs.t("menu_back"), "menu:back")],
        ]
        await self._panel(inb,
            self._msgs.t(
                "char_detail_title", name=row.name, version=row.version
            ) + f"\n\n{row.identity_prompt}",
            buttons=buttons,
        )

    async def _cb_usechar(self, inb: Inbound, data: str) -> None:
        char_id = int(data.split(":")[1])
        async with self._sessions() as s:
            row = await s.get(Character, char_id)
            await s.commit()
        if row is None or not row.is_active:
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        await self._memory.set(
            inb.user_id,
            {"kind": "generate", "step": "scene", "style_key": "",
             "character_id": char_id},
        )
        await self._safe_answer(inb)
        await self._panel(inb,
            self._msgs.t("char_using", name=row.name),
        )
        await self._ask_scene(inb)

    # ---- wizard -----------------------------------------------------------
    async def _cb_wizard(self, inb: Inbound, data: str) -> None:
        part = data.split(":")[1]
        if part == "x":  # cancel works for ANY dialog (generate, savec, settings)
            await self._memory.clear(inb.user_id)
            await self._safe_answer(inb)
            await self._panel(inb, self._msgs.t("wiz_cancelled"))
            return
        dialog = await self._memory.get(inb.user_id)
        if not dialog or dialog.get("kind") != "generate":
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        if part == "s":
            key = data.split(":", 2)[2]
            dialog["style_key"] = key  # "rnd" = explicit random; else a style key
            dialog["step"] = "character"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._ask_character(inb)
        elif part == "c":
            char_id = int(data.split(":")[2])
            if char_id == -1:  # random everything (SPEC: unchosen = random)
                dialog["character_id"] = None
                dialog["identity"] = ""
                dialog["step"] = "scene"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._ask_scene(inb)
            elif char_id == 0:
                dialog["character_id"] = None
                dialog["step"] = "identity"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._panel(inb, self._msgs.t("wiz_identity_ask"))
            else:
                dialog["character_id"] = char_id
                dialog["step"] = "scene"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._ask_scene(inb)
        elif part == "sk":  # skip the optional scene prompt
            dialog["scene"] = ""
            dialog["step"] = "count"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._ask_count(inb)
        elif part == "n":
            raw = data.split(":")[2]
            dialog["count"] = 0 if raw == "rnd" else int(raw)
            dialog["step"] = "confirm"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._ask_confirm(inb)
        elif part == "ai":  # AI-enhance the final prompt (Stage 3)
            await self._safe_answer(inb)
            if not await self._ai_ready():
                await self._panel(inb,
                    self._msgs.t("ai_error", error="disabled or not configured"),
                )
                return
            base, _lock = await self._resolve_identity(dialog)
            scene = dialog.get("scene") or ""
            preview = f"{base}\n{scene}" if scene else base
            known = bool(dialog.get("character_id"))
            enhanced = await self._call_enhance(inb, preview, known=known)
            if enhanced is None:
                return
            dialog["ai_prompt"] = enhanced
            dialog["step"] = "confirm"
            await self._memory.set(inb.user_id, dialog)
            buttons: Buttons = [[
                (self._msgs.t("wiz_confirm_yes"), "wiz:y"),
                (self._msgs.t("ai_undo"), "wiz:aiundo"),
                (self._msgs.t("wiz_cancel"), "wiz:x"),
            ]]
            await self._panel(inb,
                f"{self._msgs.t('ai_enhanced_ask')}\n\n{enhanced}",
                buttons=buttons,
            )
        elif part == "aiundo":  # back to the original (non-enhanced) prompt
            dialog.pop("ai_prompt", None)
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._ask_confirm(inb)
        elif part == "y":
            await self._safe_answer(inb)
            if dialog.get("step") != "confirm":
                await self._panel(inb, self._msgs.t("wiz_stale"))
                return
            await self._confirm_generate(inb, dialog)

    async def _ask_character(self, inb: Inbound) -> None:
        async with self._sessions() as s:
            chars = (
                await s.execute(
                    select(Character).where(Character.is_active.is_(True))
                    .order_by(Character.id.desc()).limit(20)
                )
            ).scalars().all()
        rows = [(c.name, f"wiz:c:{c.id}") for c in chars]
        rows.append((self._msgs.t("wiz_character_oneoff"), "wiz:c:0"))
        rows.append((self._msgs.t("wiz_random"), "wiz:c:-1"))
        rows.append((self._msgs.t("menu_back"), "menu:back"))
        await self._panel(inb, self._msgs.t("wiz_character_ask"), buttons=_chunk(rows)
        )

    SITE_COUNTS = (2, 4, 6, 8, 16)  # the site produces 2/4/6/8/16/32 (32 > hard max)

    async def _ask_scene(self, inb: Inbound) -> None:
        """Optional user scene prompt (pose/expression/clothes/activity/background)
        — combined with the character details at confirm (user 2026-10-16)."""
        buttons: Buttons = [
            [(self._msgs.t("wiz_skip"), "wiz:sk")],
            [(self._msgs.t("menu_back"), "menu:back")],
        ]
        await self._panel(inb, self._msgs.t("wiz_scene_ask"), buttons=buttons
        )

    async def _ask_count(self, inb: Inbound) -> None:
        # User 2026-10-16: ALL site options (2/4/6/8/16) are always offered;
        # images_per_job is only the DEFAULT for a random pick, never a hidden cap.
        rows = [(str(i), f"wiz:n:{i}") for i in self.SITE_COUNTS]
        rows.append((self._msgs.t("wiz_random"), "wiz:n:rnd"))
        rows.append((self._msgs.t("menu_back"), "menu:back"))
        await self._panel(inb, self._msgs.t("wiz_count_ask"), buttons=_chunk(rows)
        )

    async def _ai_ready(self) -> bool:
        """AI enhance is available and not switched off by the admin."""
        if self._ai is None:
            return False
        return bool(await self._settings.get_or("ai_enhance_enabled", 1))

    async def _call_enhance(
        self, inb: Inbound, text: str, *, known: bool
    ) -> str | None:
        """Run the fixed-instruction enhancement; None = it failed (user told)."""
        if self._ai is None:
            await self._panel(inb, self._msgs.t("ai_error", error="not configured")
            )
            return None
        try:
            # the model is an ENV setting only (user rule 2026-10-17) —
            # never a bot-side input
            return await self._ai.enhance(text, known=known)
        except AiError as e:
            await self._panel(inb, self._msgs.t("ai_error", error=str(e))
            )
            return None
        except Exception:  # noqa: BLE001
            log.exception("ai enhance failed")
            await self._panel(inb, self._msgs.t("error_generic"))
            return None

    def _build_identity(self, dialog: dict) -> str:
        """The character prompt this dialog represents (one source of truth)."""
        if dialog.get("mode") == "known":
            return known_character_prompt(
                dialog.get("known_name") or "",
                dialog.get("known_anime") or "",
                self._idpool,
            )
        if dialog.get("precise"):
            return assemble_identity(
                self._idpool,
                dialog.get("traits") or {},
                extras=dialog.get("extras") or "",
                precise=True,
                name=dialog.get("name") or "",
            )
        return (dialog.get("identity") or "").strip()

    async def _ask_confirm(self, inb: Inbound) -> None:
        rows: list[tuple[str, str]] = [
            (self._msgs.t("wiz_confirm_yes"), "wiz:y"),
            (self._msgs.t("wiz_cancel"), "wiz:x"),
        ]
        if await self._ai_ready():
            rows.append((self._msgs.t("ai_enhance"), "wiz:ai"))
        buttons: Buttons = [rows]
        await self._panel(inb, self._msgs.t("wiz_confirm_ask"), buttons=buttons
        )

    async def _resolve_identity(self, dialog: dict) -> tuple[str, str | None]:
        """(identity, style_lock) for a generate dialog — shared by the AI
        preview and the final confirm so both see the SAME prompt."""
        character_id = dialog.get("character_id")
        identity = dialog.get("identity") or ""
        style_lock: str | None = None
        if character_id:
            async with self._sessions() as s:
                row = await s.get(Character, character_id)
                if row is None:
                    raise ValidationError("character not found")
                identity = row.identity_prompt
                style_lock = row.style_lock or None
        if not identity:
            async with self._sessions() as s:
                row = (
                    await s.execute(
                        select(Character)
                        .where(Character.is_active.is_(True))
                        .order_by(func.random()).limit(1)
                    )
                ).scalars().first()
                identity = (
                    row.identity_prompt if row
                    else random_identity(self._idpool)  # diverse per job
                )
                style_lock = (row.style_lock or None) if row else None
        return identity, style_lock

    async def _confirm_generate(self, inb: Inbound, dialog: dict) -> None:
        try:
            style_key = dialog.get("style_key") or ""
            if style_key == "rnd" and self._cfg.style_values:
                # explicit "random style" button
                style_key, style_value = random.choice(
                    list(self._cfg.style_values.items())
                )
            elif style_key and style_key in self._cfg.style_values:
                style_value = self._cfg.style_values[style_key]
            else:
                # no style chosen (e.g. started from a character): the
                # admin's default_style setting decides — never surprise-random
                style_key = await self._settings.get("default_style") or ""
                style_value = self._cfg.style_values.get(style_key, "")
            identity, style_lock = await self._resolve_identity(dialog)
            # AI-enhanced prompt (Stage 3) replaces the composed one verbatim;
            # the scene was merged into the enhanced text at wiz:ai time.
            ai_prompt = (dialog.get("ai_prompt") or "").strip()
            if ai_prompt:
                identity = ai_prompt
                admin_text = ""
            else:
                admin_text = dialog.get("scene") or ""
            validate_adult_identity(identity, self._cfg.blocklist)
            default_count = await self._settings.get_or("images_per_job", 4)
            count = (
                int(dialog.get("count") or 0)
                or min(self.SITE_COUNTS, key=lambda c: abs(c - default_count))
            )
            style_lock = style_lock or await self._settings.get("style_lock")
            # The explicit count the user picked is honored exactly (up to the
            # site set / hard safety cap) - images_per_job is NOT a hidden clamp.
            max_images = HARD_MAX_IMAGES_PER_JOB
            max_jobs = await self._settings.get_or("daily_cap", 20)
            async with self._sessions() as s:
                job = await create_job(
                    s,
                    builder=self._builder,
                    identity=identity,
                    style_lock=style_lock,
                    count=min(count, max_images),
                    style_value=style_value,
                    admin_text=admin_text,
                    idempotency_key=f"tg:{inb.update_id}",
                    requested_by=f"tg:{inb.user_id}",
                    max_images_per_job=max_images,
                    max_jobs_per_day=max_jobs,
                    priority=1,
                )
                await s.commit()
            await self._memory.clear(inb.user_id)
            await self._panel(inb, self._msgs.t("wiz_created", job_id=job.id)
            )
        except (ValidationError, ConfigError) as e:
            await self._panel(inb, str(e))
        except Exception:  # noqa: BLE001
            log.exception("generate confirm failed")
            await self._panel(inb, self._msgs.t("error_generic"))

    async def _dialog_input(self, inb: Inbound, dialog: dict) -> None:
        kind = dialog.get("kind")
        text = (inb.text or "").strip()
        if not text:
            return
        if kind == "generate" and dialog.get("step") == "identity":
            dialog["identity"] = text
            dialog["step"] = "scene"
            await self._memory.set(inb.user_id, dialog)
            await self._ask_scene(inb)
        elif kind == "generate" and dialog.get("step") == "scene":
            dialog["scene"] = text[:500]
            dialog["step"] = "count"
            await self._memory.set(inb.user_id, dialog)
            await self._ask_count(inb)
        elif kind in ("savec", "editc"):
            await self._savec_input(inb, dialog, text)
        elif kind == "set":
            await self._set_input(inb, dialog, text)
        elif kind == "group" and dialog.get("step") == "caption":
            dialog["caption"] = text[:1024]
            await self._memory.set(inb.user_id, dialog)
            await self._group_confirm(inb, dialog)
        else:
            await self._memory.clear(inb.user_id)

    # ---- review / save character ------------------------------------------
    async def _cb_review(self, inb: Inbound, data: str, *, approve: bool) -> None:
        image_id = int(data.split(":")[1])
        async with self._sessions() as s:
            img = await s.get(Image, image_id)
            if img is None or img.status != "pending_review":
                await s.commit()
                await self._safe_answer(inb, self._msgs.t("review_stale"))
                return
            img.status = "approved" if approve else "rejected"
            await s.commit()
        await self._safe_answer(inb)
        if inb.message_id:
            # the reviewer is inside the viewer: jump to the next image in place
            ids = await self._scope_ids("inbox")
            if ids:
                await self._render_viewer(inb, "inbox", ids[0])
                return
            await self._gw.edit_message(
                inb.chat_id, inb.message_id, self._msgs.t("inbox_empty"),
                buttons=[[(self._msgs.t("view_back"), "menu:back")]],
            )
            return
        await self._panel(inb,
            self._msgs.t(
                "review_approved" if approve else "review_rejected", image_id=image_id
            ),
        )

    async def _cb_save_character(self, inb: Inbound, data: str) -> None:
        parts = data.split(":")
        action = parts[1]
        dialog = await self._memory.get(inb.user_id)
        if action == "editc":  # savec:editc:<id> — works from the menu too
            char_id = int(parts[2])
            async with self._sessions() as s:
                row = await s.get(Character, char_id)
                await s.commit()
            if row is None or not row.is_active:
                await self._safe_answer(inb, self._msgs.t("wiz_stale"))
                return
            try:
                details = json.loads(row.details_json or "{}")
                if not isinstance(details, dict):
                    details = {}
            except (ValueError, TypeError):
                details = {}  # corrupt details_json must never block editing
            orig_mode = str(details.get("mode") or "")
            traits = dict(details.get("traits") or extract_traits(
                self._idpool, row.identity_prompt
            ))
            base = {
                "kind": "editc", "edit_of": row.name,
                "name": row.name,
                "extras": details.get("extras") or "",
                "traits": traits,
            }
            if orig_mode == "known":
                # keep the known-character model: correct name + anime only
                base.update({
                    "mode": "known", "precise": True,
                    "known_name": details.get("known_name") or row.name,
                    "known_anime": details.get("known_anime") or "",
                    "step": "known_name",
                })
            elif orig_mode == "manual" and not traits:
                # free-text character: correct the description itself
                base.update({
                    "mode": "manual", "precise": False,
                    "identity": row.identity_prompt,
                    "step": "identity",
                })
            else:
                first = self._first_missing_trait(traits)
                base.update({
                    "mode": "precise", "precise": True,
                    "known_name": "", "known_anime": "",
                    "step": f"trait:{first}",
                })
            await self._memory.set(inb.user_id, base)
            await self._safe_answer(inb)
            await self._panel(inb, self._msgs.t("editc_start", name=row.name)
            )
            if base["step"] == "known_name":
                await self._panel(inb, self._msgs.t("savec_known_name_ask")
                )
            elif base["step"] == "identity":
                await self._panel(inb, self._msgs.t("savec_identity_ask")
                )
            else:
                await self._ask_trait(inb, base, base["step"].split(":", 1)[1])
            return
        if action.isdigit():
            job_id = int(action)
            async with self._sessions() as s:
                job = await s.get(Job, job_id)
                if job is None:
                    await s.commit()
                    await self._safe_answer(inb, self._msgs.t("review_stale"))
                    return
                identity = json.loads(job.filters_json or "{}").get("identity", "")
            await self._safe_answer(inb)
            await self._memory.set(
                inb.user_id,
                {"kind": "savec", "job_id": job_id, "identity": identity, "step": "name"},
            )
            await self._panel(inb, self._msgs.t("savec_name_ask"))
            return
        if not dialog or dialog.get("kind") not in ("savec", "editc"):
            await self._safe_answer(inb, self._msgs.t("wiz_stale"))
            return
        if action == "p":  # precise prompt builder (user request 2026-10-16)
            traits = extract_traits(self._idpool, dialog.get("identity") or "")
            dialog["traits"] = traits
            dialog["extras"] = ""
            dialog["precise"] = True
            dialog["step"] = f"trait:{TRAIT_ORDER[0]}"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._ask_trait(inb, dialog, TRAIT_ORDER[0])
        elif action == "k":  # known character: name + anime, that's it
            dialog["precise"] = True
            dialog["mode"] = "known"
            dialog["step"] = "known_name"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._panel(inb, self._msgs.t("savec_known_name_ask")
            )
        elif action == "j":  # keep this generation's identity, exactly
            dialog["precise"] = False
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._savec_review(inb, dialog)
        elif action == "m":  # write the description by hand
            dialog["precise"] = False
            dialog["step"] = "identity"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._panel(inb, self._msgs.t("savec_identity_ask"))
        elif action == "t":  # savec:t:<trait>:<value>
            trait, value = parts[2], ":".join(parts[3:])
            if trait not in TRAIT_ORDER:  # stale/foreign button
                await self._safe_answer(inb, self._msgs.t("wiz_stale"))
                return
            try:
                value = validate_trait_value(trait, value)
            except ValidationError as e:
                # stay on the same trait so the user can pick again
                dialog["step"] = f"trait:{trait}"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(
                    inb, self._msgs.t("savec_bad_value", error=str(e))
                )
                await self._ask_trait(inb, dialog, trait)
                return
            dialog.setdefault("traits", {})[trait] = value
            nxt = self._next_trait(dialog)
            if nxt is None:
                dialog["step"] = "trait_extras"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._ask_extras(inb)
            else:
                dialog["step"] = f"trait:{nxt}"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._ask_trait(inb, dialog, nxt)
        elif action == "tr":  # random pick for this trait (skipped = random)
            trait = parts[2]
            if trait not in TRAIT_ORDER:  # stale/foreign button
                await self._safe_answer(inb, self._msgs.t("wiz_stale"))
                return
            values = self._idpool.values(trait)
            dialog.setdefault("traits", {})[trait] = random.choice(values).value
            nxt = self._next_trait(dialog)
            if nxt is None:
                dialog["step"] = "trait_extras"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._ask_extras(inb)
            else:
                dialog["step"] = f"trait:{nxt}"
                await self._memory.set(inb.user_id, dialog)
                await self._safe_answer(inb)
                await self._ask_trait(inb, dialog, nxt)
        elif action == "tx":  # skip extras
            dialog.setdefault("extras", "")
            await self._safe_answer(inb)
            await self._savec_review(inb, dialog)
        elif action == "cust":  # free text for this trait instead of buttons
            trait = parts[2]
            if trait not in TRAIT_ORDER:  # stale/foreign button
                await self._safe_answer(inb, self._msgs.t("wiz_stale"))
                return
            dialog["step"] = f"trait_custom:{trait}"
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._panel(inb, self._msgs.t("savec_custom_ask")
            )
        elif action == "ai":  # AI-enhance the character prompt
            await self._safe_answer(inb)
            if not await self._ai_ready():
                await self._panel(inb,
                    self._msgs.t("ai_error", error="disabled or not configured"),
                )
                return
            base = (dialog.get("final_identity") or "").strip()
            if not base:
                base = self._build_identity(dialog)
            enhanced = await self._call_enhance(
                inb, base, known=dialog.get("mode") == "known"
            )
            if enhanced is None:
                return
            dialog["ai_identity"] = enhanced
            await self._memory.set(inb.user_id, dialog)
            await self._savec_review(inb, dialog, rebuild=False)
        elif action == "aiok":  # apply the AI version as the final prompt
            dialog["final_identity"] = (
                dialog.pop("ai_identity", "") or dialog.get("final_identity", "")
            )
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._savec_review(inb, dialog, rebuild=False)
        elif action == "aiundo":  # discard the AI version
            dialog.pop("ai_identity", None)
            await self._memory.set(inb.user_id, dialog)
            await self._safe_answer(inb)
            await self._savec_review(inb, dialog, rebuild=True)
        elif action == "ok":
            await self._safe_answer(inb)
            await self._savec_commit(inb, dialog)

    def _next_trait(self, dialog: dict) -> str | None:
        chosen = dialog.get("traits") or {}
        for trait in TRAIT_ORDER:
            if trait not in chosen:
                return trait
        return None

    def _first_missing_trait(self, traits: dict) -> str:
        for trait in TRAIT_ORDER:
            if trait not in traits:
                return trait
        return TRAIT_ORDER[-1]

    async def _ask_trait(self, inb: Inbound, dialog: dict, trait: str) -> None:
        lang = self._msgs.lang
        rows = [
            (self._idpool.label(trait, tv.value, lang), f"savec:t:{trait}:{tv.value}")
            for tv in self._idpool.values(trait)
        ]
        rows.append((self._msgs.t("savec_random_pick"), f"savec:tr:{trait}"))
        rows.append((self._msgs.t("savec_custom"), f"savec:cust:{trait}"))
        await self._panel(inb,
            self._msgs.t("savec_trait_ask", trait=self._msgs.t(f"trait_{trait}")),
            buttons=_chunk(rows),
        )

    async def _ask_extras(self, inb: Inbound) -> None:
        buttons: Buttons = [[(self._msgs.t("savec_skip"), "savec:tx")]]
        await self._panel(inb, self._msgs.t("savec_extras_ask"), buttons=buttons
        )

    async def _savec_review(
        self, inb: Inbound, dialog: dict, *, rebuild: bool = True
    ) -> None:
        if rebuild or not (dialog.get("final_identity") or "").strip():
            dialog["final_identity"] = self._build_identity(dialog)
        dialog["step"] = "review"
        await self._memory.set(inb.user_id, dialog)
        ai_pending = (dialog.get("ai_identity") or "").strip()
        if ai_pending:
            # show the AI version with accept/undo (user compares in chat)
            buttons: Buttons = [[
                (self._msgs.t("ai_apply"), "savec:aiok"),
                (self._msgs.t("ai_undo"), "savec:aiundo"),
            ]]
            await self._panel(inb,
                f"{self._msgs.t('ai_enhanced_ask')}\n\n{ai_pending}",
                buttons=buttons,
            )
            return
        rows: list[tuple[str, str]] = [
            (self._msgs.t("savec_confirm"), "savec:ok"),
            (self._msgs.t("wiz_cancel"), "wiz:x"),
        ]
        if await self._ai_ready():
            rows.append((self._msgs.t("ai_enhance"), "savec:ai"))
        buttons = [rows]
        await self._panel(inb,
            f"{self._msgs.t('savec_review_ask')}\n\n{dialog['final_identity']}",
            buttons=buttons,
        )

    async def _savec_commit(self, inb: Inbound, dialog: dict) -> None:
        identity = (dialog.get("final_identity") or "").strip()
        if not identity:  # OK can be pressed before the review screen
            identity = self._build_identity(dialog)
        try:
            validate_adult_identity(identity, self._cfg.blocklist)
        except ValidationError as e:
            await self._panel(inb, self._msgs.t("savec_invalid", error=str(e))
            )
            return
        name = dialog.get("name") or "unnamed"
        details = {
            "mode": dialog.get("mode") or ("precise" if dialog.get("precise") else "manual"),
            "traits": dialog.get("traits") or {},
            "extras": dialog.get("extras") or "",
            "known_name": dialog.get("known_name") or "",
            "known_anime": dialog.get("known_anime") or "",
        }
        async with self._sessions() as s:
            # edit = a NEW version (parent link keeps the history); the lookup
            # is by the ORIGINAL name so a corrected name still versions up
            prev_name = dialog.get("edit_of") or name
            prev = (
                await s.execute(
                    select(Character).where(Character.name == prev_name)
                    .order_by(Character.version.desc()).limit(1)
                )
            ).scalars().first()
            if prev is not None:
                prev.is_active = False
            row = Character(
                name=name,
                identity_prompt=identity,
                details_json=json.dumps(details, ensure_ascii=False),
                style_lock=await self._settings.get("style_lock"),
                version=(prev.version + 1) if prev else 1,
                parent_id=prev.id if prev else None,
                is_active=True,
            )
            s.add(row)
            await s.flush()
            await s.commit()
            version = row.version
        await self._memory.clear(inb.user_id)
        await self._panel(inb, self._msgs.t("savec_saved", name=name, version=version)
        )

    # ---- interactive main menu (user rule: buttons over commands) ---------
    MENU_MAP: dict[str, str] = {
        "generate": "/generate",
        "inbox": "/inbox",
        "queue": "/queue",
        "pool": "/pool",
        "publish": "/publish",
        "settings": "/settings",
        "characters": "/characters",
        "pause": "/pause",
        "resume": "/resume",
        "help": "/help",
    }

    def _main_kb_rows(self) -> list[list[str]]:
        """The persistent bottom keyboard: 2 columns, stable order."""
        order = (
            "generate", "inbox", "queue", "pool",
            "characters", "publish", "settings", "help",
        )
        labels = [self._msgs.t(f"menu_{key}") for key in order]
        return [labels[i:i + 2] for i in range(0, len(labels), 2)]

    def _main_label_map(self) -> dict[str, str]:
        """Reply-keyboard label -> command (same map as the inline menu)."""
        mapping = {
            self._msgs.t(f"menu_{key}"): cmd for key, cmd in self.MENU_MAP.items()
        }
        mapping[self._msgs.t("menu_back")] = "/menu"
        return mapping

    async def _cmd_menu(self, inb: Inbound) -> None:
        rows = [
            (self._msgs.t(f"menu_{key}"), f"menu:{key}")
            for key in self.MENU_MAP
            if key not in ("pause", "resume")
        ]
        # Pause/Resume only where relevant (user 2026-10-16):
        # resume only while paused; pause only while work is happening.
        stats = await self._worker.queue_stats()
        busy = bool(stats.get("pending") or stats.get("running"))
        if self._worker.paused:
            rows.append((self._msgs.t("menu_resume"), "menu:resume"))
        elif busy:
            rows.append((self._msgs.t("menu_pause"), "menu:pause"))
        rows.append((self._msgs.t("menu_close"), "menu:close"))
        # the modern bottom keyboard (Stage 4) — kept BELOW the inline menu
        # so the inline menu stays the last message for the tests/viewers
        await self._gw.send_reply_keyboard(
            inb.chat_id,
            self._msgs.t("kb_ready"),
            self._main_kb_rows(),
            placeholder=self._msgs.t("kb_placeholder"),
        )
        await self._panel(inb, self._msgs.t("menu_title"), buttons=_chunk(rows)
        )

    async def _cb_menu(self, inb: Inbound, data: str) -> None:
        key = data.split(":", 1)[1]
        await self._safe_answer(inb)
        if key == "back":  # «منوی اصلی» — was silently dead (debug 2026-10-17)
            await self._cmd_menu(inb)
            return
        if key == "close":  # close the menu: hide keyboard + delete panel
            await self._panel_close(inb)
            return
        cmd = self.MENU_MAP.get(key)
        if cmd is None:
            return
        await self._command(inb, cmd)

    async def _savec_input(self, inb: Inbound, dialog: dict, text: str) -> None:
        step = dialog.get("step")
        if step == "name":
            dialog["name"] = text[:100]
            dialog["step"] = "choose"
            await self._memory.set(inb.user_id, dialog)
            buttons: Buttons = [
                [(self._msgs.t("savec_build_precise"), "savec:p")],
                [(self._msgs.t("savec_known"), "savec:k")],
                [(self._msgs.t("savec_use_job"), "savec:j"),
                 (self._msgs.t("savec_write_own"), "savec:m")],
            ]
            await self._panel(inb, self._msgs.t("savec_choose_ask"), buttons=buttons
            )
            return
        if step == "trait_extras":
            dialog["extras"] = text[:200]
            await self._savec_review(inb, dialog)
            return
        if step and step.startswith("trait_custom:"):
            trait = step.split(":", 1)[1]
            if trait not in TRAIT_ORDER:  # corrupted dialog state
                await self._memory.clear(inb.user_id)
                await self._panel(inb, self._msgs.t("wiz_stale")
                )
                return
            try:
                value = validate_trait_value(trait, text)
            except ValidationError as e:
                await self._panel(inb, self._msgs.t("savec_bad_value", error=str(e))
                )
                return
            dialog.setdefault("traits", {})[trait] = value
            nxt = self._next_trait(dialog)
            if nxt is None:
                dialog["step"] = "trait_extras"
                await self._memory.set(inb.user_id, dialog)
                await self._ask_extras(inb)
            else:
                dialog["step"] = f"trait:{nxt}"
                await self._memory.set(inb.user_id, dialog)
                await self._ask_trait(inb, dialog, nxt)
            return
        if step == "known_name":
            dialog["known_name"] = text[:100]
            dialog["name"] = text[:100]  # the character's real name
            dialog["step"] = "known_anime"
            await self._memory.set(inb.user_id, dialog)
            await self._panel(inb, self._msgs.t("savec_known_anime_ask")
            )
            return
        if step == "known_anime":
            dialog["known_anime"] = text[:100]
            await self._memory.set(inb.user_id, dialog)
            await self._savec_review(inb, dialog)
            return
        if step == "identity":  # manual path: text now, review before saving
            dialog["identity"] = (
                dialog.get("identity") or "" if text.lower() in OK_WORDS else text
            )
            dialog["precise"] = False
            await self._memory.set(inb.user_id, dialog)
            await self._savec_review(inb, dialog)

    # ---- publish / ambiguous resolution -----------------------------------
    async def _cb_publish(self, inb: Inbound, data: str) -> None:
        image_id = int(data.split(":")[1])
        async with self._sessions() as s:
            img = await s.get(Image, image_id)
            if img is None or img.status != "approved":
                await s.commit()
                await self._safe_answer(inb, self._msgs.t("publish_stale"))
                return
            name = await self._image_name(s, img)
            outcome = await publish_image(
                s,
                self._gw,
                image=img,
                chat_id=self._cfg.dest_chat_id,
                caption=self._msgs.t("post_caption", name=name),
                spoiler=bool(img.is_nsfw),
                storage_root=Path(self._cfg.storage_root),
            )
            await s.commit()
        await self._safe_answer(inb)
        if outcome.status == "sent":
            await self._panel(inb,
                self._msgs.t("publish_done", image_id=image_id, message_id=outcome.message_id),
            )
        elif outcome.status == "already":
            await self._panel(inb, self._msgs.t("publish_already"))
        elif outcome.status == "unknown":
            buttons: Buttons = [
                [
                    (self._msgs.t("publish_unknown_sent"), f"res:{outcome.post.id}:y"),
                    (self._msgs.t("publish_unknown_not"), f"res:{outcome.post.id}:n"),
                ]
            ]
            await self._panel(inb, self._msgs.t("publish_unknown"), buttons=buttons
            )
        else:
            await self._panel(inb,
                self._msgs.t("publish_failed", error=outcome.error or "unknown"),
            )

    async def _image_name(self, s: AsyncSession, img: Image) -> str:
        if img.character_id:
            row = await s.get(Character, img.character_id)
            if row is not None:
                return row.name
        return self._msgs.t("app_name")

    async def _cb_resolve(self, inb: Inbound, data: str) -> None:
        _, post_id, choice = data.split(":")
        async with self._sessions() as s:
            post = await s.get(Post, int(post_id))
            if post is None or post.status != "unknown":
                await s.commit()
                await self._safe_answer(inb, self._msgs.t("publish_stale"))
                return
            await resolve_unknown_send(
                s, post=post, was_sent=(choice == "y"),
                message_id=None,  # real id unknowable after the timeout
            )
            await s.commit()
        await self._safe_answer(inb)
        await self._panel(inb,
            self._msgs.t(
                "publish_resolved_sent" if choice == "y" else "publish_resolved_not"
            ),
        )

    # ---- settings / delete ------------------------------------------------
    async def _cb_setting(self, inb: Inbound, data: str) -> None:
        if data.startswith("setv:"):  # setv:<key>:<value> — a toggle press
            _, key, raw = data.split(":", 2)
            try:
                stored = await self._settings.set(
                    key, int(raw), updated_by=f"tg:{inb.user_id}"
                )
            except (ConfigError, ValueError) as e:
                await self._safe_answer(
                    inb, self._msgs.t("settings_invalid", error=str(e))
                )
                return
            await self._safe_answer(inb)
            await self._panel(
                inb, self._msgs.t("settings_saved", key=key, value=stored),
                buttons=[[(self._msgs.t("view_back"), "menu:back")]],
            )
            return
        key = data.split(":", 1)[1]
        spec = SPECS_BY_KEY.get(key)
        await self._safe_answer(inb)
        if (spec is not None and spec.kind == "int"
                and spec.minimum == 0 and spec.maximum == 1):
            # boolean-like setting: on/off BUTTONS (user rule 2026-10-17 —
            # never a free-text input for a simple toggle)
            buttons: Buttons = [[
                (self._msgs.t("settings_toggle_on"), f"setv:{key}:1"),
                (self._msgs.t("settings_toggle_off"), f"setv:{key}:0"),
            ], [(self._msgs.t("view_back"), "menu:back")]]
            await self._panel(inb, self._msgs.t("settings_toggle_ask", key=key),
                              buttons=buttons)
            return
        await self._memory.set(inb.user_id, {"kind": "set", "key": key})
        await self._panel(inb, self._msgs.t("settings_ask_value", key=key)
        )

    async def _set_input(self, inb: Inbound, dialog: dict, text: str) -> None:
        key = dialog.get("key") or ""
        try:
            value: object = json.loads(text)
        except (ValueError, TypeError):
            value = text
        try:
            stored = await self._settings.set(key, value, updated_by=f"tg:{inb.user_id}")
        except ConfigError as e:
            await self._panel(inb, self._msgs.t("settings_invalid", error=str(e))
            )
            return
        await self._memory.clear(inb.user_id)
        await self._panel(inb, self._msgs.t("settings_saved", key=key, value=stored)
        )

    async def _cb_delete(self, inb: Inbound, data: str) -> None:
        action, raw_id = data.split(":", 1)
        image_id = int(raw_id)
        if action == "del":
            buttons: Buttons = [
                [
                    (self._msgs.t("delete_yes"), f"dely:{image_id}"),
                    (self._msgs.t("wiz_cancel"), "noop"),
                ]
            ]
            await self._safe_answer(inb)
            await self._panel(inb, self._msgs.t("delete_ask"), buttons=buttons
            )
            return
        # dely: hard delete — file + rows (admin-only, confirmed).
        async with self._sessions() as s:
            img = await s.get(Image, image_id)
            if img is None:
                await s.commit()
                await self._safe_answer(inb, self._msgs.t("delete_stale"))
                return
            path = Path(self._cfg.storage_root) / img.file_path
            if path.is_file():
                path.unlink()
            for post in (
                await s.execute(select(Post).where(Post.image_id == image_id))
            ).scalars().all():
                await s.delete(post)
            await s.delete(img)
            await s.commit()
        await self._safe_answer(inb)
        await self._panel(inb, self._msgs.t("delete_done"))


def _chunk(rows: list[tuple[str, str]], size: int = 2) -> Buttons:
    return [rows[i : i + size] for i in range(0, len(rows), size)]
