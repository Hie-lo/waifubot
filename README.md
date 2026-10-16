# waifubot

Single-admin Telegram bot (Persian UI) that generates SFW anime character images through a replaceable provider, lets the admin curate them, and publishes approved images to configured channel(s).

See `SPEC.md` (v3.1) for the full specification and `AGENT.md` for working rules.

## Status

- Sprint 0 (provider feasibility spike): **done — GO (2026-10-16).** Live generation on the user's WSL2 machine succeeded with the user-approved automation profile (recorded in `docs/spike-report.md` and `docs/provider-permission.md`). Style default: site "Anime" (user decision; "no style" also supported via config).
- Sprint 1a (foundation): **done (2026-10-16).** Config with hard caps, 5 tables + Alembic baseline, fa locale skeleton, test harness (network blocked). See `docs/progress.md`.
- Next: Sprint 1b (vertical slice with mock provider) — waiting for explicit approval.

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

## Safety

- Never run provider live steps in CI or tests.
- Tests use the mock provider only and block network.
- SFW only in the MVP (`ALLOW_NSFW=false` is a hard gate).
- `.env`, databases, browser profiles, storage, and generated images are gitignored.

## Recovery / backup

Documented per sprint as implemented. Currently: the SQLite DB lives at `DB_URL` (default `./storage/waifubot.db`); back it up together with `storage/images/` and `.env` (see SPEC §12).
