# WaifuBot — Specification v4.0

**Product:** a multi-admin Telegram bot (Persian-first UI, full English locale) that generates mostly anime character images through replaceable anti-captcha providers, lets admins curate them into a pool, and publishes approved images — directly to channels, as albums to groups, or on a schedule from the pool. It carries a character-identity system (appearance facts separate from scenes), an AI-enhance step for prompts, and NSFW support with spoilers.

**Environment:** Debian on WSL2, 16 GB RAM, Intel i5-12400, no usable GPU inside WSL (upgradeable).

**Status:** this document is the single source of truth for *what the product is and how the code is organized*. It supersedes **Specification v3.1** (archived verbatim at `docs/SPEC-v3.1-archive.md`; git history keeps everything else).

> **Golden rule:** the user's word supersedes this file. When a live instruction contradicts anything here, follow the instruction and report the conflict in the delivery report.

*How to read:* numbers marked *starting default* are tunable guesses. **[VERIFY]** marks external facts that must be checked before code depends on them.

---

## 1. Product, roles, scope

### 1.1 Core workflow

```
user scene + character data → prompt (+ optional AI enhance) → job
→ provider generates an EXACT image count → pipeline validates → admin reviews
→ approved images enter the pool → published (direct / album / schedule)
```

Approval happens once per image, at review time. Nothing unreviewed is ever published, including by the scheduler.

### 1.2 Roles

| Role | Powers |
|---|---|
| **superadmin** | everything + admin management + admin-settings panel |
| **admin** | generate, review, pool, publish, characters, queue, settings, scheduler |
| **user** | start/help only |

Admins are stored in the DB (`is_superadmin`); env `ADMIN_IDS` seeds them at startup. `/manage` is the hub; every item is button-driven.

### 1.3 In scope

Provider abstraction (mock + Perchance now; Zovla/pixai and OnlyWaifus later), prompt building from character facts + user scene, image pipeline (validation, hashing, atomic storage), full Telegram admin UX, job queue with workers and exact-count enforcement, pool + direct + scheduled publishing (channels and group albums), character identity system with versioning, AI-enhance step, NSFW handling with spoilers, durable state (SQLite + Alembic), Persian/English locales, logs, safe errors, recovery, anti-captcha policy.

### 1.4 Out of scope (for now)

Web admin panel (path reserved), paid NSFW channel button (marked **بعداً**), AI captions for albums (typed captions now), multi-provider routing/retries, automatic challenge solving (explicitly forbidden — see §2.7).

---

## 2. Current rules (2026-10-17) — the contract

These are binding behavior rules. Every change must preserve them; the review pass checks them.

### 2.1 UI & navigation

- **R1 — Interactive-first.** Every action is a button/menu where possible; typed input only where the user must type (prompts, names, captions, scenes). Toggles are buttons, never "type 1/0".
- **R2 — Bottom keyboard = sections.** The reply keyboard holds the main sections (labels from locales; main buttons live in the bottom keyboard area). Pressing a bottom button — or typing a command — **starts a NEW panel message** under the user's message (the press is visible chat activity; the answer must sit right under it).
- **R3 — One panel per menu.** Every interaction *inside* a menu (inline buttons, wizard steps, typed dialog answers) **edits the same panel message in place** — never a new message. Tracked in `PanelMemory` (`settings.panel:<admin_id>` → JSON `[chat_id, message_id]`).
- **R4 — Back & close.** Every screen except the main menu has a working **back** button (to the previous level); every panel has a **close** button (deletes the panel message, hides the reply keyboard). The main reply keyboard is non-persistent (`is_persistent=False`).
- **R5 — Viewer edits in place.** Pool and inbox viewers: prev/next/toggles **edit the same message** (`edit_message_media` / `edit_message_caption`); closing strips the buttons and leaves the photo. Never a new photo per navigation press.
- **R6 — Fast typing.** A new typed prompt while a wizard is open silently abandons the old wizard and starts fresh with the new text (long prompts supported).
- **R7 — No dead ends.** Every rejection/error is explained (locale text) with an actionable button when one exists.
- **R8 — Callback audit.** Every callback prefix is routed (review-pass audit: 72/72 today); callback data ≤ 64 bytes.

### 2.2 Generation & image counts

- **R9 — Exact counts.** Supported counts are exactly the site's options **2/4/6/8/16** (`SITE_COUNTS`). A job succeeds **only when exactly N images exist**. A short batch never silently succeeds:
  - pipeline rejects (malformed/oversized/duplicate bytes) and slow-partial provider batches count as **undercount**;
  - `error_code = partial_count` (or `no_valid_images` when zero) with a summary line (`undercount: got/need (rejected: …)`);
  - **already-produced images are kept** and shown;
  - an automatic **top-up requeue** requests only the missing count (`needed = N - already`) and continues until exactly N — this is the default path (site glitches self-heal);
  - when attempts run out: `failed` + retry button; the manual retry resets `attempt_count` and keeps topping up.
- **R10 — No stale gallery images.** Before clicking generate, the provider snapshots the page gallery; any pre-existing image URL is excluded from the new batch's collection (cross-attempt and cross-job).
- **R11 — Pipeline is not allowed to shrink the contract.** Everything the filter drops is accounted for at the job gate (R9).
- **R12 — Retry + notify.** Failed/partial jobs retry up to configured attempts with `RETRY_BACKOFF_SECONDS` backoff; job completion/failure always notifies (admin(s)).
- **R13 — Interruptions announced.** On startup, recovered interrupted jobs are announced to admins (`interrupted_notice` per job).

### 2.3 Characters & identity

- **R14 — Character data = appearance facts ONLY:** name, anime, age, height, hair/eye/skin color, face shape, body type, body-part sizes (NSFW). No actions, expressions, poses, clothes, or scenes in character data.
- **R15 — Scene at generation.** The user types the scene/action per generation; prompt build combines **facts first, scene after** (facts override templates, never the reverse).
- **R16 — Known characters.** The user types name + anime (example provided in the dialog); AI-enhance may append canonical details.
- **R17 — Save button policy.** The "save character" button appears **only** on generated-image jobs. Jobs generated *from* a saved character must never show it.
- **R18 — Edit = new version.** Editing a saved character creates a new row (`edit_of`, parent link, label "نسخه N"); the previous version stays alive and linked.
- **R19 — My-characters screen** lists all characters with version info; create/edit are full button flows with confirmation and back buttons.

### 2.4 AI enhance

- **R20 — Both confirmations.** The "🪄 AI Enhance" button appears at the **character-prompt confirm** *and* the **final-prompt confirm**. Enhancement must preserve every user detail (style, flags, subject, scene) and only add: canonical details for known characters (name + anime given), nothing for unknown ones.
- **R21 — Model is env-only.** `ai_enhance_enabled` is a 0/1 toggle button in the bot settings; **`ai_enhance_model` is not a bot setting at all** — it is read from `AI_ENHANCE_MODEL` (Claude models; Haiku recommended). Same for `ANTHROPIC_API_KEY`.

### 2.5 NSFW & publishing

- **R22 — NSFW per generation**, toggle button + checkbox confirmation; NSFW images post with a spoiler (`has_spoiler=True`); a mixed album may contain spoiler and non-spoiler images together (spoiler is per-image).
- **R23 — Group albums:** 2–10 images per album, typed caption (AI captions later), group/topic picker, per-image delete with live count, split preview when > 10.
- **R24 — Publish errors are user-visible** (`publish_too_large` with guidance; `publish_failed` on API failure).

### 2.6 Settings & safety

- **R25 — Everything is changeable in the bot.** The settings panel exposes every runtime knob; `.env` supplies *defaults and operational secrets only*. Code-level **safety ceilings** (max concurrent jobs 20, workers 100) bound user input. Values save on change and apply immediately (workers re-read settings per iteration).
- **R26 — No silent dead ends.** Unauthorized actions answer with `not_authorized` (not silence).

### 2.7 Anti-captcha & challenges

- **R28 — Never solve challenges.** Perchance options (solve 1×, enable 1×, solve+enable) are offered first; the challenge window opens **only** for an actual challenge (reuse the same single window/tab); the human hover-checkbox is the **last fallback**; a failed challenge stops with a retry button. No automated puzzle/text challenge solving, ever. **Playwright async API only.** Provider sessions are reused.

### 2.8 Engineering process

- **R29 — User word supersedes files** (report the conflict).
- **R30 — One sprint/stage at a time**; short plain-Persian report with a real-test command; **STOP** until approval; large work is split into checklists; minimal error probability over speed.
- **R31 — Review pass after every stage:** dead code, unused locale keys, unrouted callbacks, count semantics, secret scan, tests green.
- **R32 — No live provider runs** in tests/CI (only via explicit `--confirm-live`); Playwright async everywhere.
- **R33 — Locale parity:** Persian-first, complete English; `REQUIRED_KEYS` subset enforced by tests for both files.

---

## 3. Project architecture (the path)

### 3.1 Principles

1. **Layered, inward dependencies.** `bot` (delivery) → `services` (application) → `core` (domain). Infrastructure (`db`, provider adapters, future `web`) implements domain ports. Nothing in `core` imports the Telegram framework or SQLAlchemy.
2. **Ports & adapters.** `GenerationProvider` (core/providers/base) is the provider port; `BotGateway` (bot/gateway) is the Telegram port; `PromptBuilder` is pure; tests plug fakes of each (tests/fakes.py). New providers = new adapters + a `config/providers/*.yaml` — zero core changes.
3. **Async end-to-end.** asyncio + async SQLAlchemy + async Playwright (`PLAYWRIGHT_ASYNC_API=1`). One session per unit of work (`session_scope`); long provider runs never block the bot loop.
4. **Config split.** `env` = defaults + operational secrets (tokens, `AI_ENHANCE_MODEL`, limits). DB `settings` = runtime knobs (R25). Code ceilings bound both.
5. **State durability.** Every flow can resume: DB jobs + images, `user_memory` (wizard state), `settings` (panel coordinates, scheduler state). Recovery is a first-class module (bot/recovery.py) and announces itself (R13).
6. **Tests mirror the architecture** (§5): fakes as adapters, contract tests per service, one end-to-end bot flow harness.

### 3.2 Project tree (current — v4.0)

```
waifubot/
├── SPEC.md                       ← this file (single source of truth)
├── AGENT.md                      ← working rules for agents/humans
├── README.md, Makefile, pyproject.toml, requirements.txt
├── .env.example                  ← every env key documented
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002_image_is_nsfw.py
│       ├── 0003_post_caption.py
│       └── 0004_character_details.py
├── config/
│   ├── blocklist.yaml            ← forbidden-name base (rules layer adds minors/RL)
│   ├── identity_pool.yaml        ← randomized identity labels (humor/content)
│   ├── tags_pool.yaml            ← quality/style tag pools
│   └── providers/
│       ├── mock.yaml             ← deterministic test provider
│       └── perchance.yaml        ← site config (reads site-config page at runtime)
├── docs/
│   ├── SPEC-v3.1-archive.md      ← superseded spec (verbatim archive)
│   ├── schema.md, progress.md, spike-report.md,
│   ├── provider-interface.md, provider-permission.md
├── locales/
│   ├── fa.yaml                   ← Persian-first (canonical length)
│   └── en.yaml                   ← full parity (REQUIRED_KEYS enforced)
├── scripts/
│   └── upsert_admin.py
├── src/waifubot/
│   ├── __init__.py
│   ├── config.py                 ← pydantic-settings; env defaults + ceilings
│   ├── logging_setup.py          ← logging.json (console + rotating file)
│   ├── cli.py                    ← typer: init-db, run-bot, admin upsert, gen
│   ├── core/                     ← DOMAIN: pure logic, no framework imports
│   │   ├── models.py             ← GenerationRequest, ImageAsset, ProviderJob…
│   │   ├── prompts.py            ← PromptBuilder (facts → scene → flags; R15)
│   │   ├── safety.py             ← hard rules: minors, blocklist, RL heuristics
│   │   ├── images.py             ← MIME/EXIF, validation, spoiler, size caps
│   │   ├── http.py               ← shared async HTTP + stealth headers
│   │   ├── telegram_html.py      ← safe escaping
│   │   ├── topics.py             ← forum-topic normalization
│   │   ├── identity.py           ← character FACTS + scene merge (R14–R18)
│   │   ├── ai_enhance.py         ← Anthropic enhancer (R20; model env-only R21)
│   │   ├── admin_settings.py     ← settings registry + safety ceilings (R25)
│   │   └── providers/            ← anti-captcha provider adapters
│   │       ├── base.py           ← GenerationProvider port + result types
│   │       ├── captcha/          ← detection, challenge runner, errors
│   │       │   ├── types.py, errors.py
│   │       │   ├── challenge_runner.py  ← dialog detect + human hover (R28)
│   │       │   ├── conversation.py      ← typed input, ultra mode, sleep trigger
│   │       │   ├── controls.py          ← read settings, enable-solution counter
│   │       │   └── api.py               ← generator API + payload parse
│   │       └── site/
│   │           ├── mock.py       ← MockProvider (deterministic)
│   │           └── perchance.py  ← PerchanceProvider (gallery snapshot R10,
│   │                               count readback/apply, exact-need request R9)
│   ├── db/                       ← PERSISTENCE: SQLAlchemy 2.0 async
│   │   ├── base.py, models.py    ← Job, Image, Post, Setting, UserMemory,
│   │   │                            Admin, Character (+ edit_of, R18)
│   │   ├── session.py            ← engine factory, session_scope, migration-check
│   │   └── worker.py             ← (service-side in services/; kept for history)
│   ├── services/                 ← APPLICATION: use-cases
│   │   ├── generation.py         ← job lifecycle + EXACT-COUNT GATE (R9–R11)
│   │   │                           create_job / claim_job / execute_job
│   │   │                           (top-up requeue; partial_count; keep images)
│   │   ├── workers.py            ← worker loop + concurrency limiter
│   │   ├── pool.py               ← review pool queries
│   │   └── publish.py            ← publish one/album, spoilers (R22–R24)
│   ├── bot/                      ← DELIVERY: Telegram
│   │   ├── app.py                ← PTB wiring, PTBGateway, post_init recovery
│   │   │                           notify (R13), on_error
│   │   ├── commands.py           ← BotCommands: all handlers + PanelEngine
│   │   │                           (_panel/_panel_close R2–R4), dialogs, viewer
│   │   │                           (R5), settings UI (R25), retry (R12)
│   │   ├── gateway.py            ← BotGateway port (send/edit/edit_media/
│   │   │                           edit_caption/delete/reply-keyboard/hide)
│   │   ├── memory.py             ← DialogMemory (user_memory) + PanelMemory
│   │   ├── dialogs.py            ← dialog state types
│   │   ├── messages.py           ← message types
│   │   ├── posting.py            ← post/album flows
│   │   ├── worker.py             ← worker callbacks
│   │   ├── recovery.py           ← startup recovery + interrupted_ids (R13)
│   │   ├── auth.py               ← admin gating (not_authorized, R27)
│   │   └── __main__.py           ← python -m waifubot.bot
│   └── web/                      ← (reserved) future FastAPI admin panel
├── tests/
│   ├── conftest.py, fakes.py     ← FakeGateway/FakeEnhancer = port fakes
│   ├── goldens/prompt_seed42.json
│   ├── test_bot_flow.py          ← end-to-end bot harness (Env) + flow contracts
│   ├── test_cli.py
│   └── unit/                     ← one module per core/service unit
│       ├── test_generation_service.py  ← exact-count contracts (R9–R11)
│       ├── test_perchance_adapter.py   ← stale-gallery exclusion (R10)
│       ├── test_prompt_builder.py, test_identity.py, test_content_safety.py
│       ├── test_image_pipeline.py, test_admin_settings.py, test_locale.py
│       └── … (config, models, mock provider, tags pool, alembic, bot units)
└── tools/spike/                  ← isolated captcha/anti-bot spikes (never prod)
    ├── spike_run.py, spike_env_check.py, spike_config.example.yaml, README.md
```

### 3.3 Data flow (generation path)

```
bottom-keyboard press (NEW panel) / inline (EDIT panel)
  → wizard (style → character/prompt → enhance? → flags → count → NSFW → confirm)
  → services.generation.create_job   (request: prompt+style, flags, count N, kind, job_type)
  → workers claim → provider.generate(needed = N - already)      [R9]
  → pipeline (validate, hash, persist, review)  → images rows
  → exact-count gate: total >= N ? succeeded : partial_count + top-up requeue
  → notify (R12) → review (viewer edits-in-place R5) → pool / publish / album
```

### 3.4 Evolution path (reserved, planned)

| Future feature | Reserved home | Notes |
|---|---|---|
| Web admin panel | `src/waifubot/web/` | FastAPI + Jinja2/HTMX; auth = same Admin table |
| New providers (Zovla/pixai) | `core/providers/site/` + `config/providers/*.yaml` | port unchanged; no-captcha ones ship first |
| OnlyWaifus live | spike only until written permission | docs/provider-permission.md |
| Paid NSFW channel button | `bot/commands.py` publish flow | marked **بعداً** in UI |
| AI captions for albums | `core/ai_enhance.py` (caption mode) | typed captions until then |
| S3 object store | `core/images.py` ImageStore port | local FS now |
| Rate limiting / observability | `services/` + `logging_setup.py` | README known-issues |

---

## 4. Contracts

### 4.1 Job lifecycle

`pending → running → succeeded | retry_wait | failed` (plus `partial_count` **error code** on short batches, R9). Success requires `images ≥ count` exactly at the gate. Cancel/delete allowed while not running.

### 4.2 JSON shapes

- `Character.details_json`: `{traits{…}, extras, mode: known|manual|precise, name, known_name, known_anime, edit_of}`
- View state (pool/inbox): `{page, total, img_id, visible: [c,m,s,d,sp], caption}`
- Panel memory (`settings.panel:<admin_id>`): JSON `[chat_id, message_id]`
- Scheduler state (settings): `{channel_id, topic_id, post_count, random_order, min_gap_minutes, max_gap_minutes, next_at, running}`
- AI enhance request: `{details_json, scene_prompt, name, known_name, known_anime}`

### 4.3 Environment (defaults & secrets only — R25)

`TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `AI_ENHANCE_MODEL`, `PROVIDER`, `PROVIDER_CONFIG_PATH`, `POOL_DIR`, `LOG_LEVEL`, `RETRY_MAX_ATTEMPTS`, `RETRY_BACKOFF_SECONDS`, `SCHEDULER_TICK_SECONDS`, `MAX_IMAGE_MB`, `ALLOWED_MIME_TYPES`, `CHALLENGE_SLEEP_SECONDS`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH`, `POOL_CAP_ITEMS`, `POOL_CAP_BYTES`. Runtime knobs (worker count, concurrency, challenges, retries, sizes, scheduler) live in the settings panel.

### 4.4 Locale policy

All user-facing text goes through `bot/messages.py` (`t(key, **fmt)`). `REQUIRED_KEYS` are the minimum both `fa.yaml` and `en.yaml` must define; tests enforce. Adding a key = update both files.

---

## 5. Testing & deployment

### 5.1 Test commands (WSL2)

```bash
cd ~/waifubot && source .venv/bin/activate
python -m pytest tests/ -q            # full suite (161 green at v4.0)
python -m ruff check src/ tests/ alembic/   # lint gate
```

Rule: every new feature lands with tests; no live provider runs (R32); fakes are the ports' doubles; golden files lock prompt determinism.

### 5.2 Deployment (WSL2)

venv `~/waifubot/.venv`; `make install`; `make migrate` (absolute `script_location` in alembic.ini); `make run-bot` / `make worker`; `make reset-db` resets after confirmation. Secrets live in `.env` only (never committed; remote history is scrubbed with `[REDACTED]` if needed). systemd unit planned for auto-restart (`Restart=always`). RAM/WSL settings are measured before tuning (`.wslconfig` task, open).

### 5.3 Known deferred items

`.wslconfig` tuning after RAM measurement; live-run announcement protocol before any `--confirm-live`; paid NSFW button; AI captions; web panel. These are open asks — nothing else is pending.

---

## 6. Change log

| Version | Highlights |
|---|---|
| **v4.0** (2026-10-17) | Multi-admin model; bottom-keyboard = new panel / in-menu = same panel (R2–R3); back/close everywhere (R4); viewer edits-in-place (R5); fast typing (R6); **exact image counts 2/4/6/8/16 with undercount gate + auto top-up + partial_count** (R9–R11); stale-gallery exclusion (R10); character = appearance facts + scene at generation (R14–R15); known characters (R16); save-button policy (R17); edit = new version (R18); AI enhance at both confirms + env-only model (R20–R21); settings fully editable with safety ceilings (R25); adult restriction removed / minor protection kept (R26); interruption notices (R13); toggle settings buttons; panel close; full architecture documented (§3). |
| v3.1 | Original MVP spec (see `docs/SPEC-v3.1-archive.md`). |
