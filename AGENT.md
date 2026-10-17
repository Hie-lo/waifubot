# AGENT.md — Rules for the coding agent (WaifuBot)

Read this file at the start of every session. The full specification is in `SPEC.md`; read the sections relevant to the current sprint. If the two ever disagree, stop and ask.

## Working protocol

1. At the start of a session: inspect the repository, `git status`, Python version, free disk and RAM. Never delete, reset, or overwrite existing work. Read a tracked file before editing it.
2. Work on **one sprint at a time**. The user names the current sprint. Do not start the next one.
3. At the end of the sprint, send the short report (template below) and **stop until the user explicitly approves the next sprint**.
4. Report only what you actually ran: the real command and the real result. If something was not tested, say "not tested". Never write "done" or "working" for untested behavior.
5. Ask only questions that block the current sprint, and only after checking the repo and config for the answer.
6. If the architecture needs a major change, explain it and wait for approval before implementing it.
7. Anything marked **[VERIFY]** in `SPEC.md` is an unverified external fact. Check it against the official documentation or a real test before relying on it, and report what you found.

## Hard prohibitions

- **Provider access.** Never run, import, or execute the Playwright proof-of-concept, and never open a provider page, except in the live steps of Sprint 0 and Sprint 2 after the user has explicitly approved that sprint. Before any live step, tell the user that a browser will open and a real generation request will be sent. Never at startup, in tests, in CI, or during repository inspection.
- **No circumvention.** On a CAPTCHA, challenge, explicit rate limit, or access-denied page: stop the job, record `blocked_by_provider`, and report. Do not add stealth flags, bypass logic, proxy rotation, or extra retries. Do not exceed the rate and scope recorded in `docs/provider-permission.md`.
- **No network in tests.** Tests use the mock provider and must not reach any external site or API.
- **No secrets in Git.** Never commit `.env`, tokens, cookies, browser profiles, databases, or generated images. Never log them.
- **Bounded everything.** No unbounded loops, retries, generations, scheduled jobs, or disk growth. Every limit is a configuration value with a hard maximum.
- **No invention.** Do not invent selectors, provider options, seeds, or image metadata. If the page or API does not provide it, store nothing.
- **Scope.** Do not build chat, memory, payments, NSFW, local diffusion models, public multi-user access, or Ollama integration. They are out of scope until the user approves them in writing.
- **Content safety.** SFW only. Every character must be explicitly adult. Do not weaken, bypass, or make optional the content checks in `SPEC.md` section 8.

## Amendment log

- **2026-10-16 (HIGHEST PRIORITY — user standing rule):** the user's live instructions supersede the project documents (`SPEC.md`, `AGENT.md`, blueprints) whenever they conflict. When a conflict exists, the agent must state it clearly and apply the user's decision. The project files describe the plan; the user's word is the authority.
- **2026-10-16 (user decision):** the user's working proof-of-concept automation profile is approved and adopted: persistent browser profile, `slow_mo`, human-paced character-by-character typing with small pauses, and the Chromium argument `--disable-blink-features=AutomationControlled`. Background: controlled Sprint 0 spike runs without this profile were blocked by the provider at the generate step (plain `fill()` first, then human-paced typing alone), while the user's PoC with this profile generates successfully on the same machine; the user explicitly directed the agent to use that code. This exception is limited to that automation profile. Solving CAPTCHAs, bypassing challenge or access-denied pages, proxy rotation, and extra retries on `blocked_by_provider` remain prohibited. Recorded in `docs/provider-permission.md`.
- **2026-10-16 (user working rule):** at the end of every stage/sprint, if the deliverable has a real-test capability, the agent must tell the user how to test it on their machine (exact commands).
- **2026-10-16 (user decision, art style & options):** the bot must let the admin define the art style and change every tunable option at runtime (like all other options). Backed by the typed `settings` table service (`src/waifubot/core/admin_settings.py`, incl. `default_style` + `style_lock`) and exposed by `/settings` in Sprint 3 (SPEC §4/§11).
- **2026-10-16 (user decision, NSFW):** `ALLOW_NSFW` is an admin-controlled switch and MAY be enabled — the "SFW-only MVP / ALLOW_NSFW must be false" gate is removed (supersedes SPEC §8's "SFW only" line). It stays an environment setting (no Telegram override). The minor-protection checks (adult-identity validation + minor-coded blocklist on the final prompt) remain mandatory and unchanged.
- **2026-10-16 (user decision, automation tool):** Selenium is DROPPED. The user's live experience: the provider's browser bot-checks detect Selenium and return bot errors during generation. Playwright async (the user's working PoC profile) is the single approved automation tool; the "test both tools" comparison is resolved by this experience. The spike and the product now literally share one browser flow (`PerchanceProvider`) — no drift.
- **2026-10-16 (user request, typing speed):** typing may be FAST (user: "متن خیلی طول میکشه نوشته بشه یکم سریعتر"). Default pacing is now 15–45 ms/char with a 5% short pause (config `human_pacing`). If the provider blocks us again, raise the delays — the pacing stays config-driven.
- **2026-10-16 (user request, challenge pages):** when a Cloudflare/challenge page appears, the automation WAITS (default 180 s, config `challenge_wait_s`) for the user to approve it in the visible browser window and continues if it clears. It NEVER solves or bypasses the challenge itself (prohibition unchanged); after the wait budget it is `blocked_by_provider` (permanent). The user confirmed the challenge appears once per persistent profile.
- **2026-10-16 (user STANDING RULE, runtime-changeable options):** implement as many options as possible to be changeable at runtime — in bot settings or env — and ASK the user before adding/choosing such options. Delivered so far: `set-setting`/`show-settings` CLI + env fallbacks (`get_or`: stored setting > env default > code default; hard limits always clamp) for `daily_cap`, `images_per_job`, `default_style`, `style_lock`, `posting_interval_s`, `retention_days`; `live-smoke --headless/--headed`.
- **2026-10-16 (user request, headless + challenge flow):** image jobs must run WITHOUT opening a Chrome window (`live-smoke --headless` tests this; the exact proven flow). Only when a challenge appears: the window opens, the USER ticks it, the window closes and the work continues headless (`challenge_wait_s`, `MAX_CHALLENGE_RESTARTS=2`). **Pending test (user: test first):** if headless cannot deliver (or approval does not carry to headless), the user authorizes as FALLBACK human-like mouse simulation to tick the checkbox — do NOT implement before the test results are in.
- **2026-10-16 (user UX RULE — windows must not disturb):** any browser window that must open shall NEVER come on top or steal focus: it starts minimized (`open_minimized` → `--start-minimized`), works underneath without disturbing the user, and closes when the batch ends. Headless is the default (`headless: true`); one window per batch (the browser persists across jobs — close+reopen just re-triggers the challenge, learned in the live test). The Cloudflare interstitial usually clears BY ITSELF in a real window (no checkbox appears) — "challenge cleared" is often automatic, which is success. Human-like checkbox automation remains the authorized LAST fallback only if a real checkbox blocks unattended work.
- **2026-10-16 (user UX RULE, decisive):** `--start-minimized` does NOT work on Windows/WSLg (window came up full-size and on top — user report). Correct implementation: (1) preferred — run a REAL headed browser on an invisible Xvfb display (`virtual_display: auto`, install `xvfb`): pages see a normal windowed browser (the Cloudflare interstitial clears by itself) and NO window ever appears on the desktop; (2) last resort — ONE small (520x360) window, genuinely minimized via CDP `Browser.setWindowBounds {windowState: "minimized"}`, never on top, closing at batch end. Mode ladder (unit-tested `pick_launch_mode`): headless -> virtual -> window; explicit `--headless` / `--headed` flags override.

## Sprint report template

```text
Sprint N — Report
1. Scope completed
2. Files changed
3. Tests run: command and actual result
4. Tests not run, and why
5. [VERIFY] items checked, and what was found
6. Risks and open issues
7. Decisions needed from the user
Status: waiting for explicit approval to continue.
```
