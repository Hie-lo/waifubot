"""Character ownership, claiming, catalog viewer, and My Characters UI handlers (R60, R61).

Contract:
- In-bot catalog: lists unclaimed characters (viewer, edits in place, R5).
- 🎲 Random unclaimed character with confirmation prompt before claiming (R61).
- Deep-link button support: /start char_<character_id> (R61).
- Atomic claiming with max_owned_characters in the same transaction (R61).
- Best-effort channel post edit on claim (R61).
- My Characters: private viewer with card, release option, and new version creation (R60).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import CatalogCallback, MyCharactersCallback
from waifubot.bot.telegram_html import bold, code, escape_html
from waifubot.core.i18n import t
from waifubot.core.limits import Limits
from waifubot.services.identity import get_current_character
from waifubot.services.ownership import (
    claim_character,
    get_character_ownership,
    get_random_unclaimed_character,
    list_catalog_characters,
    list_user_characters,
    release_character,
    update_claim_posts_best_effort,
)


class CharacterOwnershipUIHandler:
    """Renders catalog viewer, claiming flow, and My Characters management."""

    async def render_catalog(
        self,
        session: AsyncSession,
        page: int = 0,
        limit: int = 5,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders paged catalog of claimable characters (R61)."""
        offset = page * limit
        items, total_count = await list_catalog_characters(session, offset=offset, limit=limit)
        total_pages = max(1, (total_count + limit - 1) // limit)
        current_page = page + 1

        if not items:
            text = t("catalog_empty", locale=locale)
            keyboard = [
                [
                    {
                        "text": "🎲 کاراکتر تصادفی" if locale == "fa" else "🎲 Random Character",
                        "callback_data": CatalogCallback(action="rand").pack(),
                    }
                ]
            ]
            return text, keyboard

        title = t("catalog_title", locale=locale, page=current_page, total_pages=total_pages)
        lines = [f"📚 {bold(title)}", ""]

        keyboard: list[list[dict[str, Any]]] = []

        for char, _ in items:
            preview = char.appearance_facts[:80] + (
                "..." if len(char.appearance_facts) > 80 else ""
            )
            lines.append(f"• {bold(escape_html(char.name))}")
            lines.append(f"  {escape_html(preview)}")
            cb = CatalogCallback(action="view", char_id=char.character_id, page=page)
            keyboard.append(
                [
                    {
                        "text": f"👀 مشاهده و دریافت {char.name}"
                        if locale == "fa"
                        else f"👀 View & Claim {char.name}",
                        "callback_data": cb.pack(),
                    }
                ]
            )

        # Pagination and Random row
        nav_row: list[dict[str, Any]] = []
        if page > 0:
            prev_cb = CatalogCallback(action="page", page=page - 1)
            nav_row.append(
                {"text": "⬅️ قبلی" if locale == "fa" else "⬅️ Prev", "callback_data": prev_cb.pack()}
            )
        nav_row.append(
            {
                "text": "🎲 تصادفی" if locale == "fa" else "🎲 Random",
                "callback_data": CatalogCallback(action="rand").pack(),
            }
        )
        if current_page < total_pages:
            next_cb = CatalogCallback(action="page", page=page + 1)
            nav_row.append(
                {"text": "بعدی ➡️" if locale == "fa" else "Next ➡️", "callback_data": next_cb.pack()}
            )

        if nav_row:
            keyboard.append(nav_row)

        return "\n".join(lines), keyboard

    async def render_claim_card(
        self,
        session: AsyncSession,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders detailed card of an unclaimed character with Claim button."""
        char = await get_current_character(session, character_id)
        ownership = await get_character_ownership(session, character_id)

        if char is None or ownership is None or ownership.status != "listed":
            text = (
                "این کاراکتر موجود نیست یا قبلاً دریافت شده است."
                if locale == "fa"
                else "This character is not available or has already been claimed."
            )
            back_cb = CatalogCallback(action="page", page=0)
            return text, [
                [
                    {
                        "text": "🔙 بازگشت به کاتالوگ" if locale == "fa" else "🔙 Back to Catalog",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        lines = [
            f"👤 {bold(escape_html(char.name))}",
            f"• شناسه: {code(char.character_id)}",
            f"• حالت: {char.mode}",
            "",
            bold("ظاهر کاراکتر:" if locale == "fa" else "Appearance:"),
            escape_html(char.appearance_facts),
        ]

        claim_cb = CatalogCallback(action="claim", char_id=char.character_id)
        back_cb = CatalogCallback(action="page", page=0)
        keyboard = [
            [
                {
                    "text": "🤝 دریافت کاراکتر (Claim)" if locale == "fa" else "🤝 Claim Character",
                    "callback_data": claim_cb.pack(),
                }
            ],
            [
                {
                    "text": "🔙 بازگشت به کاتالوگ" if locale == "fa" else "🔙 Back to Catalog",
                    "callback_data": back_cb.pack(),
                }
            ],
        ]
        return "\n".join(lines), keyboard

    async def render_random_confirm(
        self,
        session: AsyncSession,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Selects a random unclaimed character and renders confirmation prompt (R61)."""
        random_pair = await get_random_unclaimed_character(session)
        if random_pair is None:
            text = t("catalog_empty", locale=locale)
            back_cb = CatalogCallback(action="page", page=0)
            return text, [
                [
                    {
                        "text": "🔙 بازگشت به کاتالوگ" if locale == "fa" else "🔙 Back to Catalog",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        char, _ = random_pair
        lines = [
            "🎲 "
            + (
                f"کاراکتر تصادفی انتخاب شد: {bold(escape_html(char.name))}"
                if locale == "fa"
                else f"Random character chosen: {bold(escape_html(char.name))}"
            ),
            "",
            escape_html(char.appearance_facts[:150]),
            "",
            "آیا مایل به دریافت این کاراکتر هستید؟"
            if locale == "fa"
            else "Do you want to claim this character?",
        ]

        claim_cb = CatalogCallback(action="claim", char_id=char.character_id)
        rerand_cb = CatalogCallback(action="rand")
        back_cb = CatalogCallback(action="page", page=0)
        keyboard = [
            [
                {
                    "text": "✅ تأیید و دریافت" if locale == "fa" else "✅ Confirm & Claim",
                    "callback_data": claim_cb.pack(),
                }
            ],
            [
                {
                    "text": "🎲 انتخاب یکی دیگر" if locale == "fa" else "🎲 Pick Another",
                    "callback_data": rerand_cb.pack(),
                }
            ],
            [
                {
                    "text": "🔙 بازگشت به کاتالوگ" if locale == "fa" else "🔙 Back to Catalog",
                    "callback_data": back_cb.pack(),
                }
            ],
        ]
        return "\n".join(lines), keyboard

    async def handle_claim(
        self,
        session: AsyncSession,
        character_id: str,
        user_id: int,
        user_limits: Limits | None = None,
        is_superadmin: bool = False,
        claim_enabled: bool = True,
        publisher: Any = None,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Processes atomic claiming and triggers best-effort button update (R61)."""
        char = await get_current_character(session, character_id)
        char_name = char.name if char else character_id

        success, reason = await claim_character(
            session=session,
            character_id=character_id,
            user_id=user_id,
            user_limits=user_limits,
            is_superadmin=is_superadmin,
            claim_enabled=claim_enabled,
        )

        if success:
            # Best effort channel post edit (R61)
            if publisher is not None:
                await update_claim_posts_best_effort(
                    session=session,
                    publisher=publisher,
                    character_id=character_id,
                    claimed_label="دریافت شده ✅" if locale == "fa" else "Claimed ✅",
                )

            msg = t("claim_success", locale=locale, name=char_name)
            myc_cb = MyCharactersCallback(action="view", char_id=character_id)
            cat_cb = CatalogCallback(action="page", page=0)
            keyboard = [
                [
                    {
                        "text": "🧬 مشاهده در کاراکترهای من"
                        if locale == "fa"
                        else "🧬 View in My Characters",
                        "callback_data": myc_cb.pack(),
                    }
                ],
                [
                    {
                        "text": "📚 بازگشت به کاتالوگ" if locale == "fa" else "📚 Back to Catalog",
                        "callback_data": cat_cb.pack(),
                    }
                ],
            ]
            return msg, keyboard

        # Handle failure cases
        if reason == "claim_disabled":
            msg = t("claim_disabled_notice", locale=locale)
        elif reason == "limit_reached":
            msg = t("claim_limit_reached", locale=locale)
        elif reason == "already_claimed":
            msg = t("claim_already_claimed", locale=locale, name=char_name)
        else:
            msg = (
                "خطایی در دریافت کاراکتر رخ داد."
                if locale == "fa"
                else "An error occurred while claiming the character."
            )

        cat_cb = CatalogCallback(action="page", page=0)
        keyboard = [
            [
                {
                    "text": "📚 بازگشت به کاتالوگ" if locale == "fa" else "📚 Back to Catalog",
                    "callback_data": cat_cb.pack(),
                }
            ]
        ]
        return msg, keyboard

    async def render_my_characters(
        self,
        session: AsyncSession,
        user_id: int,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders My Characters screen for the authenticated user (R60)."""
        owned = await list_user_characters(session, user_id)

        if not owned:
            text = t("my_characters_empty", locale=locale)
            keyboard = [
                [
                    {
                        "text": "📚 کاتالوگ کاراکترها"
                        if locale == "fa"
                        else "📚 Character Catalog",
                        "callback_data": CatalogCallback(action="page", page=0).pack(),
                    }
                ],
                [
                    {
                        "text": "🎲 کاراکتر تصادفی" if locale == "fa" else "🎲 Random Character",
                        "callback_data": CatalogCallback(action="rand").pack(),
                    }
                ],
            ]
            return text, keyboard

        title = t("my_characters_title", locale=locale, total=len(owned))
        lines = [f"🧬 {bold(title)}", ""]

        keyboard: list[list[dict[str, Any]]] = []

        for char, ownership in owned:
            tag = "🔒 اختصاصی" if ownership.status == "private" else "🤝 دریافت شده"
            if locale != "fa":
                tag = "🔒 Private" if ownership.status == "private" else "🤝 Claimed"

            lines.append(f"• {bold(escape_html(char.name))} ({tag})")
            cb = MyCharactersCallback(action="view", char_id=char.character_id)
            keyboard.append(
                [
                    {
                        "text": f"👤 {char.name} ({tag})",
                        "callback_data": cb.pack(),
                    }
                ]
            )

        keyboard.append(
            [
                {
                    "text": "📚 کاتالوگ کاراکترها" if locale == "fa" else "📚 Character Catalog",
                    "callback_data": CatalogCallback(action="page", page=0).pack(),
                }
            ]
        )

        return "\n".join(lines), keyboard

    async def render_my_character_detail(
        self,
        session: AsyncSession,
        character_id: str,
        user_id: int,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders detail card for a character owned by the user."""
        char = await get_current_character(session, character_id)
        ownership = await get_character_ownership(session, character_id)

        if char is None or ownership is None or ownership.owner_user_id != user_id:
            text = (
                "کاراکتر یافت نشد یا در مالکیت شما نیست."
                if locale == "fa"
                else "Character not found or not owned by you."
            )
            back_cb = MyCharactersCallback(action="list")
            return text, [
                [
                    {
                        "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        kind_str = "اختصاصی (Private)" if ownership.status == "private" else "دریافت شده (Claimed)"
        if locale != "fa":
            kind_str = "Private" if ownership.status == "private" else "Claimed"

        lines = [
            f"🧬 {bold(escape_html(char.name))}",
            f"• شناسه: {code(char.character_id)}",
            f"• وضعیت مالکیت: {kind_str}",
            f"• نسخه ظاهری: {char.version}",
            "",
            bold("ظاهر فعلی:" if locale == "fa" else "Current Appearance:"),
            escape_html(char.appearance_facts),
        ]

        chat_cb = MyCharactersCallback(action="chat", char_id=char.character_id)
        rel_cb = MyCharactersCallback(action="rel", char_id=char.character_id)
        back_cb = MyCharactersCallback(action="list")

        keyboard = [
            [
                {
                    "text": "💬 گفتگو (Chat)" if locale == "fa" else "💬 Chat",
                    "callback_data": chat_cb.pack(),
                }
            ],
            [
                {
                    "text": "🔓 رهاسازی کاراکتر" if locale == "fa" else "🔓 Release Character",
                    "callback_data": rel_cb.pack(),
                }
            ],
            [
                {
                    "text": "🔙 بازگشت به کاراکترهای من"
                    if locale == "fa"
                    else "🔙 Back to My Characters",
                    "callback_data": back_cb.pack(),
                }
            ],
        ]
        return "\n".join(lines), keyboard

    async def render_release_confirm(
        self,
        session: AsyncSession,
        character_id: str,
        user_id: int,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders confirmation prompt before releasing or deleting an owned character (R60)."""
        char = await get_current_character(session, character_id)
        ownership = await get_character_ownership(session, character_id)

        if char is None or ownership is None or ownership.owner_user_id != user_id:
            text = (
                "کاراکتر یافت نشد یا در مالکیت شما نیست."
                if locale == "fa"
                else "Character not found or not owned by you."
            )
            back_cb = MyCharactersCallback(action="list")
            return text, [
                [
                    {
                        "text": "🔙 بازگشت" if locale == "fa" else "🔙 Back",
                        "callback_data": back_cb.pack(),
                    }
                ]
            ]

        if ownership.status == "claimed":
            warning_text = (
                f"آیا مطمئن هستید که می‌خواهید کاراکتر {bold(escape_html(char.name))} را رها کنید؟\n"
                "با این کار، کاراکتر به کاتالوگ عمومی بازمی‌گردد و حافظه چت آن پاک خواهد شد."
                if locale == "fa"
                else f"Are you sure you want to release {bold(escape_html(char.name))}?\n"
                "It will return to the public catalog and its chat memory will be wiped."
            )
        else:
            warning_text = (
                f"آیا مطمئن هستید که می‌خواهید کاراکتر شخصی {bold(escape_html(char.name))} را حذف کنید؟\n"
                "این کاراکتر برای همیشه حذف خواهد شد."
                if locale == "fa"
                else f"Are you sure you want to delete private character {bold(escape_html(char.name))}?\n"
                "It will be permanently deleted."
            )

        confirm_cb = MyCharactersCallback(action="rel_confirm", char_id=char.character_id)
        cancel_cb = MyCharactersCallback(action="view", char_id=char.character_id)

        keyboard = [
            [
                {
                    "text": "⚠️ تأیید رهاسازی / حذف"
                    if locale == "fa"
                    else "⚠️ Confirm Release / Delete",
                    "callback_data": confirm_cb.pack(),
                }
            ],
            [
                {
                    "text": "🔙 انصراف" if locale == "fa" else "🔙 Cancel",
                    "callback_data": cancel_cb.pack(),
                }
            ],
        ]
        return warning_text, keyboard

    async def handle_release(
        self,
        session: AsyncSession,
        character_id: str,
        user_id: int,
        is_staff: bool = False,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Executes release/deletion of an owned character (R60)."""
        char = await get_current_character(session, character_id)
        char_name = char.name if char else character_id

        success, result_type = await release_character(
            session=session,
            character_id=character_id,
            user_id=user_id,
            is_staff=is_staff,
        )

        if not success:
            msg = (
                "خطایی در رهاسازی کاراکتر رخ داد."
                if locale == "fa"
                else "An error occurred while releasing the character."
            )
        elif result_type == "released_to_listed":
            msg = t("character_released_listed", locale=locale, name=char_name)
        else:
            msg = t("character_released_deleted", locale=locale, name=char_name)

        back_cb = MyCharactersCallback(action="list")
        keyboard = [
            [
                {
                    "text": "🔙 بازگشت به کاراکترهای من"
                    if locale == "fa"
                    else "🔙 Back to My Characters",
                    "callback_data": back_cb.pack(),
                }
            ]
        ]
        return msg, keyboard

    async def handle_deep_link(
        self,
        session: AsyncSession,
        payload: str,
        user_id: int,
        user_limits: Limits | None = None,
        is_superadmin: bool = False,
        claim_enabled: bool = True,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Handles /start char_<character_id> deep-link for instant character card/claim (R61)."""
        char_id = payload
        if not char_id.startswith("char_"):
            char_id = f"char_{payload}"

        return await self.render_claim_card(session=session, character_id=char_id, locale=locale)
