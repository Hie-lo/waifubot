# Progress log

Living document — updated every sprint.

## Sprint 0 — Provider feasibility spike — **DONE (GO, 2026-10-16)**
- Live runs on the user's WSL2 machine: selector bug fixed (iframe), plain fill blocked,
  human typing blocked, **user's PoC automation profile succeeded** (user decision, recorded
  in AGENT.md amendment log + `docs/provider-permission.md`).
- Style default: site "Anime" (user decision); "no style" supported via config; the spike
  prints the page's real style option values.
- Metrics (`spike_metrics.csv`): 2/2 successful runs on the final profile, 8 valid JPEG
  images, 95–125 s per run. Full report: `docs/spike-report.md`. Open: peak RAM (A4), `.wslconfig` cap.

## Sprint 1a — Foundation — **DONE (2026-10-16)**
- Structure: `src/waifubot/` package (config with hard caps, error taxonomy, db, locale),
  `locales/fa.yaml` skeleton, Alembic baseline `0001`, tests (network blocked), Makefile,
  `pyproject.toml` (ruff/pytest), pinned `requirements.txt`, pre-commit (ruff).
- 5 tables + CHECK/unique constraints + indexes; FK enforcement on SQLite; schema inspect command.

## Sprint 1b — Vertical slice with mock provider — **DONE (2026-10-16)**
- Provider interface (`core/image_provider.py`) frozen against the spike findings
  (explained in `docs/provider-interface.md`); mock provider simulating all outcomes;
  Perchance adapter config-gated until Sprint 2 (`config/providers/perchance.yaml` holds
  the proven selectors + Anime style mapping + approved launch profile).
- Deterministic prompt builder (`core/prompt_builder.py`): seeded RNG, tag pool
  (`config/tags_pool.yaml`), fixed structure identity → style lock → scene → quality,
  2048-char cap, golden snapshot test. Content safety (`core/content_safety.py`):
  adult-identity validation + minor-coded blocklist on the final prompt (`config/blocklist.yaml`).
- Image pipeline (`core/image_pipeline.py`): SHA-256 dedupe, Pillow validation with
  decompression-bomb guard, format-derived extensions, atomic UUID-named saves.
- Generation service (`services/generation.py`): SPEC §10 job lifecycle; `blocked_by_provider`
  is permanent. CLI (`python -m waifubot.cli`): `init-db`, `add-character`, `mock-generate`
  — the full flow was run for real (job succeeded, 3 validated images stored).
- Tests: 96 passing, network blocked in all tests; ruff clean.

## Next: Sprint 2 — Real Playwright provider (+ Selenium comparison)
Re-verify the style option values from the page dump, adapt with hard timeouts, small live
smoke test (3 runs) with prior notice to the user. Waiting for explicit user approval.

