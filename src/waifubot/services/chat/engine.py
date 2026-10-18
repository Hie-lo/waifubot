"""Core chat engine pipeline with concurrency controls, safety, and compaction (R63-R66).

Contract:
- Overrides prompt generation mode in DM: typed text is chat (R63).
- Input & output pass safety rules (R26, R66); failed output replaced with in-character refusal.
- Daily chat quota enforced and charged (R54).
- Global concurrency semaphore (chat_max_concurrency = 4) + per-session lock (R65).
- Dual-condition compaction triggers (every N turns or token overflow) (R64).
- System instructions + persona card cached via prompt caching (R65).
- Privacy: logs never contain message text (R46, R66).
"""

import asyncio
import time

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.limits import Limits
from waifubot.core.persona import build_system_prefix
from waifubot.core.ports import ChatModel
from waifubot.core.safety import assert_safe_prompt
from waifubot.services.chat.session import (
    get_or_create_session,
    load_chat_memory,
    save_chat_memory,
)
from waifubot.services.personas import get_or_create_persona
from waifubot.services.quota import check_and_reserve, record_chat_tokens


class ChatDisabledError(Exception):
    """Raised when chat is globally disabled via settings."""


class ChatEngine:
    """Executes the dialogue pipeline between an owner and their companion character."""

    def __init__(
        self,
        chat_model: ChatModel,
        max_concurrency: int = 4,
    ) -> None:
        self.chat_model = chat_model
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._global_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    async def process_message(
        self,
        session: AsyncSession,
        user_id: int,
        role: str,
        character_id: str,
        user_text: str,
        user_limits: Limits | None = None,
        chat_enabled: bool = True,
        max_reply_tokens: int = 300,
        chat_window_turns: int = 8,
        chat_compact_every_turns: int = 10,
        chat_facts_max: int = 20,
        memory_tokens_cap: int = 500,
        app_timezone: str = "Asia/Tehran",
    ) -> str:
        """Processes a chat turn following the R63-R66 pipeline."""
        # 1. Master toggle check
        if not chat_enabled:
            raise ChatDisabledError("chat_disabled")

        # 2. Input safety verification (R26, R66)
        assert_safe_prompt(user_text)

        # 3. Quota check & reservation (R54)
        if user_limits is not None:
            await check_and_reserve(
                session=session,
                user_id=user_id,
                role=role,
                limits=user_limits,
                kind="chat",
                amount=1,
                app_timezone=app_timezone,
            )

        # 4. Resolve session & locks
        sess_record = await get_or_create_session(session, user_id, character_id)
        session_lock = await self._get_session_lock(sess_record.session_id)

        async with session_lock, self.semaphore:
            # 6. Load persona and compile prefix
            persona_record = await get_or_create_persona(session, character_id)
            system_prefix = build_system_prefix(persona_record.card)

            # 7. Load chat memory
            memory = await load_chat_memory(
                session=session,
                session_id=sess_record.session_id,
                persona_card=persona_record.card,
                memory_tokens_cap=memory_tokens_cap,
            )

            # 8. Dual-condition memory compaction (R64)
            if (
                memory.should_compact(
                    max_turns_before_compact=chat_compact_every_turns,
                    window_token_budget=800,
                )
                and len(memory.window) > chat_window_turns
            ):
                split_idx = len(memory.window) - chat_window_turns
                leaving = [t.to_dict() for t in memory.window[:split_idx]]
                if leaving:
                    compaction_data = await self.chat_model.compact_memory(
                        old_summary=memory.summary,
                        durable_facts=memory.facts,
                        leaving_turns=leaving,
                        max_tokens=memory_tokens_cap,
                    )
                    memory.apply_compaction(
                        compaction_dict=compaction_data,
                        turns_compacted_count=len(leaving),
                        chat_facts_max=chat_facts_max,
                    )

            # 9. Format prompt messages
            messages: list[dict[str, str]] = []
            mem_context = memory.to_prompt_context()
            if mem_context:
                messages.append(
                    {
                        "role": "user",
                        "content": f"[Context from previous interactions:\n{mem_context}]",
                    }
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": "Understood. I will stay in character and keep this context in mind.",
                    }
                )

            for turn in memory.window:
                messages.append({"role": turn.role, "content": turn.content})

            messages.append({"role": "user", "content": user_text})

            # 10. Model completion call with prompt caching (R65)
            reply_text, usage = await self.chat_model.complete(
                system_prefix=system_prefix,
                messages=messages,
                max_reply_tokens=max_reply_tokens,
            )

            # 11. Output safety check (R66)
            try:
                assert_safe_prompt(reply_text)
            except Exception:
                # In-character refusal without logging message text (R66)
                reply_text = "متأسفم، اما نمی‌توانم در این رابطه گفتگو کنم."

            # 12. Update memory and usage counters
            now = time.time()
            memory.append_turn(role="user", content=user_text, timestamp=now)
            memory.append_turn(role="assistant", content=reply_text, timestamp=now)

            await save_chat_memory(session, memory)
            await record_chat_tokens(
                session=session,
                user_id=user_id,
                tokens_in=usage.get("input_tokens", 0),
                tokens_out=usage.get("output_tokens", 0),
                app_timezone=app_timezone,
            )

            sess_record.last_at = now
            await session.flush()

            return reply_text
