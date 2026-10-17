"""Audit logging service for tracking settings modifications and admin management (R44).

Contract:
- Every settings change and admin management action is audited (who, what, old -> new, when).
- Never records chat message text, persona text or user-private content (R44, R66).
- Accessible in the superadmin audit view.
"""

import json
import time
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import AuditLogRecord


def _serialize_val(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return str(val)
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return str(val)


async def record_audit(
    session: AsyncSession,
    admin_id: int,
    action: str,
    target: str,
    old_val: Any = None,
    new_val: Any = None,
) -> AuditLogRecord:
    """Records an audit log entry in the audit_log table."""
    record = AuditLogRecord(
        at=time.time(),
        admin_id=admin_id,
        action=action.strip(),
        target=target.strip(),
        old_val=_serialize_val(old_val),
        new_val=_serialize_val(new_val),
    )
    session.add(record)
    await session.flush()
    return record


async def get_audit_logs(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditLogRecord]:
    """Retrieves recent audit log records ordered from newest to oldest."""
    stmt = (
        select(AuditLogRecord)
        .order_by(desc(AuditLogRecord.at), desc(AuditLogRecord.id))
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_audit_logs_for_target(
    session: AsyncSession,
    target: str,
    limit: int = 50,
) -> list[AuditLogRecord]:
    """Retrieves recent audit logs for a specific target."""
    stmt = (
        select(AuditLogRecord)
        .where(AuditLogRecord.target == target.strip())
        .order_by(desc(AuditLogRecord.at), desc(AuditLogRecord.id))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def format_audit_entry(record: AuditLogRecord, locale: str = "fa") -> str:
    """Formats an audit log entry for the bot display."""
    if locale == "fa":
        old_str = f"قبلی: {record.old_val}" if record.old_val is not None else ""
        new_str = f"جدید: {record.new_val}" if record.new_val is not None else ""
        vals = " | ".join(filter(None, [old_str, new_str]))
        val_suffix = f"\n  ↳ {vals}" if vals else ""
        return f"• [{record.action}] توسط {record.admin_id} روی {record.target}{val_suffix}"
    else:
        old_str = f"old: {record.old_val}" if record.old_val is not None else ""
        new_str = f"new: {record.new_val}" if record.new_val is not None else ""
        vals = " | ".join(filter(None, [old_str, new_str]))
        val_suffix = f"\n  ↳ {vals}" if vals else ""
        return f"• [{record.action}] by {record.admin_id} on {record.target}{val_suffix}"
