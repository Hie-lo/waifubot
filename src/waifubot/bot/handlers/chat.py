"""Chat panel, persona editor, memory viewer, and chat mode routing handlers (R63-R66).

Contract:
- Chat mode overrides R6: typed text goes to companion character instead of generation wizard (R63).
- Chat mode ends on /endchat, bottom keyboard selection, or panel's end button (R63).
- Chat panel offers: End Chat, Persona viewer/editor, Memory facts & reset, Switch Character (R63, R64).
- AI Enhance in persona mode with preview and one-step undo (R62).
- Memory viewer allows viewing summary, deleting single durable facts, or full reset (R64).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.bot.callbacks import ChatCallback
from waifubot.bot.telegram_html import bold, code, escape_html
from waifubot.core.limits import Limits
from waifubot.core.persona import PersonaFields
from waifubot.core.ports import Enhancer
from waifubot.services.chat.engine import ChatDisabledError, ChatEngine
from waifubot.services.chat.session import (
    delete_memory_fact,
    get_or_create_session,
    load_chat_memory,
    reset_session_memory,
)
from waifubot.services.identity import get_current_character
from waifubot.services.ownership import get_character_ownership
from waifubot.services.personas import (
    ai_enhance_persona,
    get_or_create_persona,
    save_enhanced_persona,
    undo_persona_edit,
)


class ChatHandler:
    """Handles chat mode lifecycle, dialog routing, and in-chat panel management."""

    def __init__(self) -> None:
        # Maps user_id -> character_id when actively in chat mode (R63)
        self._active_chats: dict[int, str] = {}

    def is_in_chat(self, user_id: int) -> bool:
        """True if the user is currently in chat mode."""
        return user_id in self._active_chats

    def get_active_character(self, user_id: int) -> str | None:
        """Returns the character_id the user is currently conversing with."""
        return self._active_chats.get(user_id)

    def enter_chat(self, user_id: int, character_id: str) -> None:
        """Enters chat mode with target character (R63)."""
        self._active_chats[user_id] = character_id

    def exit_chat(self, user_id: int) -> str | None:
        """Exits chat mode, returning previously active character_id if any."""
        return self._active_chats.pop(user_id, None)

    async def render_chat_panel(
        self,
        session: AsyncSession,
        user_id: int,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders the in-chat management panel (R63)."""
        char = await get_current_character(session, character_id)
        ownership = await get_character_ownership(session, character_id)

        if char is None or ownership is None or ownership.owner_user_id != user_id:
            text = (
                "کاراکتر یافت نشد یا در مالکیت شما نیست."
                if locale == "fa"
                else "Character not found or not owned by you."
            )
            return text, []

        self.enter_chat(user_id, character_id)
        sess = await get_or_create_session(session, user_id, character_id)
        memory = await load_chat_memory(session, sess.session_id)

        lines = [
            f"💬 {bold(escape_html(char.name))} — "
            + ("حالت گفتگو فعال است" if locale == "fa" else "Chat mode active"),
            f"• شناسه: {code(character_id)}",
            f"• پیام‌های پنجره جاری: {len(memory.window)}",
            f"• فکت‌های ثبت‌شده: {len(memory.facts)}",
            "",
            "هر متنی که تایپ کنید مستقیماً برای کاراکتر ارسال می‌شود."
            if locale == "fa"
            else "Any text you type will be sent directly to the character.",
        ]

        end_cb = ChatCallback(action="end", char_id=character_id)
        persona_cb = ChatCallback(action="persona", char_id=character_id)
        mem_cb = ChatCallback(action="memory", char_id=character_id)
        switch_cb = ChatCallback(action="switch", char_id=character_id)

        keyboard = [
            [
                {
                    "text": "🎭 شخصیت (Persona)" if locale == "fa" else "🎭 Persona",
                    "callback_data": persona_cb.pack(),
                },
                {
                    "text": "🧠 حافظه (Memory)" if locale == "fa" else "🧠 Memory",
                    "callback_data": mem_cb.pack(),
                },
            ],
            [
                {
                    "text": "🔄 تعویض کاراکتر" if locale == "fa" else "🔄 Switch Character",
                    "callback_data": switch_cb.pack(),
                },
                {
                    "text": "❌ پایان گفتگو (/endchat)"
                    if locale == "fa"
                    else "❌ End Chat (/endchat)",
                    "callback_data": end_cb.pack(),
                },
            ],
        ]
        return "\n".join(lines), keyboard

    async def render_persona_panel(
        self,
        session: AsyncSession,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders persona viewer and AI enhancement controls (R62)."""
        persona = await get_or_create_persona(session, character_id)
        fields = PersonaFields.from_json(persona.fields_json)

        lines = [
            f"🎭 {bold('تنظیمات شخصیت (Persona)' if locale == 'fa' else 'Persona Settings')}",
            f"• نام نمایشی: {fields.display_name or '-'}",
            f"• خلق‌وخو: {fields.temperament or '-'}",
            f"• سبک بیان: {fields.speaking_style or '-'}",
            f"• نسبت با کاربر: {fields.relationship_to_user or 'friend'}",
            f"• علایق: {fields.likes or '-'}",
            f"• بیزاری‌ها: {fields.dislikes or '-'}",
            f"• خط قرمزها: {fields.avoid_topics or '-'}",
            f"• نسخه: {persona.version}",
        ]

        enhance_cb = ChatCallback(action="enhance", char_id=character_id)
        undo_cb = ChatCallback(action="undo", char_id=character_id)
        back_cb = ChatCallback(action="panel", char_id=character_id)

        keyboard = [
            [
                {
                    "text": "🪄 بهینه‌سازی با هوش مصنوعی"
                    if locale == "fa"
                    else "🪄 AI Enhance Persona",
                    "callback_data": enhance_cb.pack(),
                }
            ],
        ]
        if persona.prev_json:
            keyboard.append(
                [
                    {
                        "text": "↩️ لغو آخرین تغییر (Undo)"
                        if locale == "fa"
                        else "↩️ Undo Last Edit",
                        "callback_data": undo_cb.pack(),
                    }
                ]
            )
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت به گفتگو" if locale == "fa" else "🔙 Back to Chat",
                    "callback_data": back_cb.pack(),
                }
            ]
        )
        return "\n".join(lines), keyboard

    async def handle_ai_enhance_persona(
        self,
        session: AsyncSession,
        enhancer: Enhancer,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Executes AI Enhance on character persona and saves previous state for undo (R62)."""
        char = await get_current_character(session, character_id)
        char_name = char.name if char else ""

        _, new_fields = await ai_enhance_persona(
            session=session,
            enhancer=enhancer,
            character_id=character_id,
            char_name=char_name,
        )
        await save_enhanced_persona(session, character_id, new_fields)

        msg = (
            "✨ شخصیت کاراکتر با موفقیت بهینه‌سازی شد!"
            if locale == "fa"
            else "✨ Persona enhanced successfully!"
        )
        return msg, [
            [
                {
                    "text": "🎭 مشاهده شخصیت" if locale == "fa" else "🎭 View Persona",
                    "callback_data": ChatCallback(action="persona", char_id=character_id).pack(),
                }
            ]
        ]

    async def handle_undo_persona(
        self,
        session: AsyncSession,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Reverts to the previous persona state (R62)."""
        restored = await undo_persona_edit(session, character_id)
        if restored is not None:
            msg = (
                "↩️ تغییرات قبلی با موفقیت بازیابی شد."
                if locale == "fa"
                else "↩️ Previous persona restored successfully."
            )
        else:
            msg = "تغییری برای لغو وجود ندارد." if locale == "fa" else "No changes to undo."

        return msg, [
            [
                {
                    "text": "🎭 مشاهده شخصیت" if locale == "fa" else "🎭 View Persona",
                    "callback_data": ChatCallback(action="persona", char_id=character_id).pack(),
                }
            ]
        ]

    async def render_memory_panel(
        self,
        session: AsyncSession,
        user_id: int,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Renders memory inspector: rolling summary, durable facts, and deletion (R64)."""
        sess = await get_or_create_session(session, user_id, character_id)
        memory = await load_chat_memory(session, sess.session_id)

        lines = [
            f"🧠 {bold('حافظه بلندمدت گفتگو' if locale == 'fa' else 'Long-term Chat Memory')}",
            "",
            bold("خلاصه گفتگو:" if locale == "fa" else "Rolling Summary:"),
            memory.summary
            or ("(هنوز خلاصه‌ای ثبت نشده است)" if locale == "fa" else "(No summary yet)"),
            "",
            bold("فکت‌های ماندگار درباره شما:" if locale == "fa" else "Durable Facts About You:"),
        ]

        keyboard: list[list[dict[str, Any]]] = []

        if not memory.facts:
            lines.append("(هیچ فکتی ثبت نشده است)" if locale == "fa" else "(No facts recorded yet)")
        else:
            for i, fact in enumerate(memory.facts):
                lines.append(f"{i + 1}. {escape_html(fact)}")
                del_cb = ChatCallback(action="fact_del", char_id=character_id, extra=str(i))
                keyboard.append(
                    [
                        {
                            "text": f"🗑️ حذف فکت {i + 1}"
                            if locale == "fa"
                            else f"🗑️ Delete Fact {i + 1}",
                            "callback_data": del_cb.pack(),
                        }
                    ]
                )

        reset_cb = ChatCallback(action="mem_reset", char_id=character_id)
        back_cb = ChatCallback(action="panel", char_id=character_id)

        keyboard.append(
            [
                {
                    "text": "⚠️ پاک‌سازی کامل حافظه" if locale == "fa" else "⚠️ Reset All Memory",
                    "callback_data": reset_cb.pack(),
                }
            ]
        )
        keyboard.append(
            [
                {
                    "text": "🔙 بازگشت به گفتگو" if locale == "fa" else "🔙 Back to Chat",
                    "callback_data": back_cb.pack(),
                }
            ]
        )
        return "\n".join(lines), keyboard

    async def handle_delete_fact(
        self,
        session: AsyncSession,
        user_id: int,
        character_id: str,
        fact_index: int,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Deletes a single fact from session memory (R64)."""
        sess = await get_or_create_session(session, user_id, character_id)
        await delete_memory_fact(session, sess.session_id, fact_index)
        return await self.render_memory_panel(session, user_id, character_id, locale=locale)

    async def handle_reset_memory(
        self,
        session: AsyncSession,
        user_id: int,
        character_id: str,
        locale: str = "fa",
    ) -> tuple[str, list[list[dict[str, Any]]]]:
        """Resets all summary and facts for the session (R64)."""
        sess = await get_or_create_session(session, user_id, character_id)
        await reset_session_memory(session, sess.session_id)
        return await self.render_memory_panel(session, user_id, character_id, locale=locale)

    async def handle_incoming_text(
        self,
        session: AsyncSession,
        engine: ChatEngine,
        user_id: int,
        role: str,
        text: str,
        user_limits: Limits | None = None,
        chat_enabled: bool = True,
        locale: str = "fa",
    ) -> tuple[str | None, bool]:
        """Processes incoming typed text if user is currently in chat mode (R63).

        Returns:
            (reply_text, was_handled_as_chat)
        """
        if not self.is_in_chat(user_id):
            return None, False

        clean = text.strip()
        if clean in ("/endchat", "پایان گفتگو", "خروج از چت"):
            self.exit_chat(user_id)
            end_msg = (
                "گفتگو با کاراکتر به پایان رسید. حالت عادی فعال شد."
                if locale == "fa"
                else "Chat session ended. Standard bot mode resumed."
            )
            return end_msg, True

        char_id = self.get_active_character(user_id)
        if not char_id:
            self.exit_chat(user_id)
            return None, False

        try:
            reply = await engine.process_message(
                session=session,
                user_id=user_id,
                role=role,
                character_id=char_id,
                user_text=clean,
                user_limits=user_limits,
                chat_enabled=chat_enabled,
            )
            return reply, True
        except ChatDisabledError:
            self.exit_chat(user_id)
            return (
                "قابلیت گفتگو در حال حاضر غیرفعال است."
                if locale == "fa"
                else "Chat feature is currently disabled.",
                True,
            )
        except Exception as e:
            return f"خطا در پردازش پیام: {e}", True
