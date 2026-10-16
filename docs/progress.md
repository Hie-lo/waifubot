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
- Deterministic prompt builder + content safety + image pipeline + generation service +
  CLI (`init-db`, `add-character`, `mock-generate`). 96 tests passing.

## Sprint 2 — Real Playwright provider + Selenium comparison — **DONE (2026-10-16)**
- **Real adapter** `core/providers/perchance.py` (async Playwright): user-approved launch
  profile + human-paced typing (config-driven pacing), iframe-scoped selectors from config,
  hard timeouts, bounded polling, progressive candidate collection, `blocked_by_provider`
  is permanent, categorized actionable errors (`selector_timeout`, `page_error`, …), never
  logs data URLs/cookies. `health_check` never generates an image.
- **Manual live command** `python -m waifubot.cli live-smoke --confirm-live` (refuses
  without the flag; runs ≤ 10, spaced; real jobs + validated images stored via the 1b pipeline).
- **Selenium comparison probe** `tools/spike/selenium_compare.py` (throwaway, opt-in): the
  "test both tools" comparison; production stays on async Playwright (already proven).
- **Typed admin settings** `core/admin_settings.py` (user decision 2026-10-16: art style and
  every option must be changeable from the bot): `default_style`, `style_lock`, `images_per_job`,
  `daily_cap`, `posting_interval_s`, `retention_days` — code-defined type/range/default; DB
  values can never exceed env hard limits. Sprint 3 `/settings` will expose all of them.
- Tests: 112 passing (no network/browser), ruff clean. Live path is manual-only (user machine).
- **Bugfix (2026-10-16, user-reported):** the CLI never loaded `.env` (BOT_TOKEN error on the
  user's machine) — now `load_env_file()` loads `<repo>/.env` at startup, and the dev CLI no
  longer requires Telegram secrets at all (the Sprint 3 bot will). Selenium probe repaired
  (textarea clear + ActionChains hover) to mirror the working PoC. 115 tests passing.

## Confirmed for Sprint 3 (Telegram admin bot)
- `/settings` must expose ALL admin-tunable values including art style (`default_style` key →
  per-provider style mapping) and `style_lock` (in-prompt art style definition) — per user
  decision 2026-10-16 and SPEC §4/§11.
- Needs from the user at Sprint 3 start: bot token (in `.env` only), admin numeric IDs,
  test channel ID.

## Next: Sprint 3 — Telegram admin bot, manual flow
Waiting for explicit user approval.
