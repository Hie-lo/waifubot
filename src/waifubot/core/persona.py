"""Character persona pure-logic models, compilers, and validation (R62).

Contract:
- Personality is separate from character appearance facts (R14, R62).
- Fields: display_name, speaking_style, temperament, backstory, likes, dislikes,
  relationship_to_user, quirks, avoid_topics, language, opening_message, notes.
- Persona card compile: produces compact string representation (<= persona_card_tokens = 300).
- Hash: SHA256 of the compiled persona card.
- JSON contract + repair: robust parsing and normalization with defaults.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from waifubot.core.chat_memory import estimate_tokens, normalize_text

RELATIONSHIP_CHOICES = ("friend", "partner", "rival", "mentor", "custom")


@dataclass
class PersonaFields:
    """Pure data container for character personality and roleplay configuration."""

    display_name: str = ""
    speaking_style: str = ""
    temperament: str = ""
    backstory: str = ""
    likes: str = ""
    dislikes: str = ""
    relationship_to_user: str = "friend"
    quirks: str = ""
    avoid_topics: str = ""
    language: str = "fa"
    opening_message: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        """Returns clean dictionary with NFC-normalized string values."""
        return {k: normalize_text(str(v)) for k, v in asdict(self).items()}

    def to_json(self) -> str:
        """Serializes normalized fields to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonaFields":
        """Builds PersonaFields from dictionary, applying defaults and repair."""
        rel = str(data.get("relationship_to_user", "friend")).strip().lower()
        if rel not in RELATIONSHIP_CHOICES:
            rel = "custom"

        return cls(
            display_name=normalize_text(str(data.get("display_name", ""))),
            speaking_style=normalize_text(str(data.get("speaking_style", ""))),
            temperament=normalize_text(str(data.get("temperament", ""))),
            backstory=normalize_text(str(data.get("backstory", ""))),
            likes=normalize_text(str(data.get("likes", ""))),
            dislikes=normalize_text(str(data.get("dislikes", ""))),
            relationship_to_user=rel,
            quirks=normalize_text(str(data.get("quirks", ""))),
            avoid_topics=normalize_text(str(data.get("avoid_topics", ""))),
            language=normalize_text(str(data.get("language", "fa"))),
            opening_message=normalize_text(str(data.get("opening_message", ""))),
            notes=normalize_text(str(data.get("notes", ""))),
        )

    @classmethod
    def from_json(cls, raw_json: str) -> "PersonaFields":
        """Parses JSON string with regex fallback repair."""
        raw = raw_json.strip() if raw_json else "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw)
            parsed = json.loads(match.group(0)) if match else {}

        if not isinstance(parsed, dict):
            parsed = {}
        return cls.from_dict(parsed)


def compile_persona_card(fields: PersonaFields, max_tokens: int = 300) -> tuple[str, str]:
    """Compiles persona fields into a compact persona card and its sha256 hash (R62).

    Returns:
        (compiled_card_text, sha256_hash)
    """
    lines = []
    if fields.display_name:
        lines.append(f"Name: {fields.display_name}")
    if fields.speaking_style:
        lines.append(f"Speaking Style: {fields.speaking_style}")
    if fields.temperament:
        lines.append(f"Temperament: {fields.temperament}")
    if fields.relationship_to_user:
        lines.append(f"Relationship: {fields.relationship_to_user}")
    if fields.backstory:
        lines.append(f"Backstory: {fields.backstory}")
    if fields.likes or fields.dislikes:
        likes_str = f"Likes: {fields.likes}" if fields.likes else ""
        dislikes_str = f"Dislikes: {fields.dislikes}" if fields.dislikes else ""
        lines.append(" | ".join(filter(None, [likes_str, dislikes_str])))
    if fields.quirks:
        lines.append(f"Quirks: {fields.quirks}")
    if fields.avoid_topics:
        lines.append(f"Avoid: {fields.avoid_topics}")
    if fields.language:
        lines.append(f"Language: {fields.language}")
    if fields.notes:
        lines.append(f"Notes: {fields.notes}")

    compiled = "\n".join(lines).strip()

    # Enforce token cap if exceeded
    if estimate_tokens(compiled) > max_tokens:
        words = compiled.split()
        while words and estimate_tokens(" ".join(words)) > max_tokens:
            words.pop()
        compiled = " ".join(words)

    card_hash = hashlib.sha256(compiled.encode("utf-8")).hexdigest()
    return compiled, card_hash


def build_system_prefix(
    persona_card: str,
    safety_guidelines: str = "",
    min_cache_tokens: int = 1024,
) -> str:
    """Builds consolidated stable prompt prefix for prompt caching (R65).

    Prefix contains base instruction + persona card + safety constraints.
    """
    parts = [
        "You are an anime character engaging in a private text dialogue with your owner.",
        "Stay strictly in character and speak according to your persona card below.",
        persona_card,
    ]
    if safety_guidelines:
        parts.append(safety_guidelines)

    base_prefix = "\n\n".join(parts)
    return base_prefix
