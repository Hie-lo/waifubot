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
- **User decisions (2026-10-16, supersede project files):** (1) user instruction > project
  documents on every conflict — recorded as the top amendment in AGENT.md; (2) `ALLOW_NSFW`
  is an admin switch and may be enabled — the SFW-only gate is removed (minor-protection
  checks remain); (3) Selenium is DROPPED (provider bot-checks detect it — user live
  experience) — Playwright async is the single automation tool, and `tools/spike/spike_run.py`
  now delegates its browser flow to the shared `PerchanceProvider` (exact tested code);
  (4) the CLI auto-applies migrations (fixes `no such table: settings`).
  Conflicts reported to the user; remaining hard lines (minor protections, no CAPTCHA
  solving / proxies / auto-retries on blocks) were listed for the user to override if desired.
- **First real live test SUCCEEDED (2026-10-16, user's WSL2):** `live-smoke --confirm-live
  --runs 3` → 3/3 jobs succeeded (4+2+2 images, 96–122 s each). User decisions after the test:
  faster typing (15–45 ms/char) and wait-for-human on challenge pages (default 180 s).
- **Professional review (2026-10-16, user request):** full audit of Sprints 0–2 found and fixed:
  (1) hard limits (20 images/job, 100 jobs/day) were enforced NOWHERE — `check_generation_limits`
  added to the generation service + wired through the CLI; (2) cross-job SHA-256 dedupe missing
  (SPEC §3.3) — pipeline now seeds `seen_sha256` from existing rows; (3) prompts over 2048 chars
  raised and failed the job — now a deterministic cap that never drops identity/style; (4) minor-age
  regex missed hyphen forms ("17-year-old") — regex fixed; (5) `AdminSettings.get` crashed on a
  stale/invalid stored value — now falls back to the default. Verified model↔migration consistency,
  FK pragma, session handling, mock bounds, atomic saves. Landmines documented: self-referential
  `Character.parent/children` needs `awaitable_attrs`; daily-cap day boundary is UTC.
- **Headless + runtime options (2026-10-16, user request):** `live-smoke --headless/--headed`
  visibility flags; challenge flow = window opens only for the challenge, user ticks, window
  closes, work continues headless (bounded `MAX_CHALLENGE_RESTARTS=2`); `set-setting`/
  `show-settings` CLI + `AdminSettings.get_or` (stored > env > default) make caps and all
  SETTINGS_SPECS runtime-changeable today. Pending user test: whether headless delivers
  images on the provider (fallback of human-like checkbox automation authorized but NOT
  implemented until results).
- **Headless test round 1 (2026-10-16):** the actionable diagnostics worked — the page showed
  Cloudflare's "Performing security verification" interstitial, which `detect_block` MISSED
  (so the open-window-for-approval flow never fired). Fixed: interstitial markers added;
  escalation policy: after operator approval the work continues headless; if headless is
  challenged AGAIN the window stays open to finish the job (clearance does not carry to
  headless — pending final test). Also: full Chromium via `channel: "chromium"` (Playwright's
  default headless shell is detectable), one hydration reload, job rows keep actionable
  error codes (`selector_timeout`).

## Confirmed for Sprint 3 (Telegram admin bot)
- `/settings` must expose ALL admin-tunable values including art style (`default_style` key →
  per-provider style mapping) and `style_lock` (in-prompt art style definition) — per user
  decision 2026-10-16 and SPEC §4/§11.
- Needs from the user at Sprint 3 start: bot token (in `.env` only), admin numeric IDs,
  test channel ID.

## Next: Sprint 3 — Telegram admin bot, manual flow
Waiting for explicit user approval.
- **Headless test round 2 (2026-10-16):** the approval flow worked (job succeeded, 3 images)
  but the window opened twice "pointlessly" — the Cloudflare interstitial clears BY ITSELF in
  a real window (no checkbox appears). Policy fixed per user UX rules: ONE window per batch
  (the browser persists across jobs — close+reopen just re-challenges), starts minimized
  (`--start-minimized`, never on top / never steals focus), closes at batch end; no
  close-then-retry-headless anymore. `headless: true` is the default. Headless alone is still
  tried first every batch (it currently gets challenged every time); human-like checkbox
  automation stays the authorized LAST fallback only if a real checkbox blocks unattended work.
- **Headless test round 3 (2026-10-16):** `--start-minimized` is IGNORED on Windows/WSLg —
  the window came up full-size and on top (user report). Decisive fix per user UX rules:
  a launch-mode ladder — pure headless -> VIRTUAL DISPLAY (Xvfb): a real headed browser on an
  invisible screen (pages see a normal windowed browser → the interstitial clears by itself,
  and NOTHING appears on the desktop) -> last resort: one small window with a REAL minimize
  via CDP `Browser.setWindowBounds {windowState: minimized}` (plus --window-size=520,360).
  `pick_launch_mode()` is unit-tested; Xvfb is auto-detected (install once: `sudo apt install
  -y xvfb`); no Xvfb → same flow with a real (minimized) window.
