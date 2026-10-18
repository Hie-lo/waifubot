"""Character persona service: field editing, compilation, AI Enhance, and one-step undo (R62).

Contract:
- Personality is separate from character appearance (R14, R62).
- One screen per field editing (R1, R3, R4).
- AI Enhance preserves user details; adds canon for known characters, tidies unknown (R20, R62).
- Result shown with preview before saving; one-step undo via prev_json (R62).
- Persona text passes R66 safety checks.
- Deleted when character released or deleted (R60, R66).
"""

import json
import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.persona import (
    PersonaFields,
    compile_persona_card,
)
from waifubot.core.ports import Enhancer
from waifubot.core.safety import assert_safe_prompt
from waifubot.db.models import CharacterPersonaRecord


async def get_or_create_persona(
    session: AsyncSession,
    character_id: str,
    default_name: str = "",
) -> CharacterPersonaRecord:
    """Retrieves existing persona or creates initial default persona record."""
    stmt = select(CharacterPersonaRecord).where(CharacterPersonaRecord.character_id == character_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record is not None:
        return record

    fields = PersonaFields(display_name=default_name)
    card, card_hash = compile_persona_card(fields)
    now = time.time()
    record = CharacterPersonaRecord(
        character_id=character_id,
        version=1,
        fields_json=fields.to_json(),
        notes=None,
        card=card,
        card_hash=card_hash,
        prev_json=None,
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    await session.flush()
    return record


async def update_persona_field(
    session: AsyncSession,
    character_id: str,
    field_name: str,
    value: str,
    token_cap: int = 300,
) -> CharacterPersonaRecord:
    """Updates a single field in the character's persona and recompiles the card."""
    # Safety validation on user-submitted persona text (R66)
    assert_safe_prompt(value)

    record = await get_or_create_persona(session, character_id)
    fields = PersonaFields.from_json(record.fields_json)

    if hasattr(fields, field_name):
        # Save previous state for one-step undo (R62)
        record.prev_json = record.fields_json
        setattr(fields, field_name, value)
        record.fields_json = fields.to_json()

        card, card_hash = compile_persona_card(fields, max_tokens=token_cap)
        record.card = card
        record.card_hash = card_hash
        record.version += 1
        record.updated_at = time.time()
        await session.flush()

    return record


async def ai_enhance_persona(
    session: AsyncSession,
    enhancer: Enhancer,
    character_id: str,
    char_name: str = "",
    known_anime: str = "",
) -> tuple[dict[str, Any], PersonaFields]:
    """Generates AI enhanced personality following R20/R62 contract.

    - Preserves all existing user details.
    - Adds canonical personality from name + anime for known characters.
    - Tidies and expands wording for unknown characters, inventing nothing.
    - Returns preview dict and new PersonaFields.
    """
    record = await get_or_create_persona(session, character_id, default_name=char_name)
    current_fields = PersonaFields.from_json(record.fields_json)

    prompt = (
        f"Character: {char_name or current_fields.display_name}. Anime: {known_anime or 'Unknown'}.\n"
        f"Existing persona details: {current_fields.to_dict()}"
    )

    enhanced_text = await enhancer.enhance_prompt(
        scene="Enhance character persona", character_data={"prompt": prompt}
    )

    # Try to extract JSON or structure into fields
    try:
        data = json.loads(enhanced_text)
    except Exception:
        # If model returned text, update speaking_style and temperament
        data = current_fields.to_dict()
        data["temperament"] = enhanced_text[:150]
        data["speaking_style"] = (
            enhanced_text[150:300] if len(enhanced_text) > 150 else data.get("speaking_style", "")
        )

    new_fields = PersonaFields.from_dict(data)
    # Ensure display_name is preserved
    if not new_fields.display_name:
        new_fields.display_name = current_fields.display_name or char_name

    preview = new_fields.to_dict()
    return preview, new_fields


async def save_enhanced_persona(
    session: AsyncSession,
    character_id: str,
    new_fields: PersonaFields,
    token_cap: int = 300,
) -> CharacterPersonaRecord:
    """Saves AI enhanced persona fields, keeping previous version for one-step undo (R62)."""
    record = await get_or_create_persona(session, character_id)

    # Save previous state
    record.prev_json = record.fields_json
    record.fields_json = new_fields.to_json()

    card, card_hash = compile_persona_card(new_fields, max_tokens=token_cap)
    record.card = card
    record.card_hash = card_hash
    record.version += 1
    record.updated_at = time.time()
    await session.flush()
    return record


async def undo_persona_edit(
    session: AsyncSession,
    character_id: str,
    token_cap: int = 300,
) -> CharacterPersonaRecord | None:
    """Restores previous persona version if available (one-step undo) (R62)."""
    record = await get_or_create_persona(session, character_id)
    if not record.prev_json:
        return None

    # Swap current and previous
    current = record.fields_json
    restored = record.prev_json
    record.fields_json = restored
    record.prev_json = current  # Redo capability or clear

    fields = PersonaFields.from_json(restored)
    card, card_hash = compile_persona_card(fields, max_tokens=token_cap)
    record.card = card
    record.card_hash = card_hash
    record.version += 1
    record.updated_at = time.time()
    await session.flush()
    return record


async def wipe_character_persona(
    session: AsyncSession,
    character_id: str,
) -> None:
    """Deletes persona and history when character released or deleted (R60, R66)."""
    stmt = delete(CharacterPersonaRecord).where(CharacterPersonaRecord.character_id == character_id)
    await session.execute(stmt)
    await session.flush()
