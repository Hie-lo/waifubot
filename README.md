# waifubot

Single-admin Telegram bot (Persian UI) that generates SFW anime character images through a replaceable provider, lets the admin curate them, and publishes approved images to configured channel(s).

See `SPEC.md` (v3.1) for the full specification and `AGENT.md` for working rules.

## Status

- Sprint 0 (provider feasibility spike): **done — GO (2026-10-16).** Live generation on the user's WSL2 machine succeeded with the user-approved automation profile (recorded in `docs/spike-report.md` and `docs/provider-permission.md`). Style default: site "Anime" (user decision; "no style" also supported via config).
- Sprint 1a (foundation): in progress.
- No application code yet beyond the throwaway spike tools.

## Setup (target: WSL2, Python 3.11)

1. `python3 -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt` (to be added in Sprint 1a)
3. `cp .env.example .env` and fill in values.
4. `python tools/spike/spike_env_check.py`

## Safety

- Never run provider live steps in CI or tests.
- Tests use the mock provider only and block network.
- `.env`, databases, browser profiles, storage, and generated images are gitignored.

## Recovery / backup

Documented per sprint as implemented. Currently N/A.