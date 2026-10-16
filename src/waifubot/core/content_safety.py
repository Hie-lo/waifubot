"""Content safety checks (SPEC §8). SFW only; every character explicitly adult.

Human (admin) review is the PRIMARY control. These automated checks reduce
risk: a text rule cannot make a provider draw an adult, and keyword lists can
be bypassed. False positives are safe — they reject a job and cost nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from waifubot.errors import ConfigError, ValidationError

# A stated age below 18 anywhere in the prompt is always rejected.
MINOR_AGE_RE = re.compile(r"\b(?:[1-9]|1[0-7])\s*[- ]?\s*(?:years?\s*old|yo)\b", re.IGNORECASE)
# Identity must state an adult age (18+) or the word "adult"...
ADULT_AGE_RE = re.compile(
    r"\b(?:1[89]|[2-9]\d)\s*[- ]?\s*(?:years?\s*old|yo)\b|\badult\b", re.IGNORECASE
)
# ...and adult features.
ADULT_FEATURE_RE = re.compile(r"\b(?:woman|adult|mature)\b", re.IGNORECASE)


def load_blocklist(path: Path) -> tuple[str, ...]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"cannot read blocklist {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"malformed blocklist YAML {path}: {e}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("terms"), list):
        raise ConfigError(f"blocklist {path} must be a mapping with a 'terms' list")
    terms = tuple(str(t).strip().lower() for t in raw["terms"] if str(t).strip())
    if not terms:
        raise ConfigError(f"blocklist {path} must contain at least one term")
    return terms


def validate_adult_identity(identity: str, blocklist: tuple[str, ...]) -> None:
    """A character's identity must state adult age and adult features (SPEC §8)."""
    text = identity.strip()
    if not text:
        raise ValidationError("identity_prompt must not be empty")
    lowered = text.lower()
    for term in blocklist:
        if term in lowered:
            raise ValidationError(f"identity_prompt contains blocked term: {term!r}")
    if MINOR_AGE_RE.search(text):
        raise ValidationError("identity_prompt states a minor age")
    if not ADULT_AGE_RE.search(text):
        raise ValidationError("identity_prompt must state an adult age (18+) or 'adult'")
    if not ADULT_FEATURE_RE.search(text):
        raise ValidationError("identity_prompt must state adult features (e.g. 'woman')")


def check_blocklist(prompt: str, blocklist: tuple[str, ...]) -> None:
    """Blocklist runs on the FINAL prompt, including admin-typed text (SPEC §8)."""
    if MINOR_AGE_RE.search(prompt):
        raise ValidationError("final prompt states a minor age")
    lowered = prompt.lower()
    for term in blocklist:
        if term in lowered:
            raise ValidationError(f"final prompt contains blocked term: {term!r}")
