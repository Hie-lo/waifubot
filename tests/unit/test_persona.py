"""Unit tests for character persona fields, card compilation, AI Enhance, and undo (R62)."""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from waifubot.core.persona import (
    PersonaFields,
    build_system_prefix,
    compile_persona_card,
)
from waifubot.core.safety import SafetyViolationError
from waifubot.db.base import Base
from waifubot.services.identity import create_character
from waifubot.services.personas import (
    ai_enhance_persona,
    get_or_create_persona,
    undo_persona_edit,
    update_persona_field,
)


class MockEnhancer:
    """Mock enhancer providing canonical or tidied persona responses."""

    def __init__(
        self,
        response_text: str = '{"temperament": "Tsundere", "speaking_style": "Sharp but caring"}',
    ) -> None:
        self.response_text = response_text

    async def enhance_prompt(
        self,
        scene: str,
        character_data: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> str:
        return self.response_text


class TestPersona(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def test_persona_fields_json_repair_and_normalization(self) -> None:
        """PersonaFields normalizes text with NFC and repairs malformed JSON."""
        # Malformed JSON with surrounding text
        raw_text = (
            'Here is the JSON: {"display_name": "آسـوکا", "relationship_to_user": "rival"} done'
        )
        fields = PersonaFields.from_json(raw_text)
        self.assertEqual(fields.display_name, "آسوکا")
        self.assertEqual(fields.relationship_to_user, "rival")
        self.assertEqual(fields.language, "fa")

        # Invalid relationship falls back to 'custom'
        fields_invalid = PersonaFields.from_dict({"relationship_to_user": "enemy_unknown"})
        self.assertEqual(fields_invalid.relationship_to_user, "custom")

    def test_persona_card_compilation_and_hash(self) -> None:
        """compile_persona_card creates compact representation and sha256 hash (R62)."""
        fields = PersonaFields(
            display_name="Makise Kurisu",
            temperament="Analytical and Tsundere",
            speaking_style="Formal with occasional sarcastic remarks",
            relationship_to_user="partner",
            likes="Dr Pepper, Lab experiments",
            dislikes="Being called Christina",
        )
        card, card_hash = compile_persona_card(fields, max_tokens=300)

        self.assertIn("Name: Makise Kurisu", card)
        self.assertIn("Temperament: Analytical and Tsundere", card)
        self.assertIn("Likes: Dr Pepper", card)
        self.assertEqual(len(card_hash), 64)  # Valid SHA256 string

        # Identical fields produce identical hash
        card2, card_hash2 = compile_persona_card(fields, max_tokens=300)
        self.assertEqual(card_hash, card_hash2)

        # System prefix assembly
        prefix = build_system_prefix(card)
        self.assertIn("Makise Kurisu", prefix)

    async def test_persona_service_update_and_one_step_undo(self) -> None:
        """Updating persona keeps previous state and allows one-step undo (R62)."""
        async with self.session_factory() as session:
            char = await create_character(
                session, "Rin Tohsaka", "Twin tails, red coat", created_by=1
            )
            char_id = char.character_id

            # Initial persona
            persona = await get_or_create_persona(session, char_id, default_name=char.name)
            self.assertEqual(persona.version, 1)

            # Update temperament
            updated = await update_persona_field(
                session=session,
                character_id=char_id,
                field_name="temperament",
                value="Tsundere prodigy mage",
            )
            self.assertEqual(updated.version, 2)
            self.assertIn("Tsundere prodigy mage", updated.card)
            self.assertIsNotNone(updated.prev_json)

            # Undo edit (R62)
            restored = await undo_persona_edit(session, char_id)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.version, 3)
            # Restored fields should not contain the edited temperament
            self.assertNotIn("Tsundere prodigy mage", restored.card)

    async def test_ai_enhance_persona_contract(self) -> None:
        """AI Enhance preserves existing fields and enriches persona (R20, R62)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Asuka", "Pilot", created_by=1)
            enhancer = MockEnhancer(
                response_text='{"display_name": "Asuka Langley", "temperament": "Fiery and proud", "relationship_to_user": "rival"}'
            )

            preview, new_fields = await ai_enhance_persona(
                session=session,
                enhancer=enhancer,
                character_id=char.character_id,
                char_name=char.name,
                known_anime="Evangelion",
            )
            self.assertEqual(preview["temperament"], "Fiery and proud")
            self.assertEqual(new_fields.relationship_to_user, "rival")
            self.assertEqual(new_fields.display_name, "Asuka Langley")

    async def test_persona_safety_violation_rejected(self) -> None:
        """Persona update rejects forbidden or unsafe content (R26, R66)."""
        async with self.session_factory() as session:
            char = await create_character(session, "Safe Char", "Appearance", created_by=1)
            with self.assertRaises(SafetyViolationError):
                await update_persona_field(
                    session=session,
                    character_id=char.character_id,
                    field_name="temperament",
                    value="underage loli character description",
                )


if __name__ == "__main__":
    unittest.main()
