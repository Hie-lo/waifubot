"""Channel registry, verification, health, and access control service (R56–R59).

Contract:
- R56: Channels registered with 3-step verification:
  1. Chat existence check
  2. Bot rights check (post/media permissions)
  3. Adder permission check (R57)
- R57: Adder must be creator or administrator with post rights. Superadmin is exempt.
- R58: Health checks and re-verify. When bot loses rights: mark 'lost_access',
  pause active schedules (never delete them), and notify owner/admins.
- R59: Channel scope (private vs shared). Owners can publish to private channels;
  staff can publish to shared channels; superadmins can publish to all.
- Active schedule guard: cannot delete channel with active schedules.
"""

import json
import time
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.limits import Limits
from waifubot.core.ports import ChannelProbe
from waifubot.db.models import ChannelRecord, ChannelTopicRecord, ScheduleRecord
from waifubot.services.audit import record_audit
from waifubot.services.quota import check_and_reserve


class ChannelVerificationError(Exception):
    """Raised when channel verification fails (R56, R57)."""

    def __init__(self, reason: str, extra: Any = None) -> None:
        self.reason = reason
        self.extra = extra
        super().__init__(f"Channel verification failed: {reason} ({extra})")


class ChannelUsabilityError(Exception):
    """Raised when asserting channel usability fails (R58, R59)."""

    def __init__(self, reason: str, channel_id: int) -> None:
        self.reason = reason
        self.channel_id = channel_id
        super().__init__(f"Channel {channel_id} cannot be used: {reason}")


async def get_channel(session: AsyncSession, channel_id: int) -> ChannelRecord | None:
    """Retrieves channel record by internal ID."""
    stmt = select(ChannelRecord).where(ChannelRecord.id == channel_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def get_channel_by_tg_id(session: AsyncSession, tg_chat_id: int) -> ChannelRecord | None:
    """Retrieves channel record by Telegram chat ID."""
    stmt = select(ChannelRecord).where(ChannelRecord.tg_chat_id == tg_chat_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def list_channels(
    session: AsyncSession,
    owner_user_id: int | None = None,
    scope: str | None = None,
    status: str | None = None,
) -> list[ChannelRecord]:
    """Lists registered channels matching optional filters."""
    stmt = select(ChannelRecord)
    if owner_user_id is not None:
        stmt = stmt.where(ChannelRecord.owner_user_id == owner_user_id)
    if scope is not None:
        stmt = stmt.where(ChannelRecord.scope == scope)
    if status is not None:
        stmt = stmt.where(ChannelRecord.status == status)

    stmt = stmt.order_by(ChannelRecord.created_at.desc())
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def verify_channel(
    session: AsyncSession,
    probe: ChannelProbe,
    chat_ref: str | int,
    adder_user_id: int,
    adder_role: str,
    limits: Limits | None = None,
) -> ChannelRecord:
    """Performs the strict 3-step verification on channel addition (R56, R57).

    Steps:
    1. Chat exists and bot can see it.
    2. Bot has required admin rights (post messages for channels; media for groups).
    3. Adder passes R57 permission check (creator/admin, superadmin exempt, user within quota).
    """
    # Step 1: Chat exists
    try:
        chat_info = await probe.get_chat_info(chat_ref)
    except Exception as e:
        raise ChannelVerificationError("chat_not_found", str(e)) from e

    if not chat_info or "id" not in chat_info:
        raise ChannelVerificationError("chat_not_found", "empty_chat_info")

    tg_chat_id = int(chat_info["id"])
    kind = str(chat_info.get("type", "channel"))
    title = str(chat_info.get("title", f"Chat_{tg_chat_id}"))
    username = chat_info.get("username")

    # Step 2: Bot rights
    try:
        bot_rights = await probe.get_bot_rights(tg_chat_id)
    except Exception as e:
        raise ChannelVerificationError("bot_insufficient_rights", str(e)) from e

    if kind == "channel":
        if not bot_rights.get("can_post_messages", False):
            raise ChannelVerificationError("bot_insufficient_rights", "can_post_messages")
    else:  # group, supergroup
        if not (
            bot_rights.get("can_send_messages", False) or bot_rights.get("can_send_media", False)
        ):
            raise ChannelVerificationError("bot_insufficient_rights", "can_send_media")

    # Step 3: Adder permission check (R57)
    adder_role_in_chat = "creator"
    if adder_role == "superadmin":
        # Superadmin is exempt from ownership check (R53, R57)
        adder_role_in_chat = "creator"
    else:
        # Admins and Users must be creator or administrator with post right
        try:
            member_role = await probe.get_chat_member_role(tg_chat_id, adder_user_id)
        except Exception as e:
            raise ChannelVerificationError("channel_not_yours", str(e)) from e

        if member_role not in ("creator", "administrator"):
            raise ChannelVerificationError("channel_not_yours", f"role_{member_role}")
        adder_role_in_chat = member_role

        # If user role is "user", verify plan quota capacity (R52, R54)
        if adder_role == "user" and limits is not None:
            await check_and_reserve(
                session=session,
                user_id=adder_user_id,
                role="user",
                limits=limits,
                kind="channel",
                amount=1,
            )

    now = time.time()
    existing = await get_channel_by_tg_id(session, tg_chat_id)
    if existing is not None:
        existing.title = title
        existing.username = username
        existing.kind = kind
        existing.bot_rights_json = json.dumps(bot_rights)
        existing.verified_at = now
        existing.status = "active"
        existing.adder_role_in_chat = adder_role_in_chat
        existing.updated_at = now
        await session.flush()

        await record_audit(
            session=session,
            admin_id=adder_user_id,
            action="reverify_channel",
            target=f"channel:{existing.id}",
            new_val=json.dumps({"title": title, "status": "active"}),
        )
        return existing

    # Create new channel record
    scope = "shared" if adder_role == "superadmin" else "private"
    new_channel = ChannelRecord(
        tg_chat_id=tg_chat_id,
        kind=kind,
        title=title,
        username=username,
        owner_user_id=adder_user_id,
        scope=scope,
        status="active",
        bot_rights_json=json.dumps(bot_rights),
        verified_at=now,
        added_by=adder_user_id,
        adder_role_in_chat=adder_role_in_chat,
        created_at=now,
        updated_at=now,
    )
    session.add(new_channel)
    await session.flush()

    await record_audit(
        session=session,
        admin_id=adder_user_id,
        action="add_channel",
        target=f"channel:{new_channel.id}",
        new_val=json.dumps({"tg_chat_id": tg_chat_id, "title": title, "scope": scope}),
    )
    return new_channel


async def assert_usable(
    session: AsyncSession,
    channel_id: int,
    requester_user_id: int,
    requester_role: str,
) -> ChannelRecord:
    """Verifies that channel exists, is active, and accessible to requester (R58, R59)."""
    channel = await get_channel(session, channel_id)
    if channel is None:
        raise ChannelUsabilityError("not_found", channel_id)

    if channel.status == "legacy_unverified":
        raise ChannelUsabilityError("legacy_unverified", channel_id)

    if channel.status == "disabled":
        raise ChannelUsabilityError("disabled", channel_id)

    if channel.status == "lost_access":
        raise ChannelUsabilityError("lost_access", channel_id)

    # Scope permissions (R59)
    if requester_role == "superadmin":
        return channel

    if channel.owner_user_id == requester_user_id:
        return channel

    if channel.scope == "shared" and requester_role in ("admin", "superadmin"):
        return channel

    raise ChannelUsabilityError("permission_denied", channel_id)


async def set_channel_scope(
    session: AsyncSession,
    channel_id: int,
    scope: str,
    requester_user_id: int,
    requester_role: str,
) -> ChannelRecord:
    """Sets channel scope (private vs shared). Only owner or superadmin can modify (R59)."""
    channel = await get_channel(session, channel_id)
    if channel is None:
        raise ValueError("Channel not found")

    if requester_role != "superadmin" and channel.owner_user_id != requester_user_id:
        raise PermissionError("Only channel owner or superadmin can change channel scope")

    clean_scope = scope.strip().lower()
    if clean_scope not in ("private", "shared"):
        raise ValueError(f"Invalid scope: {scope}")

    old_val = channel.scope
    channel.scope = clean_scope
    channel.updated_at = time.time()
    await session.flush()

    await record_audit(
        session=session,
        admin_id=requester_user_id,
        action="set_channel_scope",
        target=f"channel:{channel_id}",
        old_val=old_val,
        new_val=clean_scope,
    )
    return channel


async def set_channel_status(
    session: AsyncSession,
    channel_id: int,
    status: str,
    requester_user_id: int,
    requester_role: str,
) -> ChannelRecord:
    """Enables or disables channel. Owner, admin, or superadmin can toggle (R58)."""
    channel = await get_channel(session, channel_id)
    if channel is None:
        raise ValueError("Channel not found")

    if requester_role not in ("admin", "superadmin") and channel.owner_user_id != requester_user_id:
        raise PermissionError("Not authorized to change channel status")

    clean_status = status.strip().lower()
    if clean_status not in ("active", "disabled"):
        raise ValueError(f"Invalid status: {status}")

    old_val = channel.status
    channel.status = clean_status
    channel.updated_at = time.time()
    await session.flush()

    await record_audit(
        session=session,
        admin_id=requester_user_id,
        action="set_channel_status",
        target=f"channel:{channel_id}",
        old_val=old_val,
        new_val=clean_status,
    )
    return channel


async def reverify_channel(
    session: AsyncSession,
    probe: ChannelProbe,
    channel_id: int,
    requester_user_id: int,
    requester_role: str,
) -> tuple[bool, str]:
    """Re-verifies channel health and bot permissions (R58).

    If rights lost -> status='lost_access', schedules paused (not deleted).
    If rights restored -> status='active'.
    """
    channel = await get_channel(session, channel_id)
    if channel is None:
        raise ValueError("Channel not found")

    try:
        bot_rights = await probe.get_bot_rights(channel.tg_chat_id)
        has_rights = (
            bot_rights.get("can_post_messages", False)
            if channel.kind == "channel"
            else (
                bot_rights.get("can_send_messages", False)
                or bot_rights.get("can_send_media", False)
            )
        )
    except Exception:
        has_rights = False

    now = time.time()
    if has_rights:
        channel.status = "active"
        channel.verified_at = now
        channel.updated_at = now
        await session.flush()
        return True, "active"
    else:
        channel.status = "lost_access"
        channel.updated_at = now
        # Pause any active schedules using this channel (R58)
        pause_stmt = (
            update(ScheduleRecord)
            .where(ScheduleRecord.channel_id == channel_id)
            .values(is_running=False, updated_at=now)
        )
        await session.execute(pause_stmt)
        await session.flush()
        return False, "lost_access"


async def handle_bot_membership_update(
    session: AsyncSession,
    tg_chat_id: int,
    new_status: str,
    new_rights: dict[str, bool],
) -> ChannelRecord | None:
    """Handles Telegram my_chat_member updates to immediately track lost access (R58)."""
    channel = await get_channel_by_tg_id(session, tg_chat_id)
    if channel is None:
        return None

    now = time.time()
    has_rights = (
        new_rights.get("can_post_messages", False)
        if channel.kind == "channel"
        else (new_rights.get("can_send_messages", False) or new_rights.get("can_send_media", False))
    )

    if new_status in ("left", "kicked", "restricted") or not has_rights:
        channel.status = "lost_access"
        channel.updated_at = now
        # Pause schedules without deleting (R58)
        pause_stmt = (
            update(ScheduleRecord)
            .where(ScheduleRecord.channel_id == channel.id)
            .values(is_running=False, updated_at=now)
        )
        await session.execute(pause_stmt)
    else:
        channel.bot_rights_json = json.dumps(new_rights)
        channel.updated_at = now

    await session.flush()
    return channel


async def delete_channel(
    session: AsyncSession,
    channel_id: int,
    requester_user_id: int,
    requester_role: str,
    force: bool = False,
) -> None:
    """Deletes channel record. Blocked if active schedules exist on it (R58)."""
    channel = await get_channel(session, channel_id)
    if channel is None:
        raise ValueError("Channel not found")

    if requester_role != "superadmin" and channel.owner_user_id != requester_user_id:
        raise PermissionError("Only channel owner or superadmin can delete this channel")

    # Check active schedule guard (R58)
    active_sched_stmt = select(func.count(ScheduleRecord.id)).where(
        ScheduleRecord.channel_id == channel_id,
        ScheduleRecord.is_running.is_(True),
    )
    active_count = int((await session.execute(active_sched_stmt)).scalar_one() or 0)
    if active_count > 0 and not force:
        raise ValueError(
            f"Cannot remove channel: {active_count} active schedules are using it. Pause or reassign schedules first (R58)."
        )

    # Delete topics and channel
    await session.execute(
        delete(ChannelTopicRecord).where(ChannelTopicRecord.channel_id == channel_id)
    )
    await session.execute(delete(ChannelRecord).where(ChannelRecord.id == channel_id))
    await session.flush()

    await record_audit(
        session=session,
        admin_id=requester_user_id,
        action="delete_channel",
        target=f"channel:{channel_id}",
        old_val=json.dumps({"tg_chat_id": channel.tg_chat_id, "title": channel.title}),
    )


async def sync_channel_topics(
    session: AsyncSession,
    channel_id: int,
    topics: list[dict[str, Any]],
) -> None:
    """Syncs forum group topics for topic picker (R23, R56)."""
    # Delete old topics
    await session.execute(
        delete(ChannelTopicRecord).where(ChannelTopicRecord.channel_id == channel_id)
    )
    for t in topics:
        topic_id = int(t["id"])
        title = str(t.get("title", f"Topic_{topic_id}"))
        session.add(ChannelTopicRecord(channel_id=channel_id, topic_id=topic_id, title=title))
    await session.flush()


async def get_channel_topics(session: AsyncSession, channel_id: int) -> list[ChannelTopicRecord]:
    """Retrieves forum group topics for a channel."""
    stmt = select(ChannelTopicRecord).where(ChannelTopicRecord.channel_id == channel_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())
