"""Character identity and versioning service (R14, R18).

Contract:
- Editing a saved character creates a new version of the same character (R18).
- Stable character_id across versions.
- Sequential integer version (1, 2, 3...).
- Only one current version (is_current=True); previous versions remain accessible and linked via edit_of.
- Character facts contain appearance details ONLY (R14).
"""

import time
import uuid

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.db.models import CharacterRecord


def format_version_label(version: int, is_current: bool = False) -> str:
    """Formats character version for Persian UI (R18)."""
    current_badge = " (جاری)" if is_current else ""
    return f"نسخه {version}{current_badge}"


async def create_character(
    session: AsyncSession,
    name: str,
    appearance_facts: str,
    created_by: int,
    mode: str = "known",
    traits_json: str | None = None,
    extras: str | None = None,
    character_id: str | None = None,
) -> CharacterRecord:
    """Creates initial version 1 of a new character."""
    stable_id = character_id or f"char_{uuid.uuid4().hex[:12]}"
    now = time.time()

    record = CharacterRecord(
        character_id=stable_id,
        version=1,
        is_current=True,
        edit_of=None,
        name=name.strip(),
        appearance_facts=appearance_facts.strip(),
        traits_json=traits_json,
        extras=extras,
        mode=mode,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )

    session.add(record)
    await session.flush()
    return record


async def create_new_version(
    session: AsyncSession,
    parent_id: int,
    new_appearance_facts: str,
    new_name: str | None = None,
    traits_json: str | None = None,
    extras: str | None = None,
    created_by: int | None = None,
) -> CharacterRecord:
    """Creates a new version of an existing character, retiring the current version (R18)."""
    # Fetch parent version
    res = await session.execute(
        select(CharacterRecord).where(CharacterRecord.id == parent_id)
    )
    parent = res.scalar_one_or_none()
    if parent is None:
        raise ValueError(f"Parent character record {parent_id} not found")

    now = time.time()

    # Unmark all versions of this character as current
    await session.execute(
        update(CharacterRecord)
        .where(CharacterRecord.character_id == parent.character_id)
        .values(is_current=False, updated_at=now)
    )

    # Insert new version record
    next_ver = parent.version + 1
    new_record = CharacterRecord(
        character_id=parent.character_id,
        version=next_ver,
        is_current=True,
        edit_of=parent.id,
        name=new_name.strip() if new_name else parent.name,
        appearance_facts=new_appearance_facts.strip(),
        traits_json=traits_json if traits_json is not None else parent.traits_json,
        extras=extras if extras is not None else parent.extras,
        mode=parent.mode,
        created_by=created_by or parent.created_by,
        created_at=now,
        updated_at=now,
    )

    session.add(new_record)
    await session.flush()
    return new_record


async def get_current_character(
    session: AsyncSession,
    character_id: str,
) -> CharacterRecord | None:
    """Returns the current active version of a character."""
    res = await session.execute(
        select(CharacterRecord).where(
            and_(
                CharacterRecord.character_id == character_id,
                CharacterRecord.is_current.is_(True),
            )
        )
    )
    return res.scalar_one_or_none()


async def list_character_versions(
    session: AsyncSession,
    character_id: str,
) -> list[CharacterRecord]:
    """Returns all historical versions of a character ordered by version descending."""
    res = await session.execute(
        select(CharacterRecord)
        .where(CharacterRecord.character_id == character_id)
        .order_by(CharacterRecord.version.desc())
    )
    return list(res.scalars().all())
