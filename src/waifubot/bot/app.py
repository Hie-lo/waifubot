"""Telegram Application wiring (Sprint 3, SPEC §4/§11).

One process, one event loop: the Telegram Application owns the loop; the
generation worker is a single asyncio task started in post-init and stopped in
post-shutdown. `PTBGateway` is the only place that touches the Telegram API —
everything else is testable with a fake gateway (SPEC §13: no network in tests).

Build is synchronous (no event loop yet); async startup work (recovery, worker)
runs in post_init INSIDE the Application's loop — never in a foreign loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from waifubot.bot.auth import AdminGate
from waifubot.bot.commands import BotApp, BotConfig, Inbound
from waifubot.bot.memory import DialogMemory
from waifubot.bot.messages import Messages
from waifubot.bot.recovery import startup_recover
from waifubot.bot.worker import GenerationWorker
from waifubot.config import Settings, ensure_runtime_dirs
from waifubot.core.admin_settings import AdminSettings
from waifubot.core.ai_enhance import build_enhancer
from waifubot.core.content_safety import load_blocklist
from waifubot.core.identity import load_identity_pool
from waifubot.core.image_pipeline import ImagePipeline
from waifubot.core.prompt_builder import PromptBuilder
from waifubot.core.providers import create_provider, load_provider_config
from waifubot.core.tags_pool import load_tags_pool
from waifubot.db.session import make_engine, make_session_factory
from waifubot.errors import ConfigError

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")

COMMANDS = (
    "start", "help", "status", "generate", "inbox", "queue", "pool",
    "publish", "settings", "pause", "resume", "characters",
)


class PTBGateway:
    """The production TelegramGateway (thin; no logic, never logs secrets)."""

    def __init__(self) -> None:
        self._bot: object | None = None

    def bind(self, bot: object) -> None:
        self._bot = bot

    def _require(self) -> object:
        if self._bot is None:
            raise RuntimeError("PTBGateway used before bind()")
        return self._bot

    async def send_message(
        self, chat_id: int | str, text: str, buttons: object = None
    ) -> int:
        msg = await self._require().send_message(
            chat_id=chat_id, text=text, reply_markup=_markup(buttons)
        )
        return msg.message_id

    async def send_reply_keyboard(
        self,
        chat_id: int | str,
        text: str,
        rows: list[list[str]],
        placeholder: str = "",
    ) -> int:
        markup = ReplyKeyboardMarkup(
            [[KeyboardButton(label) for label in row] for row in rows],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder=placeholder or None,
        )
        msg = await self._require().send_message(
            chat_id=chat_id, text=text, reply_markup=markup
        )
        return msg.message_id

    async def send_photo(
        self,
        chat_id: int | str,
        photo_path: Path,
        caption: str = "",
        spoiler: bool = False,
        buttons: object = None,
    ) -> int:
        with open(photo_path, "rb") as fh:
            msg = await self._require().send_photo(
                chat_id=chat_id,
                photo=fh,
                caption=caption or None,
                has_spoiler=spoiler,
                reply_markup=_markup(buttons),
            )
        return msg.message_id

    async def send_media_group(
        self,
        chat_id: int | str,
        items: list[tuple[Path, bool]],
        caption: str = "",
    ) -> list[int]:
        from contextlib import ExitStack

        from telegram import InputMediaPhoto

        with ExitStack() as stack:
            media = []
            for i, (path, spoiler) in enumerate(items):
                fh = stack.enter_context(open(path, "rb"))
                media.append(
                    InputMediaPhoto(
                        media=fh,
                        caption=(caption or None) if i == 0 else None,
                        has_spoiler=bool(spoiler),
                    )
                )
            msgs = await self._require().send_media_group(chat_id=chat_id, media=media)
        return [m.message_id for m in msgs]

    async def send_document(
        self, chat_id: int | str, doc_path: Path, caption: str = "", buttons: object = None
    ) -> int:
        with open(doc_path, "rb") as fh:
            msg = await self._require().send_document(
                chat_id=chat_id, document=fh, caption=caption or None,
                reply_markup=_markup(buttons),
            )
        return msg.message_id

    async def edit_message(
        self, chat_id: int | str, message_id: int, text: str, buttons: object = None
    ) -> None:
        await self._require().edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=_markup(buttons)
        )

    async def edit_message_media(
        self,
        chat_id: int | str,
        message_id: int,
        photo_path: Path,
        caption: str = "",
        buttons: object = None,
    ) -> None:
        from telegram import InputMediaPhoto

        with open(photo_path, "rb") as fh:
            await self._require().edit_message_media(
                media=InputMediaPhoto(media=fh, caption=caption or None),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=_markup(buttons),
            )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._require().answer_callback_query(callback_id, text=text or None)


def _markup(buttons: object) -> object:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in buttons
        ]
    )


def _inbound(update: Update) -> Inbound:
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message else ""
    data = update.callback_query.data if update.callback_query else None
    return Inbound(
        user_id=user.id if user else 0,
        chat_id=chat.id if chat else 0,
        text=text or "",
        data=data,
        update_id=update.update_id,
        callback_id=update.callback_query.id if update.callback_query else "",
        message_id=(
            update.callback_query.message.message_id
            if update.callback_query and update.callback_query.message
            else None
        ),
    )


def build_app(settings: Settings) -> tuple[Application, GenerationWorker, BotApp]:
    """Build everything synchronously; raises ConfigError on any config problem."""
    if not settings.provider_enabled:
        raise ConfigError("PROVIDER_ENABLED must be true for the bot")
    if not TOKEN_RE.match(settings.bot_token or ""):
        raise ConfigError("BOT_TOKEN format is invalid")
    ensure_runtime_dirs(settings)

    engine = make_engine(settings.db_url)  # lazy: no connections before the loop
    sessions = make_session_factory(engine)

    provider_cfg = load_provider_config(REPO_ROOT / "config" / "providers" / "perchance.yaml")
    provider = create_provider(provider_cfg)
    pipeline = ImagePipeline(settings.storage_root)
    blocklist = load_blocklist(REPO_ROOT / "config" / "blocklist.yaml")
    idpool = load_identity_pool(REPO_ROOT / "config" / "identity_pool.yaml")
    builder = PromptBuilder(load_tags_pool(REPO_ROOT / "config" / "tags_pool.yaml"), blocklist)
    msgs = Messages(REPO_ROOT / "locales", os.environ.get("LANGUAGE", "fa"))
    gate = AdminGate(settings.admin_user_ids)
    memory = DialogMemory(sessions)
    admin_settings = AdminSettings(sessions)

    gateway = PTBGateway()
    worker = GenerationWorker(
        sessions=sessions,
        provider=provider,
        pipeline=pipeline,
        gateway=gateway,
        msgs=msgs,
        admin_ids=settings.admin_user_ids,
        storage_root=settings.storage_root,
        disk_floor_bytes=settings.disk_floor_bytes,
    )
    bot_app = BotApp(
        sessions=sessions,
        admin_settings=admin_settings,
        memory=memory,
        gateway=gateway,
        msgs=msgs,
        worker=worker,
        gate=gate,
        builder=builder,
        identity_pool=idpool,
        enhancer=build_enhancer(
            settings.anthropic_api_key, settings.ai_enhance_model
        ),
        config=BotConfig(
            admin_ids=settings.admin_user_ids,
            dest_chat_id=settings.destination_chat_id,
            allow_nsfw=settings.allow_nsfw,
            storage_root=str(settings.storage_root),
            style_values=dict(provider_cfg.get("style_values") or {}),
            blocklist=blocklist,
        ),
    )

    async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await bot_app.handle_message(_inbound(update))

    async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await bot_app.handle_callback(_inbound(update))

    async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("telegram update failed", exc_info=ctx.error)

    async def post_init(_app: Application) -> None:
        # Async startup INSIDE the Application's loop (SPEC §10 recovery).

        from waifubot.db.models import Setting

        await startup_recover(sessions, settings.storage_root)
        async with sessions() as s:
            row = await s.get(Setting, "runtime_paused")
            if row is not None and row.value_json in ("true", "True"):
                worker.pause()
        worker.start()
        log.info("bot started (worker running, paused=%s)", worker.paused)

    async def post_shutdown(_app: Application) -> None:
        await worker.stop()
        await engine.dispose()
        log.info("bot stopped (worker and browser closed)")

    app = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    gateway.bind(app.bot)
    app.add_handler(CommandHandler(list(COMMANDS), on_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    app.add_handler(CallbackQueryHandler(on_callback))
    return app, worker, bot_app


def run_bot(settings: Settings) -> int:
    """Run polling until interrupted (SPEC §11: polling only — no webhooks)."""
    app, _worker, _bot_app = build_app(settings)
    # Migration (asyncio.run in alembic env) leaves "no current event loop";
    # PTB's run_polling needs one on Python 3.10+ (RuntimeError otherwise).
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except NetworkError as e:
        print(f"error [network]: cannot reach Telegram: {e}", file=sys.stderr)
        return 1
    except InvalidToken:
        print(
            "error [config_error]: BOT_TOKEN was rejected by Telegram "
            "(valid format, wrong token - check @BotFather)",
            file=sys.stderr,
        )
        return 2
    return 0
