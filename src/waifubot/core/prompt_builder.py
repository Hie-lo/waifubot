"""Deterministic, free prompt builder (SPEC §6). No LLM, no paid API.

Prompt structure (fixed positions): identity, then style lock, then variable
scene (pose, expression, outfit, background, camera, lighting), then admin text
if any, then provider quality constraints. Seeded RNG; the exact string is
returned so the caller can store it. 2048-char cap. Content checks run on the
final prompt.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from waifubot.core.content_safety import check_blocklist, validate_adult_identity
from waifubot.core.tags_pool import Preset, TagsPool
from waifubot.errors import ValidationError

MAX_PROMPT_CHARS = 2048
_SEED_MAX = 2**31 - 1


@dataclass(frozen=True)
class PromptResult:
    prompt: str
    negative_prompt: str
    preset_ids: tuple[str, ...]
    seed: int
    template_version: str


class PromptBuilder:
    def __init__(self, pool: TagsPool, blocklist: tuple[str, ...]) -> None:
        self._pool = pool
        self._blocklist = blocklist

    @property
    def style_lock(self) -> str:
        return self._pool.style_lock

    def _pick_presets(self, rng: random.Random) -> list[Preset]:
        """One preset per category, weighted, in fixed category order.
        Incompatible combinations are rejected deterministically (drop the
        conflicting candidate and re-pick), never silently mixed (SPEC §7)."""
        chosen: list[Preset] = []
        for _category, presets in self._pool.categories.items():
            candidates = list(presets)
            for _attempt in range(len(presets) + 1):
                total = sum(p.weight for p in candidates)
                roll = rng.uniform(0, total)
                pick = candidates[-1]
                acc = 0.0
                for p in candidates:
                    acc += p.weight
                    if roll <= acc:
                        pick = p
                        break
                chosen_ids = {c.id for c in chosen}
                conflicts = set(pick.incompatible_with) & chosen_ids
                if not conflicts:
                    chosen.append(pick)
                    break
                candidates = [p for p in candidates if p.id != pick.id]
                if not candidates:
                    break  # no compatible preset in this category: skip category
        return chosen

    def build(
        self,
        *,
        identity: str,
        style_lock: str | None = None,
        seed: int | None = None,
        admin_text: str = "",
    ) -> PromptResult:
        validate_adult_identity(identity, self._blocklist)

        if seed is None:
            seed = random.SystemRandom().randrange(1, _SEED_MAX)
        if not 0 < seed < _SEED_MAX:
            raise ValidationError(f"seed must be between 1 and {_SEED_MAX - 1}")
        rng = random.Random(seed)

        chosen = self._pick_presets(rng)
        scene = ", ".join(p.fragment for p in chosen)
        parts = [identity.strip(), (style_lock or self._pool.style_lock).strip()]
        if scene:
            parts.append(scene)
        admin = admin_text.strip()
        if admin:
            parts.append(admin)
        parts.append(self._pool.quality_fragment.strip())
        prompt = ", ".join(p for p in parts if p)

        if len(prompt) > MAX_PROMPT_CHARS:
            # Deterministic cap (SPEC §6): drop the lowest-priority tail parts
            # (quality, then admin, then scene) — identity and style lock always
            # survive; never fail a job over a long-but-allowed identity.
            while len(parts) > 2 and len(prompt) > MAX_PROMPT_CHARS:
                parts.pop()
                prompt = ", ".join(p for p in parts if p)
            if len(prompt) > MAX_PROMPT_CHARS:
                prompt = prompt[:MAX_PROMPT_CHARS]
        check_blocklist(prompt, self._blocklist)

        return PromptResult(
            prompt=prompt,
            negative_prompt=self._pool.negative_prompt,
            preset_ids=tuple(p.id for p in chosen),
            seed=seed,
            template_version=self._pool.template_version,
        )
