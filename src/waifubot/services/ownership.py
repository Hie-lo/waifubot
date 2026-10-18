"""Character ownership, claiming, catalog, and lifecycle management service (R60, R61).

Contract:
- Ownership attaches to the stable character_id (R18).
- Editing appearance (a new version) never changes the owner (R60).
- States: draft (staff only) -> listed (claimable) -> claimed (one owner) or private (DM user, owned from start).
- Exactly one owner at a time.
- Atomic first-come-first-served claiming with max_owned_characters in the same transaction (R61).
- Loser of a claim race gets already_claimed (R61).
- Best-effort update of channel post button to 'claimed ✅' (R61).
- Superadmin ignores claim limits (R53).
- Admins can revoke or reassign ownership from user card, audited (R44, R50).
- Release: claimed returns to listed; private deleted after confirmation (R60).
"""

import random
import time
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.limits import Limits
from waifubot.db.models import CharacterOwnershipRecord, CharacterRecord, ClaimPostRecord
from waifubot.services.audit import record_audit
from waifubot.services.identity import create_character


async def get_character_ownership(
    session: AsyncSession,
    character_id: str,
) -> CharacterOwnershipRecord | None:
    """Retrieves ownership record for a character by stable character_id."""
    stmt = select(CharacterOwnershipRecord).where(
        CharacterOwnershipRecord.character_id == character_id
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def ensure_character_ownership(
    session: AsyncSession,
    character_id: str,
    status: str = "draft",
    owner_user_id: int | None = None,
    claimable: bool = False,
) -> CharacterOwnershipRecord:
    """Gets or creates the ownership record for a character."""
    record = await get_character_ownership(session, character_id)
    if record is None:
        now = time.time()
        record = CharacterOwnershipRecord(
            character_id=character_id,
            status=status,
            owner_user_id=owner_user_id,
            claimable=claimable,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        await session.flush()
    return record


async def set_character_status(
    session: AsyncSession,
    character_id: str,
    status: str,
    claimable: bool | None = None,
) -> CharacterOwnershipRecord:
    """Updates the status and claimable flag of a character."""
    record = await ensure_character_ownership(session, character_id)
    record.status = status
    if claimable is not None:
        record.claimable = claimable
    elif status == "listed":
        record.claimable = True
    elif status in ("claimed", "private", "draft"):
        record.claimable = False

    record.updated_at = time.time()
    await session.flush()
    return record


async def count_user_owned_characters(
    session: AsyncSession,
    user_id: int,
) -> int:
    """Returns number of active claimed and private characters owned by a user (R60, R61)."""
    stmt = select(func.count(CharacterOwnershipRecord.character_id)).where(
        and_(
            CharacterOwnershipRecord.owner_user_id == user_id,
            CharacterOwnershipRecord.status.in_(["claimed", "private"]),
        )
    )
    res = await session.execute(stmt)
    return res.scalar() or 0


async def claim_character(
    session: AsyncSession,
    character_id: str,
    user_id: int,
    user_limits: Limits | None = None,
    is_superadmin: bool = False,
    claim_enabled: bool = True,
) -> tuple[bool, str]:
    """Atomically claims a listed character under first-come-first-served semantics (R61).

    Returns:
        (success, reason) where reason is:
        - "claimed": Successfully claimed
        - "claim_disabled": Master setting is disabled
        - "limit_reached": User reached max_owned_characters limit
        - "already_claimed": Another user won the race or character not listed
        - "not_found": Character does not exist
    """
    if not claim_enabled:
        return False, "claim_disabled"

    # 1. Check max_owned_characters limit in the same transaction (R54, R61)
    if not is_superadmin and user_limits and user_limits.max_owned_characters is not None:
        owned_count = await count_user_owned_characters(session, user_id)
        if owned_count >= user_limits.max_owned_characters:
            return False, "limit_reached"

    # 2. Atomic first-come-first-served UPDATE ... WHERE status='listed' (R61)
    now = time.time()
    stmt = (
        update(CharacterOwnershipRecord)
        .where(
            and_(
                CharacterOwnershipRecord.character_id == character_id,
                CharacterOwnershipRecord.status == "listed",
            )
        )
        .values(
            status="claimed",
            owner_user_id=user_id,
            claimable=False,
            claimed_at=now,
            updated_at=now,
        )
    )
    res = await session.execute(stmt)
    if res.rowcount == 0:
        # Check if record exists at all
        existing = await get_character_ownership(session, character_id)
        if existing is None:
            return False, "not_found"
        return False, "already_claimed"

    await session.flush()
    return True, "claimed"


async def release_character(
    session: AsyncSession,
    character_id: str,
    user_id: int,
    is_staff: bool = False,
) -> tuple[bool, str]:
    """Releases an owned character (R60).

    - Claimed character returns to 'listed' (unclaimed in catalog).
    - Private character is deleted after confirmation.
    - Persona and chat memory will be wiped (R66).

    Returns:
        (success, action) where action is 'released_to_listed', 'deleted', or error code.
    """
    record = await get_character_ownership(session, character_id)
    if record is None:
        return False, "not_found"

    if record.owner_user_id != user_id and not is_staff:
        return False, "not_authorized"

    from waifubot.services.chat.session import wipe_character_chat_data
    from waifubot.services.personas import wipe_character_persona

    now = time.time()
    if record.status == "claimed":
        record.status = "listed"
        record.owner_user_id = None
        record.claimable = True
        record.released_at = now
        record.updated_at = now
        await wipe_character_chat_data(session, character_id)
        await wipe_character_persona(session, character_id)
        await session.flush()
        return True, "released_to_listed"

    if record.status == "private":
        # Delete ownership record and character versions (R60)
        await session.delete(record)
        char_stmt = delete(CharacterRecord).where(CharacterRecord.character_id == character_id)
        await session.execute(char_stmt)
        await wipe_character_chat_data(session, character_id)
        await wipe_character_persona(session, character_id)
        await session.flush()
        return True, "deleted"

    return False, "invalid_state"


async def create_character_in_dm(
    session: AsyncSession,
    user_id: int,
    name: str,
    appearance_facts: str,
    user_limits: Limits | None = None,
    is_superadmin: bool = False,
    mode: str = "known",
    traits_json: str | None = None,
    extras: str | None = None,
) -> tuple[CharacterRecord | None, str]:
    """Creates a user-owned character in private DM (R60).

    Status is 'private' and counts against user's max_owned_characters quota.
    """
    if not is_superadmin and user_limits and user_limits.max_owned_characters is not None:
        owned_count = await count_user_owned_characters(session, user_id)
        if owned_count >= user_limits.max_owned_characters:
            return None, "limit_reached"

    char = await create_character(
        session=session,
        name=name,
        appearance_facts=appearance_facts,
        created_by=user_id,
        mode=mode,
        traits_json=traits_json,
        extras=extras,
    )

    now = time.time()
    ownership = CharacterOwnershipRecord(
        character_id=char.character_id,
        status="private",
        owner_user_id=user_id,
        claimable=False,
        created_at=now,
        updated_at=now,
    )
    session.add(ownership)
    await session.flush()
    return char, "ok"


async def list_catalog_characters(
    session: AsyncSession,
    offset: int = 0,
    limit: int = 5,
) -> tuple[list[tuple[CharacterRecord, CharacterOwnershipRecord]], int]:
    """Returns unclaimed characters currently listed in catalog (R61)."""
    base_filter = and_(
        CharacterOwnershipRecord.status == "listed",
        CharacterRecord.character_id == CharacterOwnershipRecord.character_id,
        CharacterRecord.is_current.is_(True),
    )

    count_stmt = (
        select(func.count(CharacterOwnershipRecord.character_id))
        .join(
            CharacterRecord, CharacterRecord.character_id == CharacterOwnershipRecord.character_id
        )
        .where(
            and_(
                CharacterOwnershipRecord.status == "listed",
                CharacterRecord.is_current.is_(True),
            )
        )
    )
    count_res = await session.execute(count_stmt)
    total_count = count_res.scalar() or 0

    query = (
        select(CharacterRecord, CharacterOwnershipRecord)
        .join(
            CharacterRecord, CharacterRecord.character_id == CharacterOwnershipRecord.character_id
        )
        .where(base_filter)
        .order_by(CharacterRecord.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    res = await session.execute(query)
    items = list(res.all())
    return [(row[0], row[1]) for row in items], total_count


async def get_random_unclaimed_character(
    session: AsyncSession,
) -> tuple[CharacterRecord, CharacterOwnershipRecord] | None:
    """Picks a random unclaimed character from the catalog (R61)."""
    items, total = await list_catalog_characters(session, offset=0, limit=100)
    if not items:
        return None
    return random.choice(items)


async def list_user_characters(
    session: AsyncSession,
    user_id: int,
) -> list[tuple[CharacterRecord, CharacterOwnershipRecord]]:
    """Returns active characters owned by user (claimed + private) (R60)."""
    query = (
        select(CharacterRecord, CharacterOwnershipRecord)
        .join(
            CharacterRecord, CharacterRecord.character_id == CharacterOwnershipRecord.character_id
        )
        .where(
            and_(
                CharacterOwnershipRecord.owner_user_id == user_id,
                CharacterOwnershipRecord.status.in_(["claimed", "private"]),
                CharacterRecord.is_current.is_(True),
            )
        )
        .order_by(CharacterRecord.updated_at.desc())
    )
    res = await session.execute(query)
    items = list(res.all())
    return [(row[0], row[1]) for row in items]


async def record_claim_post(
    session: AsyncSession,
    character_id: str,
    chat_id: int,
    message_id: int,
) -> ClaimPostRecord:
    """Stores channel post reference for best-effort edit on claim (R61)."""
    post = ClaimPostRecord(
        character_id=character_id,
        chat_id=chat_id,
        message_id=message_id,
        created_at=time.time(),
    )
    session.add(post)
    await session.flush()
    return post


async def update_claim_posts_best_effort(
    session: AsyncSession,
    publisher: Any,
    character_id: str,
    claimed_label: str = "دریافت شده ✅",
) -> int:
    """Attempts to edit the inline button on channel posts to 'Claimed ✅' (R61).

    Telegram failures are safely caught and ignored.
    """
    stmt = select(ClaimPostRecord).where(ClaimPostRecord.character_id == character_id)
    res = await session.execute(stmt)
    posts = list(res.scalars().all())

    edited_count = 0
    for p in posts:
        try:
            if hasattr(publisher, "edit_message_reply_markup"):
                markup = [[{"text": claimed_label, "callback_data": "noop"}]]
                await publisher.edit_message_reply_markup(
                    chat_id=p.chat_id,
                    message_id=p.message_id,
                    reply_markup=markup,
                )
                edited_count += 1
        except Exception:
            # R61: failures ignored best-effort
            pass

    return edited_count


async def revoke_ownership(
    session: AsyncSession,
    character_id: str,
    admin_id: int,
    new_status: str = "listed",
) -> bool:
    """Admin action: Revokes character ownership and returns to catalog or draft (R44, R50, R61)."""
    record = await get_character_ownership(session, character_id)
    if record is None:
        return False

    old_owner = record.owner_user_id
    now = time.time()
    record.owner_user_id = None
    record.status = new_status
    record.claimable = new_status == "listed"
    record.released_at = now
    record.updated_at = now

    await record_audit(
        session=session,
        admin_id=admin_id,
        action="revoke_character_ownership",
        target=character_id,
        old_val={"owner_user_id": old_owner},
        new_val={"status": new_status, "owner_user_id": None},
    )
    await session.flush()
    return True


async def reassign_ownership(
    session: AsyncSession,
    character_id: str,
    new_owner_user_id: int,
    admin_id: int,
) -> bool:
    """Admin action: Reassigns character ownership to another user (R44, R50, R61)."""
    record = await ensure_character_ownership(session, character_id)
    old_owner = record.owner_user_id
    now = time.time()
    record.owner_user_id = new_owner_user_id
    record.status = "claimed"
    record.claimable = False
    record.claimed_at = now
    record.updated_at = now

    await record_audit(
        session=session,
        admin_id=admin_id,
        action="reassign_character_ownership",
        target=character_id,
        old_val={"owner_user_id": old_owner},
        new_val={"owner_user_id": new_owner_user_id},
    )
    await session.flush()
    return True
