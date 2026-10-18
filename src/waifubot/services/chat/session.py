"""Chat session persistence, memory loading, fact editing, and privacy wiping (R63, R64, R66).

Contract:
- Sessions are scoped to (owner, character) in private DM (R63).
- Memory stored in chat_memory: rolling summary, durable facts, and last window turns (R64).
- Raw dialogue history is never stored beyond the window (R64).
- Memory, facts, and sessions deleted when character released or deleted (R60, R66).
- Memory deleted when user is deleted (R50, R66).
"""

import json
import time
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.chat_memory import ChatMemory, ChatTurn
from waifubot.db.models import ChatMemoryRecord, ChatSessionRecord


async def get_or_create_session(
    session: AsyncSession,
    user_id: int,
    character_id: str,
) -> ChatSessionRecord:
    """Retrieves existing chat session or creates a new one for (user, character)."""
    stmt = select(ChatSessionRecord).where(
        ChatSessionRecord.user_id == user_id,
        ChatSessionRecord.character_id == character_id,
    )
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record is not None:
        return record

    sess_id = f"sess_{uuid.uuid4().hex[:12]}"
    now = time.time()
    record = ChatSessionRecord(
        session_id=sess_id,
        user_id=user_id,
        character_id=character_id,
        last_at=now,
        created_at=now,
    )
    session.add(record)
    await session.flush()
    return record


async def load_chat_memory(
    session: AsyncSession,
    session_id: str,
    persona_card: str = "",
    memory_tokens_cap: int = 500,
) -> ChatMemory:
    """Loads ChatMemory dataclass from the database."""
    stmt = select(ChatMemoryRecord).where(ChatMemoryRecord.session_id == session_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()

    if record is None:
        return ChatMemory(
            session_id=session_id,
            persona_card=persona_card,
            summary="",
            facts=[],
            window=[],
            chat_memory_tokens_cap=memory_tokens_cap,
        )

    # Reconstruct turns from window_json
    try:
        raw_turns = json.loads(record.window_json)
        window = [
            ChatTurn(
                role=t.get("role", "user"),
                content=t.get("content", ""),
                timestamp=t.get("timestamp", 0.0),
            )
            for t in raw_turns
            if isinstance(t, dict)
        ]
    except Exception:
        window = []

    try:
        facts = json.loads(record.facts_json)
        if not isinstance(facts, list):
            facts = []
    except Exception:
        facts = []

    return ChatMemory(
        session_id=session_id,
        persona_card=persona_card,
        summary=record.summary,
        facts=[str(f) for f in facts],
        window=window,
        chat_memory_tokens_cap=memory_tokens_cap,
    )


async def save_chat_memory(
    session: AsyncSession,
    memory: ChatMemory,
) -> ChatMemoryRecord:
    """Saves updated ChatMemory back to database record."""
    stmt = select(ChatMemoryRecord).where(ChatMemoryRecord.session_id == memory.session_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()

    now = time.time()
    window_data = [t.to_dict() for t in memory.window]
    window_json = json.dumps(window_data, ensure_ascii=False)
    facts_json = json.dumps(memory.facts, ensure_ascii=False)
    mem_tokens = memory.total_memory_tokens()

    if record is None:
        record = ChatMemoryRecord(
            session_id=memory.session_id,
            window_json=window_json,
            summary=memory.summary,
            facts_json=facts_json,
            mem_tokens=mem_tokens,
            turn_count=len(memory.window),
            since_compaction=0,
            version=1,
            updated_at=now,
        )
        session.add(record)
    else:
        record.window_json = window_json
        record.summary = memory.summary
        record.facts_json = facts_json
        record.mem_tokens = mem_tokens
        record.turn_count += 1
        record.since_compaction += 1
        record.version += 1
        record.updated_at = now

    await session.flush()
    return record


async def reset_session_memory(
    session: AsyncSession,
    session_id: str,
) -> bool:
    """Wipes summary, facts, and window turns for a specific session (R64)."""
    stmt = select(ChatMemoryRecord).where(ChatMemoryRecord.session_id == session_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record is not None:
        record.window_json = "[]"
        record.summary = ""
        record.facts_json = "[]"
        record.mem_tokens = 0
        record.since_compaction = 0
        record.version += 1
        record.updated_at = time.time()
        await session.flush()
        return True
    return False


async def delete_memory_fact(
    session: AsyncSession,
    session_id: str,
    fact_index: int,
) -> bool:
    """Deletes a single fact from durable memory by index (R64)."""
    stmt = select(ChatMemoryRecord).where(ChatMemoryRecord.session_id == session_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record is None:
        return False

    try:
        facts = json.loads(record.facts_json)
        if isinstance(facts, list) and 0 <= fact_index < len(facts):
            facts.pop(fact_index)
            record.facts_json = json.dumps(facts, ensure_ascii=False)
            record.version += 1
            record.updated_at = time.time()
            await session.flush()
            return True
    except Exception:
        pass
    return False


async def wipe_character_chat_data(
    session: AsyncSession,
    character_id: str,
) -> None:
    """Privacy wipe: deletes all chat sessions and memory for a character (R60, R66)."""
    # Cascade foreign key on chat_memory removes memory rows automatically
    stmt = delete(ChatSessionRecord).where(ChatSessionRecord.character_id == character_id)
    await session.execute(stmt)
    await session.flush()


async def wipe_user_chat_data(
    session: AsyncSession,
    user_id: int,
) -> None:
    """Privacy wipe: deletes all chat sessions and memory for a user (R50, R66)."""
    stmt = delete(ChatSessionRecord).where(ChatSessionRecord.user_id == user_id)
    await session.execute(stmt)
    await session.flush()
