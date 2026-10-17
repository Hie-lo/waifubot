"""Content safety checks (R26, R39).

Contract:
- Minor protection is absolute: any prompt/character implying or depicting
  a minor is rejected, regardless of the NSFW toggle.
- Forbidden real-person name base stays in force.
- No adult-content restriction: adult NSFW is governed solely by user consent (R22/R26).
"""

import re
import unicodedata

# Normalized lower-case patterns indicating minors
_MINOR_PATTERNS = [
    r"\bloli\b",
    r"\blolicon\b",
    r"\bshota\b",
    r"\bshotacon\b",
    r"\bchild\b",
    r"\bchildren\b",
    r"\bunderage\b",
    r"\btoddler\b",
    r"\binfant\b",
    r"\bbaby\b",
    r"\bkindergarten\b",
    r"\belementary school\b",
    r"\bmiddle schooler\b",
    r"\bmiddle school\b",
    r"\bkid\b",
    r"\bkids\b",
    r"\bminor\b",
    r"\bminors\b",
]

# Patterns for forbidden real persons
_REAL_PERSON_PATTERNS = [
    r"\bpolitician\b",
    r"\bcelebrity\b",
]


def check_safety(text: str) -> tuple[bool, str | None]:
    """Check text against R26 safety hard rules.

    Returns:
        (is_safe, failure_reason_key)
    """
    if not text:
        return True, None

    normalized = unicodedata.normalize("NFKC", text).lower()

    for pattern in _MINOR_PATTERNS:
        if re.search(pattern, normalized):
            return False, "safety_rejected_minor"

    for pattern in _REAL_PERSON_PATTERNS:
        if re.search(pattern, normalized):
            return False, "safety_rejected_real_person"

    return True, None


def recheck_enhancer_output(original: str, enhanced: str) -> tuple[str, bool, str | None]:
    """Re-check AI enhancer output against safety rules (R39).

    If enhanced text fails safety, soft-fails back to original text with reason.
    Returns:
        (final_text, passed_enhancement, failure_reason)
    """
    is_safe, reason = check_safety(enhanced)
    if is_safe:
        return enhanced, True, None

    # Soft fail: keep original text
    return original, False, reason
