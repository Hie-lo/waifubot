# Progress log

Living document — updated every sprint.

## Sprint 0 — Provider feasibility spike — **DONE (GO, 2026-10-16)**
- Live runs on the user's WSL2 machine: selector bug fixed (iframe), plain fill blocked,
  human typing blocked, **user's PoC automation profile succeeded** (user decision, recorded
  in AGENT.md amendment log + `docs/provider-permission.md`).
- Style default: site "Anime" (user decision); "no style" supported via config; the spike
  prints the page's real style option values.
- Full report: `docs/spike-report.md`. Open items there: paste metrics CSV numbers, `.wslconfig` cap.

## Sprint 1a — Foundation — **DONE (2026-10-16)**
- Structure: `src/waifubot/` package (config with hard caps, error taxonomy, db, locale),
  `locales/fa.yaml` skeleton, Alembic baseline `0001`, tests (network blocked), Makefile,
  `pyproject.toml` (ruff/pytest), pinned `requirements.txt`, pre-commit (ruff).
- 5 tables + CHECK/unique constraints + indexes; FK enforcement on SQLite; schema inspect command.
- Exit criteria: clean checkout sets up; schema documented (`docs/schema.md`) and tested; tests pass.

## Next: Sprint 1b — Vertical slice with mock provider
(mock provider, prompt builder + tags pool, image validation pipeline, CLI). Waiting for
explicit user approval.
