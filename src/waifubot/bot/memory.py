"""24-hour dialog memory per admin (SPEC §11 wizard; must survive restarts).

State lives in the `settings` table under `dialog:<admin_id>` keys — durable,
and `updated_at` gives the 24 h expiry for free. Values are JSON blobs that
belong to dialogs, not typed admin settings (which go through AdminSettings).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waifubot.db.models import Setting

DIALOG_TTL = timedelta(hours=24)


class DialogMemory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _key(admin_id: int) -> str:
        return f"dialog:{admin_id}"

    async def get(self, admin_id: int) -> dict[str, Any] | None:
        async with self._sessions() as s:
            row = await s.get(Setting, self._key(admin_id))
            if row is None:
                return None
            try:
                data = json.loads(row.value_json)
            except (ValueError, TypeError):
                return None
            if not isinstance(data, dict):
                return None
            updated = row.updated_at
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if updated is not None and datetime.now(UTC) - updated > DIALOG_TTL:
                await self.clear(admin_id)
                return None
            return data

    async def set(self, admin_id: int, data: dict[str, Any]) -> None:
        async with self._sessions() as s:
            row = await s.get(Setting, self._key(admin_id))
            payload = json.dumps(data, ensure_ascii=False)
            if row is None:
                s.add(Setting(key=self._key(admin_id), value_json=payload,
                              updated_by=str(admin_id)))
            else:
                row.value_json = payload
                row.updated_by = str(admin_id)
                row.updated_at = datetime.now(UTC)
            await s.commit()

    async def clear(self, admin_id: int) -> None:
        async with self._sessions() as s:
            row = await s.get(Setting, self._key(admin_id))
            if row is not None:
                await s.delete(row)
                await s.commit()


class PanelMemory:
    """The ONE menu panel message per admin (user rule 2026-10-17: every menu
    section works inside a single message — navigation edits it in place).
    Stored in the `settings` table like DialogMemory; a forgotten panel is
    harmless (the next send creates a fresh one)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _key(admin_id: int) -> str:
        return f"panel:{admin_id}"

    async def get(self, admin_id: int) -> tuple[int, int] | None:
        """(chat_id, message_id) of the live panel, if any."""
        async with self._sessions() as s:
            row = await s.get(Setting, self._key(admin_id))
            await s.commit()
            if row is None:
                return None
            try:
                data = json.loads(row.value_json)
                chat_id, message_id = int(data[0]), int(data[1])
            except (ValueError, TypeError, KeyError, IndexError):
                return None
            return chat_id, message_id

    async def set(self, admin_id: int, chat_id: int, message_id: int) -> None:
        async with self._sessions() as s:
            row = await s.get(Setting, self._key(admin_id))
            payload = json.dumps([chat_id, message_id])
            if row is None:
                s.add(Setting(key=self._key(admin_id), value_json=payload,
                              updated_by=str(admin_id)))
            else:
                row.value_json = payload
                row.updated_at = datetime.now(UTC)
            await s.commit()

    async def clear(self, admin_id: int) -> None:
        async with self._sessions() as s:
            row = await s.get(Setting, self._key(admin_id))
            if row is not None:
                await s.delete(row)
                await s.commit()
