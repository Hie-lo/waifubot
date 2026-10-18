"""Composition root for WaifuBot application.

Wires adapters to core ports and domain services.
This is the only module in bot/ permitted to import from waifubot.adapters.
"""

from dataclasses import dataclass
from typing import Any

from waifubot.adapters.ai_factory import create_chat_model, create_enhancer
from waifubot.adapters.clock import SystemClock
from waifubot.adapters.providers.mock import MockGenerationProvider, MockSlotPool
from waifubot.adapters.storage.memory import MemoryImageStore
from waifubot.core.ports import (
    ChatModel,
    Clock,
    Enhancer,
    GenerationProvider,
    ImageStore,
    ProviderSlotPool,
)
from waifubot.services.generation import GenerationService


@dataclass
class AppConfig:
    """Application runtime configuration."""

    bot_token: str = "mock-token"
    storage_dir: str = "data/images"
    admin_ids: tuple[int, ...] = (1001,)
    default_provider: str = "mock"


class BotContainer:
    """Dependency injection container holding initialized services and adapters."""

    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        slot_pool: ProviderSlotPool,
        provider: GenerationProvider,
        image_store: ImageStore,
        enhancer: Enhancer | None = None,
        chat_model: ChatModel | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.slot_pool = slot_pool
        self.provider = provider
        self.image_store = image_store
        self.enhancer = enhancer
        self.chat_model = chat_model

        # Initialize application services
        self.generation_service = GenerationService(clock=self.clock)


def create_app(
    config: AppConfig | None = None,
    clock: Clock | None = None,
    slot_pool: ProviderSlotPool | None = None,
    provider: GenerationProvider | None = None,
    image_store: ImageStore | None = None,
    enhancer: Enhancer | None = None,
    chat_model: ChatModel | None = None,
) -> BotContainer:
    """Composition root factory: builds the container with supplied or default adapters."""
    resolved_config = config or AppConfig()
    resolved_clock = clock or SystemClock()
    resolved_slot_pool = slot_pool or MockSlotPool(size=4)
    resolved_provider = provider or MockGenerationProvider(name=resolved_config.default_provider)
    resolved_store = image_store or MemoryImageStore()
    resolved_enhancer = enhancer if enhancer is not None else create_enhancer()
    resolved_chat_model = chat_model if chat_model is not None else create_chat_model()

    return BotContainer(
        config=resolved_config,
        clock=resolved_clock,
        slot_pool=resolved_slot_pool,
        provider=resolved_provider,
        image_store=resolved_store,
        enhancer=resolved_enhancer,
        chat_model=resolved_chat_model,
    )


def build_bot_app(
    token: str | None = None,
    db_url: str | None = None,
    admin_ids: list[int] | None = None,
) -> Any:
    """Builds and wires the Telegram Application with handlers, DB, and services."""
    import asyncio
    import contextlib
    import os
    import sys
    from typing import Any

    from sqlalchemy import select
    from telegram import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        KeyboardButton,
        ReplyKeyboardMarkup,
        Update,
    )
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    from waifubot.bot.auth import (
        check_access_and_notify,
        get_role_reply_keyboard_buttons,
        verify_callback_authorization,
    )
    from waifubot.bot.callbacks import (
        AccountCallback,
        AdminCallback,
        CatalogCallback,
        ChannelCallback,
        ChatCallback,
        GenActionCallback,
        GenCountCallback,
        MyCharactersCallback,
        MyImagesCallback,
        NavCallback,
        PlanCallback,
        StudioCharCallback,
        StudioOptCallback,
        SystemCallback,
        UserCallback,
        registry,
    )
    from waifubot.bot.handlers.admins import AdminManagementHandler
    from waifubot.bot.handlers.channels import ChannelsHandler
    from waifubot.bot.handlers.chat import ChatHandler
    from waifubot.bot.handlers.generate import SpecialCharacterStudioHandler
    from waifubot.bot.handlers.ownership import CharacterOwnershipUIHandler
    from waifubot.bot.handlers.plans import PlansHandler
    from waifubot.bot.handlers.status import format_status_message
    from waifubot.bot.handlers.system import SystemManagementHandler
    from waifubot.bot.handlers.user_generation import UserGenerationHandler
    from waifubot.bot.handlers.users import UserManagementHandler
    from waifubot.bot.panel import PanelEngine
    from waifubot.bot.telegram_html import bold, escape_html
    from waifubot.config import load_env
    from waifubot.core.i18n import t
    from waifubot.db.session import create_engine_and_sessionmaker, init_db
    from waifubot.services.admins import consume_invite, seed_admins
    from waifubot.services.chat.engine import ChatEngine
    from waifubot.services.ownership import list_user_characters
    from waifubot.services.settings import SettingsService
    from waifubot.services.status import collect_system_status
    from waifubot.services.system import RestartController

    load_env()

    resolved_token = token or os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not resolved_token or resolved_token in ("mock-token", "your_bot_token_here"):
        print(
            "ERROR: BOT_TOKEN is not configured in .env. Please set a valid Telegram bot token.",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_db_url = (
        db_url or os.environ.get("DATABASE_URL") or "sqlite+aiosqlite:///storage/waifubot.db"
    )
    if admin_ids is None:
        admin_ids_str = os.environ.get("ADMIN_USER_IDS") or ""
        resolved_admin_ids: list[int] = []
        for part in admin_ids_str.split(","):
            part = part.strip()
            if part.isdigit():
                resolved_admin_ids.append(int(part))
    else:
        resolved_admin_ids = admin_ids

    engine, session_factory = create_engine_and_sessionmaker(database_url=resolved_db_url)

    from waifubot.adapters.browser.pool import BrowserSlotPool
    from waifubot.adapters.providers.perchance import PerchanceProvider
    from waifubot.adapters.storage.local_fs import LocalFsImageStore
    from waifubot.services.workers import WorkerPool

    headless_env = os.environ.get("PROVIDER_HEADLESS")
    if headless_env is None:
        headless_env = os.environ.get("BROWSER_HEADLESS", "true")
    is_headless = headless_env.lower() in ("true", "1", "yes")

    browser_slot_pool = BrowserSlotPool(
        max_contexts=int(os.environ.get("MAX_BROWSER_CONTEXTS", 2)),
        storage_state_dir="storage/browser_sessions",
        headless=is_headless,
    )
    perchance_provider = PerchanceProvider(
        vision_lab_url=os.environ.get("VISION_LAB_URL", "https://perchance.org/vision-lab-studio"),
        text_to_image_url=os.environ.get(
            "PROVIDER_PAGE_URL", "https://perchance.org/ai-text-to-image-generator"
        ),
    )
    image_store = LocalFsImageStore(root_dir="storage/images")

    container = create_app(
        config=AppConfig(
            bot_token=resolved_token,
            admin_ids=tuple(resolved_admin_ids),
        ),
        slot_pool=browser_slot_pool,
        provider=perchance_provider,
        image_store=image_store,
    )
    settings_service = SettingsService()

    def to_inline_markup(keyboard_data: Any) -> InlineKeyboardMarkup | None:
        if isinstance(keyboard_data, InlineKeyboardMarkup):
            return keyboard_data
        if not keyboard_data or not isinstance(keyboard_data, list):
            return None
        rows: list[list[InlineKeyboardButton]] = []
        for row in keyboard_data:
            buttons: list[InlineKeyboardButton] = []
            for btn in row:
                if isinstance(btn, InlineKeyboardButton):
                    buttons.append(btn)
                elif isinstance(btn, dict):
                    btn_text = btn.get("text", "")
                    cb = btn.get("callback_data")
                    url = btn.get("url")
                    if url:
                        buttons.append(InlineKeyboardButton(text=btn_text, url=url))
                    elif cb is not None:
                        buttons.append(InlineKeyboardButton(text=btn_text, callback_data=cb))
            if buttons:
                rows.append(buttons)
        return InlineKeyboardMarkup(rows) if rows else None

    def to_reply_markup(button_rows: list[list[str]]) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(text=btn) for btn in row] for row in button_rows],
            resize_keyboard=True,
        )

    class TelegramGatewayAdapter:
        def __init__(self, bot: Any) -> None:
            self.bot = bot

        async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> Any:
            markup = (
                to_inline_markup(reply_markup) if isinstance(reply_markup, list) else reply_markup
            )
            try:
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            except Exception:
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=markup,
                )

        async def edit_message_text(
            self, chat_id: int, message_id: int, text: str, reply_markup: Any = None
        ) -> Any:
            markup = (
                to_inline_markup(reply_markup) if isinstance(reply_markup, list) else reply_markup
            )
            try:
                return await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            except Exception as exc:
                err = str(exc).lower()
                if "message is not modified" in err:
                    raise
                return await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=markup,
                )

        async def delete_message(self, chat_id: int, message_id: int) -> bool:
            try:
                return bool(await self.bot.delete_message(chat_id=chat_id, message_id=message_id))
            except Exception:
                return False

    async def send_user_photo(chat_id: int, photo: bytes, caption: str = "") -> None:
        import io
        import logging

        logger = logging.getLogger(__name__)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                bio = io.BytesIO(photo)
                bio.name = "image.png"
                await application.bot.send_photo(
                    chat_id=chat_id,
                    photo=bio,
                    caption=caption,
                    write_timeout=120.0,
                    read_timeout=120.0,
                    connect_timeout=60.0,
                )
                logger.info("Successfully sent photo to chat %s (attempt %s)", chat_id, attempt)
                return
            except Exception as exc:
                logger.warning(
                    "Attempt %s/%s failed to send photo to chat %s: %s",
                    attempt,
                    max_retries,
                    chat_id,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2.0 * attempt)
                else:
                    with contextlib.suppress(Exception):
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"⚠️ خطا در ارسال تصویر به تلگرام: {exc}\n"
                                "لطفاً اتصال اینترنت، فیلترشکن یا پروکسی ربات را بررسی نمایید."
                            ),
                        )

    async def send_user_message(chat_id: int, text: str) -> int | None:
        try:
            msg = await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                write_timeout=60.0,
                read_timeout=60.0,
                connect_timeout=30.0,
            )
            return msg.message_id
        except Exception:
            return None

    async def edit_user_message(chat_id: int, message_id: int, text: str) -> None:
        with contextlib.suppress(Exception):
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
            )

    async def on_post_init(app: Application) -> None:
        await init_db(engine)
        if resolved_admin_ids:
            async with session_factory() as session:
                await seed_admins(session, resolved_admin_ids)
                await session.commit()

        restart_ctrl = RestartController()
        pending = restart_ctrl.get_restart_pending()
        if pending:
            with contextlib.suppress(Exception):
                await app.bot.edit_message_text(
                    chat_id=pending["chat_id"],
                    message_id=pending["message_id"],
                    text="✅ ربات با موفقیت راه‌اندازی مجدد شد.",
                )
            restart_ctrl.clear_restart_pending()

        worker_pool = WorkerPool(
            session_factory=session_factory,
            slot_pool=container.slot_pool,
            provider=container.provider,
            generation_service=container.generation_service,
            settings_service=settings_service,
            image_store=container.image_store,
            telegram_sender=send_user_photo,
            telegram_message_sender=send_user_message,
            telegram_message_editor=edit_user_message,
        )
        worker_pool.start()
        app.bot_data["worker_pool"] = worker_pool

    async def on_post_shutdown(app: Application) -> None:
        wp = app.bot_data.get("worker_pool")
        if wp:
            await wp.stop()
        if hasattr(container.slot_pool, "close"):
            await container.slot_pool.close()

    from telegram.request import HTTPXRequest

    proxy_url = (
        os.environ.get("TELEGRAM_PROXY_URL")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
    )
    request_cfg = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=60.0,
        read_timeout=120.0,
        write_timeout=120.0,
        media_write_timeout=120.0,
        pool_timeout=60.0,
        proxy=proxy_url if proxy_url else None,
    )

    application = (
        ApplicationBuilder()
        .token(resolved_token)
        .request(request_cfg)
        .post_init(on_post_init)
        .post_shutdown(on_post_shutdown)
        .build()
    )
    application.bot_data["session_factory"] = session_factory

    gateway = TelegramGatewayAdapter(application.bot)
    panel_engine = PanelEngine(telegram_gateway=gateway)
    studio_handler = SpecialCharacterStudioHandler(panel_engine=panel_engine)
    ownership_handler = CharacterOwnershipUIHandler()
    chat_handler = ChatHandler()
    user_gen_handler = UserGenerationHandler()
    channels_handler = ChannelsHandler()
    admin_handler = AdminManagementHandler()
    user_mgmt_handler = UserManagementHandler()
    plans_handler = PlansHandler()
    system_handler = SystemManagementHandler()
    chat_engine = ChatEngine(chat_model=container.chat_model)

    async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat or not update.message:
            return

        chat_handler.exit_chat(user.id)
        studio_handler.wizard_store.clear(user.id, chat.id)

        session_fac = context.bot_data["session_factory"]
        async with session_fac() as session:
            allowed, ctx = await check_access_and_notify(
                session=session,
                telegram_id=user.id,
                name=user.full_name or user.first_name or f"User_{user.id}",
                username=user.username,
                bot=context.bot,
            )
            if not allowed:
                return

            if context.args:
                arg = context.args[0]
                if arg.startswith("invite_"):
                    raw_token = arg[len("invite_") :]
                    try:
                        await consume_invite(session, raw_token, user.id)
                        await session.commit()
                        _, ctx = await check_access_and_notify(
                            session=session,
                            telegram_id=user.id,
                            name=user.full_name or user.first_name or f"User_{user.id}",
                            username=user.username,
                            bot=context.bot,
                        )
                        await update.message.reply_text(
                            "🎉 شما با موفقیت به عنوان ادمین اضافه شدید!"
                            if ctx.locale == "fa"
                            else "🎉 You have been successfully added as an admin!"
                        )
                    except Exception as exc:
                        await update.message.reply_text(f"⚠️ {exc}")
                elif arg.startswith("char_"):
                    char_id = arg[len("char_") :]
                    text, kb = await ownership_handler.render_claim_card(
                        session, char_id, locale=ctx.locale
                    )
                    await update.message.reply_text(
                        text,
                        reply_markup=to_inline_markup(kb),
                        parse_mode="HTML",
                    )
                    return

            buttons = get_role_reply_keyboard_buttons(ctx)
            reply_markup = to_reply_markup(buttons)
            welcome_text = (
                f"👋 سلام {bold(escape_html(user.first_name or 'کاربر'))}!\n\n"
                f"به ربات WaifuBot خوش آمدید.\n"
                f"از منوی زیر می‌توانید بخش مورد نظر خود را انتخاب کنید."
                if ctx.locale == "fa"
                else f"👋 Hello {bold(escape_html(user.first_name or 'User'))}!\n\n"
                f"Welcome to WaifuBot.\n"
                f"Please choose an option from the menu below."
            )
            await update.message.reply_text(
                welcome_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )

    async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            studio_handler.wizard_store.clear(user.id, chat.id)
            chat_handler.exit_chat(user.id)
        if update.message:
            await update.message.reply_text("عملیات جاری لغو شد.")

    async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.message:
            return

        session_fac = context.bot_data["session_factory"]
        async with session_fac() as session:
            allowed, ctx = await check_access_and_notify(
                session=session,
                telegram_id=user.id,
                name=user.full_name or user.first_name or f"User_{user.id}",
                username=user.username,
                bot=context.bot,
            )
            if not allowed:
                return

            status_data = await collect_system_status(
                session=session,
                settings_service=settings_service,
                slot_pool=container.slot_pool,
            )
            msg_text = format_status_message(status_data, locale=ctx.locale)
            await update.message.reply_text(msg_text, parse_mode="HTML")

    async def on_endchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not update.message:
            return

        chat_handler.exit_chat(user.id)
        if chat:
            studio_handler.wizard_store.clear(user.id, chat.id)

        session_fac = context.bot_data["session_factory"]
        async with session_fac() as session:
            allowed, ctx = await check_access_and_notify(
                session=session,
                telegram_id=user.id,
                name=user.full_name or user.first_name or f"User_{user.id}",
                username=user.username,
                bot=context.bot,
            )
            if not allowed:
                return

            msg = (
                "گفتگو با کاراکتر به پایان رسید. حالت عادی فعال شد."
                if ctx.locale == "fa"
                else "Chat session ended. Standard bot mode resumed."
            )
            await update.message.reply_text(msg)

    async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        msg = update.message
        if not user or not chat or not msg or not msg.text:
            return

        raw_text = msg.text.strip()
        session_fac = context.bot_data["session_factory"]
        async with session_fac() as session:
            allowed, ctx = await check_access_and_notify(
                session=session,
                telegram_id=user.id,
                name=user.full_name or user.first_name or f"User_{user.id}",
                username=user.username,
                bot=context.bot,
            )
            if not allowed:
                return

            loc = ctx.locale
            btn_gen = t("btn_generate", locale=loc)
            btn_chars = t("btn_characters", locale=loc)
            btn_rev = t("btn_review", locale=loc)
            btn_pol = t("btn_pool", locale=loc)
            btn_sch = t("btn_schedules", locale=loc)
            btn_sett = t("btn_settings", locale=loc)
            btn_my_img = t("btn_my_images", locale=loc)
            btn_my_chr = t("btn_my_characters", locale=loc)
            btn_cht = t("btn_chat", locale=loc)
            btn_acc = t("btn_my_account", locale=loc)

            menu_buttons = {
                btn_gen,
                btn_chars,
                btn_rev,
                btn_pol,
                btn_sch,
                btn_sett,
                btn_my_img,
                btn_my_chr,
                btn_cht,
                btn_acc,
                "🎨 تولید تصویر",
                "👥 کاراکترها",
                "🖼 تصاویر من",
                "🧬 کاراکترهای من",
                "💬 گفتگو",
                "👤 حساب کاربری من",
                "⚙️ تنظیمات",
            }

            if raw_text in ("/cancel", "لغو", "انصراف"):
                chat_handler.exit_chat(user.id)
                studio_handler.wizard_store.clear(user.id, chat.id)
                await msg.reply_text("عملیات جاری لغو شد.")
                return

            if raw_text in menu_buttons:
                chat_handler.exit_chat(user.id)
                studio_handler.wizard_store.clear(user.id, chat.id)

            if raw_text in (btn_gen, "🎨 تولید تصویر"):
                studio_handler.wizard_store.clear(user.id, chat.id)
                text, kb = studio_handler.render_mode_selection_panel()
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="mode_select",
                    force_new=True,
                )
                return

            if raw_text in (btn_chars, "👥 کاراکترها"):
                text, kb = await ownership_handler.render_catalog(session, page=0, locale=loc)
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="catalog",
                    force_new=True,
                )
                return

            if raw_text in (btn_my_chr, "🧬 کاراکترهای من"):
                text, kb = await ownership_handler.render_my_characters(
                    session, user.id, page=0, locale=loc
                )
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="my_characters",
                    force_new=True,
                )
                return

            if raw_text in (btn_my_img, "🖼 تصاویر من"):
                text, kb, _ = await user_gen_handler.render_my_images_viewer(
                    session, user_id=user.id, page=0, locale=loc
                )
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="my_images",
                    force_new=True,
                )
                return

            if raw_text in (btn_acc, "👤 حساب کاربری من"):
                text, kb = await user_gen_handler.render_my_account(
                    session, user_id=user.id, role=ctx.role, locale=loc
                )
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="account",
                    force_new=True,
                )
                return

            if raw_text in (btn_cht, "💬 گفتگو"):
                my_chars = await list_user_characters(session, user.id)
                if not my_chars:
                    empty_msg = (
                        "شما هنوز هیچ کاراکتری ندارید! ابتدا از بخش «👥 کاراکترها» یک کاراکتر دریافت کنید."
                        if loc == "fa"
                        else "You don't own any characters yet! Please claim one from '👥 Characters'."
                    )
                    await msg.reply_text(empty_msg)
                    return
                target_char = my_chars[0]
                text, kb = await chat_handler.render_chat_panel(
                    session=session,
                    user_id=user.id,
                    character_id=target_char.character_id,
                    locale=loc,
                )
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="chat",
                    force_new=True,
                )
                return

            if raw_text in (btn_sett, "⚙️ تنظیمات"):
                if not ctx.is_staff:
                    await msg.reply_text(t("not_authorized", locale=loc))
                    return
                text = f"⚙️ {bold('تنظیمات مدیریت' if loc == 'fa' else 'Staff Settings')}"
                kb = [
                    [
                        {
                            "text": "👑 ادمین‌ها",
                            "callback_data": AdminCallback(action="list").pack(),
                        },
                        {
                            "text": "👥 کاربران",
                            "callback_data": UserCallback(action="overview").pack(),
                        },
                    ],
                    [
                        {
                            "text": "📋 پلن‌ها",
                            "callback_data": PlanCallback(action="list").pack(),
                        },
                        {
                            "text": "📢 کانال‌ها",
                            "callback_data": ChannelCallback(action="list").pack(),
                        },
                    ],
                    [
                        {"text": "⚙️ سیستم", "callback_data": SystemCallback(action="menu").pack()},
                        {"text": "❌ بستن", "callback_data": NavCallback(action="close").pack()},
                    ],
                ]
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="settings",
                    force_new=True,
                )
                return

            if chat_handler.is_in_chat(user.id):
                reply, handled = await chat_handler.handle_incoming_text(
                    session=session,
                    engine=chat_engine,
                    user_id=user.id,
                    role=ctx.role,
                    text=raw_text,
                    user_limits=ctx.limits,
                    locale=loc,
                )
                if handled and reply:
                    await msg.reply_text(reply)
                return

            wizard_state = studio_handler.wizard_store.get(user.id, chat.id)
            if wizard_state:
                if wizard_state.mode == "custom" and wizard_state.step in (
                    "prompt_input",
                    "edit_prompt",
                ):
                    wizard_state.custom_prompt = raw_text
                    wizard_state.step = "count_select"
                    studio_handler.wizard_store.set(wizard_state)
                    text, kb = studio_handler.render_text_to_image_panel(wizard_state)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="txt2img",
                        force_new=True,
                    )
                    return
                elif wizard_state.mode == "special_studio":
                    accepted, feedback = studio_handler.handle_typed_text(wizard_state, raw_text)
                    if not accepted and feedback:
                        await msg.reply_text(feedback)
                    return

    async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        await panel_engine.answer_callback_instant(query)

        session_fac = context.bot_data["session_factory"]
        async with session_fac() as session:
            allowed, ctx = await check_access_and_notify(
                session=session,
                telegram_id=user.id,
                name=user.full_name or user.first_name or f"User_{user.id}",
                username=user.username,
                bot=context.bot,
            )
            if not allowed:
                return

            if not verify_callback_authorization(ctx, query.data):
                await panel_engine.answer_callback_instant(
                    query, text=t("not_authorized", locale=ctx.locale), show_alert=True
                )
                return

            try:
                cb = registry.parse(query.data)
            except Exception:
                return

            loc = ctx.locale

            if isinstance(cb, NavCallback):
                if cb.action == "close":
                    studio_handler.wizard_store.clear(user.id, chat.id)
                    await panel_engine.close_panel(user.id, chat.id)
                elif cb.action == "back":
                    if cb.target == "settings":
                        text = f"⚙️ {bold('تنظیمات مدیریت' if loc == 'fa' else 'Staff Settings')}"
                        kb = [
                            [
                                {
                                    "text": "👑 ادمین‌ها",
                                    "callback_data": AdminCallback(action="list").pack(),
                                },
                                {
                                    "text": "👥 کاربران",
                                    "callback_data": UserCallback(action="overview").pack(),
                                },
                            ],
                            [
                                {
                                    "text": "📋 پلن‌ها",
                                    "callback_data": PlanCallback(action="list").pack(),
                                },
                                {
                                    "text": "📢 کانال‌ها",
                                    "callback_data": ChannelCallback(action="list").pack(),
                                },
                            ],
                            [
                                {
                                    "text": "⚙️ سیستم",
                                    "callback_data": SystemCallback(action="menu").pack(),
                                },
                                {
                                    "text": "❌ بستن",
                                    "callback_data": NavCallback(action="close").pack(),
                                },
                            ],
                        ]
                        await panel_engine.render_panel(
                            admin_id=user.id,
                            chat_id=chat.id,
                            text=text,
                            reply_markup=kb,
                            screen="settings",
                        )
                    elif cb.target == "catalog":
                        text, kb = await ownership_handler.render_catalog(
                            session, page=0, locale=loc
                        )
                        await panel_engine.render_panel(
                            admin_id=user.id,
                            chat_id=chat.id,
                            text=text,
                            reply_markup=kb,
                            screen="catalog",
                        )

            elif isinstance(cb, StudioCharCallback):
                state = studio_handler.wizard_store.get(
                    user.id, chat.id
                ) or studio_handler.start_studio_wizard(user.id, chat.id)
                if cb.char_index == -1:
                    text, kb = studio_handler.render_studio_panel(state, char_page=cb.page)
                else:
                    state = studio_handler.select_character(state, index=cb.char_index)
                    text, kb = studio_handler.render_studio_panel(state)
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="studio",
                )

            elif isinstance(cb, StudioOptCallback):
                state = studio_handler.wizard_store.get(user.id, chat.id)
                if state:
                    if cb.item_index == -2:
                        text, kb = studio_handler.render_studio_panel(state, category_key="")
                    elif cb.item_index == -1:
                        text, kb = studio_handler.render_studio_panel(
                            state, category_key=cb.category_key, opt_page=cb.page
                        )
                    else:
                        cat_items = studio_handler.categories.get(cb.category_key, [])
                        if 0 <= cb.item_index < len(cat_items):
                            item = cat_items[cb.item_index]
                            opt_val = item.get("prompt", item.get("name", ""))
                            state = studio_handler.toggle_option(
                                state, category_key=cb.category_key, option_val=opt_val
                            )
                        text, kb = studio_handler.render_studio_panel(
                            state, category_key=cb.category_key, opt_page=cb.page
                        )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="studio",
                    )

            elif isinstance(cb, GenCountCallback):
                state = studio_handler.wizard_store.get(user.id, chat.id)
                if state:
                    state = studio_handler.set_image_count(state, cb.count)
                    if state.mode == "custom":
                        text, kb = studio_handler.render_text_to_image_panel(state)
                        screen = "txt2img"
                    else:
                        text, kb = studio_handler.render_studio_panel(state)
                        screen = "studio"
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen=screen,
                    )

            elif isinstance(cb, GenActionCallback):
                if cb.action == "mode_vl":
                    state = studio_handler.start_studio_wizard(user.id, chat.id)
                    text, kb = studio_handler.render_studio_panel(state)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="studio",
                    )
                elif cb.action == "mode_txt":
                    from waifubot.bot.wizard import WizardState

                    state = WizardState(
                        user_id=user.id,
                        chat_id=chat.id,
                        mode="custom",
                        step="prompt_input",
                    )
                    studio_handler.wizard_store.set(state)
                    text, kb = studio_handler.render_text_to_image_panel(state)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="txt2img",
                    )
                elif cb.action == "mode_picker":
                    studio_handler.wizard_store.clear(user.id, chat.id)
                    text, kb = studio_handler.render_mode_selection_panel()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="mode_select",
                    )
                elif cb.action == "edit_prompt":
                    state = studio_handler.wizard_store.get(user.id, chat.id)
                    if state:
                        state.custom_prompt = ""
                        state.step = "prompt_input"
                        studio_handler.wizard_store.set(state)
                        text, kb = studio_handler.render_text_to_image_panel(state)
                        await panel_engine.render_panel(
                            admin_id=user.id,
                            chat_id=chat.id,
                            text=text,
                            reply_markup=kb,
                            screen="txt2img",
                        )
                elif cb.action in ("pick_saved_char", "saved_char_pg"):
                    from waifubot.services.ownership import list_user_characters

                    page = int(cb.nonce) if cb.nonce and cb.nonce.isdigit() else 0
                    owned_chars = await list_user_characters(session, user.id)
                    text, kb = studio_handler.render_saved_characters_panel(
                        owned_characters=owned_chars, page=page
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="saved_chars",
                    )
                elif cb.action == "sel_char":
                    from waifubot.bot.wizard import WizardState
                    from waifubot.db.models import CharacterRecord

                    state = studio_handler.wizard_store.get(user.id, chat.id)
                    if not state:
                        state = WizardState(user_id=user.id, chat_id=chat.id, mode="custom")

                    char_id = cb.nonce
                    c_stmt = select(CharacterRecord).where(
                        CharacterRecord.character_id == char_id,
                        CharacterRecord.is_current.is_(True),
                    )
                    c_res = await session.execute(c_stmt)
                    char_obj = c_res.scalar_one_or_none()
                    char_name = char_obj.name if char_obj else ""
                    if char_obj:
                        char_facts = f"{char_obj.name}, {char_obj.appearance_facts}".strip().strip(
                            ","
                        )
                        if state.custom_prompt.strip():
                            state.custom_prompt = f"{char_facts}, {state.custom_prompt.strip()}"
                        else:
                            state.custom_prompt = char_facts
                        state.step = "ready"
                        studio_handler.wizard_store.set(state)

                    text, kb = studio_handler.render_text_to_image_panel(state)
                    status_prefix = (
                        f"✅ <i>کاراکتر «{char_name}» به پرامپت اضافه شد.</i>\n\n"
                        if char_name
                        else ""
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=f"{status_prefix}{text}",
                        reply_markup=kb,
                        screen="txt2img",
                    )
                elif cb.action == "enhance":
                    state = studio_handler.wizard_store.get(user.id, chat.id)
                    if not state:
                        return
                    is_custom = state.mode == "custom"
                    base_text = state.custom_prompt if is_custom else state.compose_prompt()
                    if not base_text or not base_text.strip():
                        warn_msg = (
                            "⚠️ لطفاً ابتدا متن پرامپت یا گزینه‌های کاراکتر را انتخاب کنید."
                            if loc == "fa"
                            else "⚠️ Please enter a prompt or choose options first."
                        )
                        text, kb = (
                            studio_handler.render_text_to_image_panel(state)
                            if is_custom
                            else studio_handler.render_studio_panel(state)
                        )
                        await panel_engine.render_panel(
                            admin_id=user.id,
                            chat_id=chat.id,
                            text=f"{warn_msg}\n\n{text}",
                            reply_markup=kb,
                            screen="txt2img" if is_custom else "studio",
                        )
                        return

                    enhancer = getattr(container, "enhancer", None)
                    if enhancer is None:
                        from waifubot.adapters.ai_factory import create_enhancer

                        enhancer = create_enhancer()

                    try:
                        enhanced_text = await asyncio.wait_for(
                            enhancer.enhance_prompt(scene=base_text),
                            timeout=30.0,
                        )
                        from waifubot.core.safety import recheck_enhancer_output

                        final_prompt, is_safe, reason = recheck_enhancer_output(
                            base_text, enhanced_text
                        )
                        if is_safe and final_prompt:
                            state.custom_prompt = final_prompt
                            studio_handler.wizard_store.set(state)
                            text, kb = (
                                studio_handler.render_text_to_image_panel(state)
                                if is_custom
                                else studio_handler.render_studio_panel(state)
                            )
                            await panel_engine.render_panel(
                                admin_id=user.id,
                                chat_id=chat.id,
                                text=f"✨ <i>پرامپت با هوش مصنوعی ارتقا یافت:</i>\n\n{text}",
                                reply_markup=kb,
                                screen="txt2img" if is_custom else "studio",
                            )
                        else:
                            err_reason = reason or "محتوای نامناسب تشخیص داده شد."
                            text, kb = (
                                studio_handler.render_text_to_image_panel(state)
                                if is_custom
                                else studio_handler.render_studio_panel(state)
                            )
                            await panel_engine.render_panel(
                                admin_id=user.id,
                                chat_id=chat.id,
                                text=f"⚠️ بهینه‌سازی انجام نشد ({err_reason})\n\n{text}",
                                reply_markup=kb,
                                screen="txt2img" if is_custom else "studio",
                            )
                    except Exception as exc:
                        text, kb = (
                            studio_handler.render_text_to_image_panel(state)
                            if is_custom
                            else studio_handler.render_studio_panel(state)
                        )
                        await panel_engine.render_panel(
                            admin_id=user.id,
                            chat_id=chat.id,
                            text=f"⚠️ خطا در ارتباط با هوش مصنوعی ({exc})\n\n{text}",
                            reply_markup=kb,
                            screen="txt2img" if is_custom else "studio",
                        )
                else:
                    state = studio_handler.wizard_store.get(user.id, chat.id)
                    if state:
                        if cb.action == "change_char":
                            state.selected_character = ""
                            state.step = "character_select"
                            studio_handler.wizard_store.set(state)
                            text, kb = studio_handler.render_studio_panel(state)
                            await panel_engine.render_panel(
                                admin_id=user.id,
                                chat_id=chat.id,
                                text=text,
                                reply_markup=kb,
                                screen="studio",
                            )
                        elif cb.action == "submit":
                            is_vl = state.mode == "special_studio"
                            prompt = state.compose_prompt()
                            if not prompt.strip():
                                text, kb = (
                                    studio_handler.render_studio_panel(state)
                                    if is_vl
                                    else studio_handler.render_text_to_image_panel(state)
                                )
                                await panel_engine.render_panel(
                                    admin_id=user.id,
                                    chat_id=chat.id,
                                    text="⚠️ پرامپت خالی است. لطفاً گزینه‌ها یا پرامپت را مشخص کنید.\n\n"
                                    + text,
                                    reply_markup=kb,
                                    screen="studio" if is_vl else "txt2img",
                                )
                                return
                            job_scope = "vision_lab" if is_vl else "user"
                            try:
                                job = await user_gen_handler.create_user_job(
                                    session=session,
                                    user_id=user.id,
                                    prompt=prompt,
                                    target_count=state.target_count,
                                    role=ctx.role,
                                    limits=ctx.limits,
                                    job_scope=job_scope,
                                )
                                await session.commit()
                                studio_handler.wizard_store.clear(user.id, chat.id)
                                mode_name = (
                                    "استودیوی کاراکترهای ویژه (Vision Lab)"
                                    if is_vl
                                    else "تبدیل متن به تصویر (Text-to-Image)"
                                )
                                prompt_summary = (
                                    escape_html(prompt[:250]) + "..."
                                    if len(prompt) > 250
                                    else escape_html(prompt)
                                )
                                confirm_text = (
                                    f"🚀 <b>درخواست تولید تصویر با موفقیت ثبت شد!</b>\n\n"
                                    f"🆔 <b>شناسه کار:</b> <code>{job.job_id}</code>\n"
                                    f"🔢 <b>تعداد تصاویر:</b> <b>{state.target_count}</b>\n"
                                    f"⚙️ <b>مدل:</b> <i>{mode_name}</i>\n"
                                    f"📝 <b>پرامپت:</b>\n<code>{prompt_summary}</code>\n\n"
                                    f"⏳ <i>درخواست شما در صف پردازش قرار گرفت. به محض آماده شدن، تصاویر به همین چت ارسال خواهند شد.</i>"
                                    if loc == "fa"
                                    else (
                                        f"🚀 <b>Generation job queued successfully!</b>\n\n"
                                        f"🆔 <b>Job ID:</b> <code>{job.job_id}</code>\n"
                                        f"🔢 <b>Count:</b> <b>{state.target_count}</b>\n"
                                        f"⚙️ <b>Mode:</b> <i>{mode_name}</i>\n"
                                        f"📝 <b>Prompt:</b>\n<code>{prompt_summary}</code>\n\n"
                                        f"⏳ <i>Your job is being processed. Images will be sent here upon completion.</i>"
                                    )
                                )
                                kb = [
                                    [
                                        {
                                            "text": "🎨 ساخت تصویر جدید",
                                            "callback_data": GenActionCallback(
                                                action="mode_picker"
                                            ).pack(),
                                        },
                                        {
                                            "text": "❌ بستن",
                                            "callback_data": NavCallback(action="close").pack(),
                                        },
                                    ]
                                ]
                                await panel_engine.render_panel(
                                    admin_id=user.id,
                                    chat_id=chat.id,
                                    text=confirm_text,
                                    reply_markup=kb,
                                    screen="submitted",
                                )
                            except Exception as exc:
                                text, kb = (
                                    studio_handler.render_studio_panel(state)
                                    if is_vl
                                    else studio_handler.render_text_to_image_panel(state)
                                )
                                await panel_engine.render_panel(
                                    admin_id=user.id,
                                    chat_id=chat.id,
                                    text=f"⚠️ <b>خطا در ثبت درخواست:</b>\n{escape_html(str(exc))}\n\n{text}",
                                    reply_markup=kb,
                                    screen="error",
                                )

            elif isinstance(cb, CatalogCallback):
                if cb.action == "page":
                    text, kb = await ownership_handler.render_catalog(
                        session, page=cb.page, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="catalog",
                    )
                elif cb.action == "view":
                    text, kb = await ownership_handler.render_claim_card(
                        session, character_id=cb.char_id, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="claim_card",
                    )
                elif cb.action == "rand":
                    text, kb = await ownership_handler.render_random_confirm(session, locale=loc)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="claim_rand",
                    )
                elif cb.action == "claim":
                    text, kb = await ownership_handler.handle_claim(
                        session,
                        character_id=cb.char_id,
                        user_id=user.id,
                        limits=ctx.limits,
                        locale=loc,
                    )
                    await session.commit()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="claim_result",
                    )

            elif isinstance(cb, MyCharactersCallback):
                if cb.action in ("list", "page"):
                    page = int(cb.extra) if cb.extra.isdigit() else 0
                    text, kb = await ownership_handler.render_my_characters(
                        session, user_id=user.id, page=page, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="my_characters",
                    )
                elif cb.action == "view":
                    text, kb = await ownership_handler.render_my_character_card(
                        session, character_id=cb.char_id, user_id=user.id, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="my_char_card",
                    )
                elif cb.action == "rel_ask":
                    text, kb = await ownership_handler.render_release_confirm(
                        session, character_id=cb.char_id, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="release_confirm",
                    )
                elif cb.action == "rel_do":
                    text, kb = await ownership_handler.handle_release(
                        session, character_id=cb.char_id, user_id=user.id, locale=loc
                    )
                    await session.commit()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="release_result",
                    )

            elif isinstance(cb, ChatCallback):
                if cb.action == "panel":
                    text, kb = await chat_handler.render_chat_panel(
                        session, user.id, cb.char_id, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="chat",
                    )
                elif cb.action == "end":
                    chat_handler.exit_chat(user.id)
                    await panel_engine.close_panel(user.id, chat.id)
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text="گفتگو با کاراکتر به پایان رسید." if loc == "fa" else "Chat ended.",
                    )
                elif cb.action == "persona":
                    text, kb = await chat_handler.render_persona_panel(
                        session, cb.char_id, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="persona",
                    )
                elif cb.action == "enhance":
                    if container.enhancer is not None:
                        text, kb = await chat_handler.handle_ai_enhance_persona(
                            session, container.enhancer, cb.char_id, locale=loc
                        )
                        await session.commit()
                        await panel_engine.render_panel(
                            admin_id=user.id,
                            chat_id=chat.id,
                            text=text,
                            reply_markup=kb,
                            screen="enhance",
                        )
                elif cb.action == "undo":
                    text, kb = await chat_handler.handle_undo_persona(
                        session, cb.char_id, locale=loc
                    )
                    await session.commit()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="undo",
                    )
                elif cb.action == "memory":
                    text, kb = await chat_handler.render_memory_panel(
                        session, user.id, cb.char_id, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="memory",
                    )
                elif cb.action == "fact_del":
                    fact_idx = int(cb.extra) if cb.extra.isdigit() else 0
                    text, kb = await chat_handler.handle_delete_fact(
                        session, user.id, cb.char_id, fact_idx, locale=loc
                    )
                    await session.commit()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="memory",
                    )
                elif cb.action == "mem_reset":
                    text, kb = await chat_handler.handle_reset_memory(
                        session, user.id, cb.char_id, locale=loc
                    )
                    await session.commit()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="memory",
                    )
                elif cb.action == "switch":
                    text, kb = await ownership_handler.render_my_characters(
                        session, user.id, page=0, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="my_characters",
                    )

            elif isinstance(cb, MyImagesCallback):
                if cb.action in ("page", "view"):
                    text, kb, _ = await user_gen_handler.render_my_images_viewer(
                        session, user.id, page=cb.page, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="my_images",
                    )
                elif cb.action == "del":
                    await user_gen_handler.delete_user_image(
                        session, user_id=user.id, image_id=int(cb.img_id)
                    )
                    await session.commit()
                    text, kb, _ = await user_gen_handler.render_my_images_viewer(
                        session, user.id, page=cb.page, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="my_images",
                    )

            elif isinstance(cb, AccountCallback):
                text, kb = await user_gen_handler.render_my_account(
                    session, user_id=user.id, role=ctx.role, locale=loc
                )
                await panel_engine.render_panel(
                    admin_id=user.id,
                    chat_id=chat.id,
                    text=text,
                    reply_markup=kb,
                    screen="account",
                )

            elif isinstance(cb, AdminCallback):
                if cb.action == "list":
                    text, kb = await admin_handler.render_admin_list(session, locale=loc)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="admins",
                    )
                elif cb.action == "invite":
                    text, kb = await admin_handler.handle_create_invite(
                        session, created_by=user.id, locale=loc
                    )
                    await session.commit()
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="admins",
                    )

            elif isinstance(cb, UserCallback):
                if cb.action == "overview":
                    text, kb = await user_mgmt_handler.render_overview(session, locale=loc)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="users",
                    )

            elif isinstance(cb, PlanCallback):
                if cb.action == "list":
                    text, kb = await plans_handler.render_plans_list(session, locale=loc)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="plans",
                    )

            elif isinstance(cb, ChannelCallback):
                if cb.action == "list":
                    text, kb = await channels_handler.render_channels_list(
                        session, user.id, ctx.role, locale=loc
                    )
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="channels",
                    )

            elif isinstance(cb, SystemCallback):
                if cb.action in ("menu", "cancel"):
                    text, kb = system_handler.render_system_menu(locale=loc)
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="system",
                    )
                elif cb.action == "restart_workers":
                    restart_ctrl = RestartController()
                    await restart_ctrl.restart_workers(
                        worker_pool=None, actor_id=user.id, session=session
                    )
                    await session.commit()
                    await panel_engine.answer_callback_instant(
                        query,
                        text="🔄 کارگرها ریستارت شدند." if loc == "fa" else "Workers restarted.",
                        show_alert=True,
                    )
                elif cb.action == "confirm_bot_restart":
                    text = (
                        "⚠️ آیا از راه‌اندازی مجدد کامل ربات مطمئن هستید؟"
                        if loc == "fa"
                        else "⚠️ Are you sure you want to restart the bot?"
                    )
                    kb = [
                        [
                            {
                                "text": "✅ بله، ریستارت کن",
                                "callback_data": SystemCallback(action="do_bot_restart").pack(),
                            }
                        ],
                        [
                            {
                                "text": "❌ انصراف",
                                "callback_data": SystemCallback(action="menu").pack(),
                            }
                        ],
                    ]
                    await panel_engine.render_panel(
                        admin_id=user.id,
                        chat_id=chat.id,
                        text=text,
                        reply_markup=kb,
                        screen="restart_confirm",
                    )
                elif cb.action == "do_bot_restart":
                    restart_ctrl = RestartController()
                    await restart_ctrl.request_bot_restart(
                        requested_by=user.id,
                        chat_id=chat.id,
                        message_id=query.message.message_id if query.message else 0,
                        session=session,
                        exit_fn=lambda code: sys.exit(code),
                    )

    # Register handlers with PTB application
    application.add_handler(CommandHandler("start", on_start))
    application.add_handler(CommandHandler("cancel", on_cancel))
    application.add_handler(CommandHandler("status", on_status))
    application.add_handler(CommandHandler("endchat", on_endchat))
    application.add_handler(CallbackQueryHandler(on_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))

    return application


def run_bot() -> None:
    """Entrypoint to launch the Telegram bot polling loop."""
    import logging
    import os

    from waifubot.core.logging import setup_logging

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    setup_logging(log_file="storage/waifubot.log", level=log_level, to_console=True)

    logger = logging.getLogger("waifubot")
    logger.info("Initializing WaifuBot application...")

    application = build_bot_app()
    logger.info("Starting WaifuBot Telegram bot polling loop...")
    application.run_polling()


if __name__ == "__main__":
    run_bot()
