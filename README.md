# waifubot

Single-admin Telegram bot (Persian UI) that generates SFW anime character images through a replaceable provider, lets the admin curate them, and publishes approved images to configured channel(s).

See `SPEC.md` (v3.1) for the full specification and `AGENT.md` for working rules.

## Status

- Sprint 0 (provider feasibility spike): **done — GO (2026-10-16).** 2/2 successful live runs (8 valid JPEG images, 95–125 s/run) with the user-approved automation profile (`docs/spike-report.md`, `docs/provider-permission.md`). Style default: site "Anime".
- Sprint 1a (foundation): **done (2026-10-16).** Config with hard caps, 5 tables + Alembic baseline, fa locale skeleton, network-blocked tests.
- Sprint 1b (vertical slice with mock provider): **done (2026-10-16).** Provider interface + mock provider, deterministic prompt builder + content safety, image validation pipeline, CLI (`init-db`, `add-character`, `mock-generate`). See `docs/progress.md`.
- Sprint 2 (real Playwright provider): **done (2026-10-16).** Real adapter with the approved automation profile, `live-smoke --confirm-live` manual command, typed admin settings (art style + all options changeable — Sprint 3 `/settings` will expose them). 115+ tests passing. **Real-testable: `live-smoke` (see below).** User decisions applied: `ALLOW_NSFW` is an admin switch (may be on); Selenium dropped (detected by the provider) — Playwright is the single tool; the spike and the product share one browser flow.
- Next: Sprint 3 (Telegram admin bot, manual flow) — waiting for explicit approval.

## Setup (target: WSL2, Python 3.11)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
make migrate           # create the database
make test              # run the test suite (no network needed)
make inspect-schema    # print the database DDL
```

Live provider runs (Sprint 0/2 only, after explicit approval) use `tools/spike/` — see `tools/spike/README.md`. They always require `--confirm-live` and open a real browser.

## CLI (Sprint 1b, dev tool — the admin UI is Telegram in Sprint 3)

```bash
PYTHONPATH=src python3 -m waifubot.cli init-db
PYTHONPATH=src python3 -m waifubot.cli add-character --name Aki \
    --identity "adult woman, 25 years old, long blue hair, red eyes"
PYTHONPATH=src python3 -m waifubot.cli mock-generate --character Aki --count 4 --seed 42
```

## Live smoke test (Sprint 2 — real provider, on the user's machine only)

Opens a real browser and sends real generation requests. Always opt-in:

```bash
PYTHONPATH=src python3 -m waifubot.cli live-smoke --confirm-live --runs 3
```

- Refuses to run without `--confirm-live` (exit 2). Runs are bounded (max 10) and spaced (default 30 s).
- Style comes from the `default_style` admin setting ("Anime" by default; use `--style no_style` for prompt-only style).
- Images are validated and stored as `pending_review` like any real job. Migrations are applied automatically — no `init-db` needed first.
- The browser flow is literally the tested code: the spike (`tools/spike/spike_run.py`) and this command share `PerchanceProvider`.

## Safety

- Never run provider live steps in CI or tests.
- Tests use the mock provider only and block network.
- SFW only in the MVP (`ALLOW_NSFW=false` is a hard gate).
- `.env`, databases, browser profiles, storage, and generated images are gitignored.

## Recovery / backup

Documented per sprint as implemented. Currently: the SQLite DB lives at `DB_URL` (default `./storage/waifubot.db`); back it up together with `storage/images/` and `.env` (see SPEC §12).
