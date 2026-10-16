"""Content safety checks (SPEC §8, amended by user decisions 2026-10-16).

USER DECISION 1: ALLOW_NSFW is an admin switch — the SFW-only MVP gate is gone.
USER DECISION 2 (2026-10-16, latest word wins): the "adult" framing restriction
is REMOVED — identities no longer need to state an adult age or 'woman'; the
site itself does not require it.

What REMAINS (standing user rule: minor protection is mandatory): the
minor-coded blocklist and the stated-minor-age rejection, on identities AND on
the final prompt. Human (admin) review stays the primary control.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from waifubot.errors import ConfigError, ValidationError

# A stated age below 18 anywhere in the prompt is always rejected.
# (Review 2026-10-16: the hyphen form "17-year-old" previously slipped through.)
MINOR_AGE_RE = re.compile(
    r"\b(?:[1-9]|1[0-7])\s*[- ]?\s*(?:years?[\s-]*old|yo)\b", re.IGNORECASE
)

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
    """Minor protection only (standing user rule): reject minor-coded terms and
    stated minor ages. No 'adult' framing is required (user 2026-10-16)."""
    text = identity.strip()
    if not text:
        raise ValidationError("identity_prompt must not be empty")
    lowered = text.lower()
    for term in blocklist:
        if term in lowered:
            raise ValidationError(f"identity_prompt contains blocked term: {term!r}")
    if MINOR_AGE_RE.search(text):
        raise ValidationError("identity_prompt states a minor age")


def check_blocklist(prompt: str, blocklist: tuple[str, ...]) -> None:
    """Blocklist runs on the FINAL prompt, including admin-typed text (SPEC §8)."""
    if MINOR_AGE_RE.search(prompt):
        raise ValidationError("final prompt states a minor age")
    lowered = prompt.lower()
    for term in blocklist:
        if term in lowered:
            raise ValidationError(f"final prompt contains blocked term: {term!r}")
