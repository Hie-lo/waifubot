# WaifuBot — Architecture (v4.1)

Behavior lives in `SPEC.md`; this file explains **how the code is organized and why**. The tree below is the **target layout**; `docs/MIGRATION-v4.0-to-v4.1.md` describes how to get there one stage at a time.

---

## 1. Principles

1. **Layered, inward dependencies.** `bot` (delivery) → `services` (application) → `core` (domain). Infrastructure lives in `adapters/` and `db/` and implements ports declared in `core/ports.py`. `core` imports only the standard library, pydantic and light pure libraries (e.g. Pillow for validation). **It never imports** Telegram, SQLAlchemy, Playwright, httpx or the Anthropic SDK. This is enforced in CI by `import-linter` contracts (ADR-0001).
2. **Ports & adapters.** Ports (`core/ports.py`): `GenerationProvider`, `ProviderSlotPool`, `Enhancer`, `ImageStore`, `Clock`; delivery port `BotGateway` (`bot/gateway.py`). Tests plug fakes for each. A new provider = a new adapter package + `config/providers/*.yaml`, zero core changes. A **shared contract-test suite** runs against every provider adapter (mock and real, the latter against recorded fixtures).
3. **Async structured concurrency.** asyncio + async SQLAlchemy + async Playwright. Tasks live in a `TaskGroup`; nothing is fire-and-forget. One session per unit of work (`session_scope`). Long provider runs never block the bot loop.
4. **Config split.** `env` = defaults + operational secrets (`SecretStr`). DB `settings` = runtime knobs, read through an in-process cache that is invalidated on write (R25). Code ceilings bound both.
5. **Durability.** Every flow resumes: jobs, images, `user_memory` (versioned wizard state), settings, schedules. Recovery is startup recovery **plus** a runtime lease reaper (R36).
6. **One writer rule per resource.** Browser slots are owned by one job at a time (R35); panels are edited by one update at a time per admin (R40); the DB is touched through short transactions only.
7. **Provenance and audit by default.** Images record how they were made (R43); settings/admin changes are audited (R44).
8. **One authorization point.** Role, block status, plan and limits are resolved once per update into a `RequestContext` (`bot/auth.py` → `core/limits.py`); handlers and services never re-derive them, and the superadmin's "no limits" (R53) lives in exactly one function.
9. **Tests mirror the architecture** (§9).

---

## 2. Project tree (target — v4.1)

```
waifubot/
├── SPEC.md  ARCHITECTURE.md  AGENT.md  CHANGELOG.md
├── README.md  Makefile  pyproject.toml  uv.lock  .python-version
├── .pre-commit-config.yaml  .env.example  alembic.ini
├── .github/workflows/ci.yml          ← ruff, pyright, import-linter, pytest
├── alembic/versions/                 ← 0001…0004 existing; 0005… see §6
├── config/
│   ├── blocklist.yaml  identity_pool.yaml  tags_pool.yaml
│   └── providers/  mock.yaml  perchance.yaml     (incl. provider_concurrency)
├── docs/
│   ├── adr/  0001…0005
│   ├── MIGRATION-v4.0-to-v4.1.md
│   ├── SPEC-v3.1-archive.md  schema.md  progress.md  spike-report.md
│   └── provider-interface.md  provider-permission.md
├── locales/  fa.yaml  en.yaml
├── scripts/  upsert_admin.py
├── src/waifubot/
│   ├── config.py  logging_setup.py  cli.py        ← cli adds: health
│   ├── core/                  ← DOMAIN (pure)
│   │   ├── models.py          ← GenerationRequest, ImageAsset, ProviderJob, SlotInfo…
│   │   ├── ports.py           ← GenerationProvider, ProviderSlotPool, Enhancer, ImageStore, Clock,
│   │   │                         ChatModel, ChannelProbe
│   │   ├── prompts.py  safety.py  identity.py
│   │   ├── limits.py          ← Limits + effective_limits(role, plan, overrides) (R52–R53)
│   │   ├── persona.py         ← persona fields, card compile + hash (R62)
│   │   ├── chat_memory.py     ← window/summary/facts, token budgets, JSON contract (R64)
│   │   ├── images.py          ← pure validation: MIME/EXIF strip, sha256, pHash, size caps
│   │   └── admin_settings.py  ← typed settings registry + ceilings
│   ├── adapters/              ← INFRASTRUCTURE implementing ports
│   │   ├── providers/
│   │   │   ├── mock.py
│   │   │   └── perchance/     adapter.py  gallery.py  controls.py  api.py
│   │   │                      challenge/ (types, errors, runner, conversation)
│   │   ├── browser/           pool.py (one Chromium, N contexts, storage-state reuse)
│   │   ├── enhance/           anthropic.py
│   │   ├── chat/              anthropic.py (ChatModel + compaction call, prompt caching)
│   │   ├── storage/           local_fs.py (content-addressed, atomic)
│   │   └── http.py            ← shared async HTTP (standard browser headers)
│   ├── db/                    base.py  models.py  session.py (WAL pragmas, migration check)
│   ├── services/              ← APPLICATION
│   │   ├── generation.py      ← create_job, execute_job, EXACT-COUNT GATE (R9–R11, R38)
│   │   ├── claiming.py        ← atomic claim, fair round-robin, lease+heartbeat (R34, R36)
│   │   ├── workers.py         ← WorkerPool: N worker tasks, live resize, graceful stop (R34, R37)
│   │   ├── reaper.py          ← expired-lease requeue + notice (R36, R13)
│   │   ├── resources.py       ← MemoryGuard, ChallengeGate (R35, R28)
│   │   ├── pool.py  publish.py  scheduler.py  audit.py  status.py
│   │   ├── admins.py          ← add/remove/promote, invites, last-superadmin invariant (R48)
│   │   ├── users.py           ← user registry, block/unblock, search, stats (R50)
│   │   ├── system.py          ← RestartController: workers restart, process restart, marker (R49)
│   │   ├── plans.py  quota.py ← plans CRUD, check_and_reserve / settle / refund (R52, R54)
│   │   ├── channels.py        ← verify_channel, assert_usable, ChannelHealth loop (R56–R59)
│   │   ├── ownership.py       ← claim (atomic), release, catalog, random (R60–R61)
│   │   ├── personas.py        ← persona edit, AI Enhance preview, undo (R62)
│   │   └── chat/              engine.py  memory.py  compaction.py  prompt.py (R63–R66)
│   └── bot/                   ← DELIVERY: Telegram
│       ├── app.py             ← wiring, PTBGateway + rate limiter, post_init recovery, on_error
│       ├── gateway.py  memory.py  dialogs.py  messages.py
│       ├── panel.py           ← PanelEngine (R2–R4, R40): per-admin lock, versioned panels
│       ├── callbacks.py       ← typed CallbackData registry (R8, R41)
│       ├── auth.py            ← role gating + blocked-user middleware (R27, R50)
│       ├── recovery.py  telegram_html.py  topics.py
│       ├── handlers/          generate.py  review.py (pool/inbox viewer)  characters.py
│       │                      settings.py  publish.py  scheduler.py  admins.py  users.py
│       │                      system.py (restart)  status.py
│       │                      me.py (My account, My images)  plans.py  channels.py
│       │                      claim.py (catalog, random, deep link)  persona.py  chat.py
│       └── __main__.py
├── src/waifubot/web/          ← (reserved) FastAPI + Jinja2/HTMX admin panel
├── tests/                     ← see §9
└── tools/spike/               ← isolated spikes, never prod
```

Removed from v4.0: `db/worker.py` (dead code), `core/providers/`, `core/http.py`, `core/ai_enhance.py` (moved to `adapters/`), `bot/commands.py` (split into `panel.py` + `handlers/`), `bot/worker.py` (logic moves to `services/workers.py`; the bot only supplies notify callbacks).

---

## 3. Layering rules (enforced)

`import-linter` contracts in `pyproject.toml`:

- `core` must not import `waifubot.adapters`, `waifubot.db`, `waifubot.services`, `waifubot.bot`, nor `telegram`, `sqlalchemy`, `playwright`, `httpx`, `anthropic`.
- `services` must not import `waifubot.bot` or `telegram`.
- `adapters` and `db` must not import `waifubot.bot` or `waifubot.services`.
- `bot` may import `services` and `core`, never `adapters` directly (wiring is done in `app.py` via a small composition root).

---

## 4. Concurrency model (R34–R38)

```
                    ┌──────────────── settings cache (live resize) ───────────────┐
 Telegram admins ─► bot handlers ─► services.generation.create_job ─► DB (jobs)
                                                                         │
        WorkerPool (TaskGroup)                                           │ atomic claim
   ┌────────┬────────┬────────┬────────┐                                 ▼
   │worker 1│worker 2│worker 3│worker N│ ──► claiming.claim_next(fair, lease)
   └───┬────┴───┬────┴───┬────┴───┬────┘
       │        │        │        │      effective = min(worker_count,
       ▼        ▼        ▼        ▼                       max_concurrent_jobs,
   job semaphore (max_concurrent_jobs)                    max_browser_contexts,
       │                                                  provider_concurrency)
       ▼
   ProviderSlotPool.acquire()  ── MemoryGuard (soft) ──► BrowserPool
       │                                                  one Chromium
       ▼                                                  slot = BrowserContext + Page
   provider.generate(needed, slot)   ── challenge? ──► ChallengeGate (1 window at a time)
       │
       ▼
   pipeline (validate · hash · pHash · persist) ─► exact-count gate ─► succeed | top-up requeue | fail
       │  heartbeat every heartbeat_seconds renews the lease while running
       ▼
   reaper loop: lease expired → requeue (+ announce)        SIGTERM → drain → release leases
```

Key points:

- **A worker runs one job at a time**; concurrency = several workers (ADR-0003). Workers are cheap asyncio tasks; effective parallelism is the `min(...)` of the four limits, so raising `worker_count` alone never overloads the machine.
- **Slots, not shared pages.** Each job gets an isolated `BrowserContext`. Warm contexts keep `storage_state` (cookies/localStorage) so sessions are reused across jobs without ever being used by two jobs at once. Mock provider uses trivial slots.
- **Challenges** are rare and human-involved; the `ChallengeGate` guarantees one challenge window at a time (R28). Waiting jobs keep their slot up to `challenge_wait_seconds`, then go to `retry_wait`.
- **Claim** (SQLite, `BEGIN IMMEDIATE`): select the next eligible job (`pending`, or `retry_wait` with `next_attempt_at <= now`), ordered oldest-first with round-robin across admins when `fair_claim` is on, and `UPDATE … RETURNING` with the lease in the same transaction. Portable to PostgreSQL via `FOR UPDATE SKIP LOCKED`.
- **Live resize.** The pool re-reads limits from the settings cache every loop; growth spawns workers, shrinkage lets surplus workers exit after their current job.
- **Shutdown.** `stop_event` → stop claiming → wait ≤ `shutdown_grace_seconds` → release leases (`running → pending`, attempt not consumed) → close slots/browser.
- **Restart (R49)** reuses this path. *Workers restart* = shutdown steps + rebuild `WorkerPool`/slot pools inside the running process. *Bot restart* = shutdown steps + write the `restart.pending` marker + exit code `75`; the supervisor starts a new process, which edits the marked panel and runs recovery.
- **Top-up** reuses the same job row (`needed = N − kept`), idempotent via content hash, bounded by `topup_rounds` and `zero_progress_limit` (R38).

---

## 5. Data flow (generation path)

```
bottom-keyboard press (NEW panel) / inline (EDIT panel, per-admin lock)
  → wizard (style → character/prompt → enhance? → flags → count → NSFW → confirm)
       enhance → Enhancer adapter → safety re-check (R39) → prompt
  → services.generation.create_job (prompt+style, flags, count N, kind, job_type)
  → WorkerPool claims (atomic, leased) → ProviderSlotPool.acquire()
  → provider.generate(needed = N − kept, slot)                       [R9, R10, R35]
  → pipeline: validate · sha256 · pHash · near-dup check · EXIF strip · CAS write
  → exact-count gate: kept == N ? succeeded : partial_count + top-up (R38)
  → provenance rows (R43) · notify (R12) · review (viewer edits in place, R5)
  → pool / publish (rate-limited, file_id cached, R42) / album / schedule (R45)
```

---

## 5A. Users, plans & quotas (R51–R55)

```
Telegram update
  → bot/auth.py middleware: resolve role (superadmin|admin|user), blocked?, plan (+expiry, overrides)
        → RequestContext{ user, role, limits }          ← the ONLY place limits are derived
  → handler → services.quota.check_and_reserve(ctx, kind, amount) → service action
```

- `core/limits.py` (pure): `Limits` dataclass (`None` = unlimited) and `effective_limits(role, plan, overrides)`. For the superadmin it returns `Limits.unlimited()` — **the single short-circuit of R53**; no other module tests the role for limit purposes. Admins resolve to the `staff` plan; users to their assigned plan (or `default_plan_id` after expiry), overrides applied last.
- `services/quota.py`: `check_and_reserve` / `settle` / `refund` run in one DB transaction with the action they guard. Day keys use `app_timezone`.
- **Generation lifecycle:** `create_job` reserves N images → job runs → at the exact-count gate (R9) the reservation is settled to `kept` and `N − kept` is refunded; `failed`/`cancelled` refunds everything; a lease-expired requeue keeps the reservation (same job).
- **User jobs** carry `requester_user_id` and `job_scope='user'`; images get `owner_user_id` and live in *My images* (R51), outside review and the pool. `claiming.py` orders by plan priority, then round-robin across requesters (R55).
- Per-user rate limiting of button spam is handled by R40 (per-admin lock generalized to per-user); it is not a quota.

## 5B. Channels (R56–R59)

- **Port:** `core/ports.py: ChannelProbe` — `get_chat(ref)`, `get_chat_member(chat, user)`, `get_bot_rights(chat)`. The Telegram implementation lives in the gateway adapter; services never import Telegram.
- **`services/channels.py`:** `verify_channel(adder_ctx, chat_ref) → VerifyResult` with a typed failure enum (`not_found`, `bot_not_admin`, `bot_missing_rights{which}`, `adder_not_member`, `adder_not_admin`, `plan_no_capacity`) so the UI can show the exact fix. Order: existence → bot rights → adder check (R57; the superadmin skips only the adder check) → plan capacity (R54, transactional with the insert).
- **Health:** a `ChannelHealth` loop in the `TaskGroup` (every `channel_recheck_hours`) plus a `my_chat_member` handler. State machine: `active ⇄ disabled`, `active → lost_access` (schedules paused) `→ active` (after re-verify + confirmation), `legacy_unverified → active` on first successful verify.
- **Use-time check (R59):** `publish.py` and `scheduler.py` call `channels.assert_usable(ctx, channel_id)` which checks scope, status and — for non-owners — repeats the R57 membership check.
- Schedules reference `channels.id` (FK); the `0012` migration creates `legacy_unverified` rows for ids found in old scheduler state.

## 5C. Character ownership & chat (R60–R66)

```
DM text in chat mode ─► handlers/chat.py ─► debounce/merge ─► services/chat/engine.py
    1. quota.check_and_reserve(chat_message)                         (R54)
    2. safety.check(user text)                                        (R66)
    3. memory.load(session)         → persona card · facts · summary · window
    4. prompt.build(...)            → stable prefix (cacheable) + dynamic tail
    5. ChatModel.complete(...)      → semaphore(chat_max_concurrency), timeout, reply cap
    6. safety.check(reply)          → refusal fallback
    7. send reply · memory.append_turn · usage record
    8. if compaction due: background task (per-session lock) → compaction.run()
```

- **Pure core:** `core/persona.py` (fields, validation, deterministic card compile + hash), `core/chat_memory.py` (window/summary/facts structures, token budgeting, JSON-contract parse and repair), `core/limits.py`. Golden test for card compilation; property tests for budget caps.
- **Adapter:** `adapters/chat/anthropic.py` implements `ChatModel` (and the compaction call), marks the stable prefix for **prompt caching** (consolidating base system instructions, safety guardrails, persona card, facts, and few-shot dialogue to deterministically exceed the Anthropic $\ge 1,024$-token threshold), applies timeouts and returns token usage.
- **Compaction:** dual-condition trigger (every `chat_compact_every_turns` OR whenever window tokens reach budget); input = old summary + facts + leaving turns, output = strict JSON `{summary, facts_add, facts_remove}`. Runs **after** the reply is sent; per-session lock prevents double compaction; on parse failure it retries once with regex block extraction (`r'\{[\s\S]*\}'`), then trims to the hard cap and logs. Token estimation uses a calibrated Persian BPE upper-bound ($\lceil \text{chars} \times 1.4 \rceil$ or $\lceil \text{bytes} \times 0.70 \rceil$) with Unicode NFC normalization (`unicodedata.normalize`).
- **Persona edit:** `services/personas.py` handles field edits, **AI Enhance** (the existing `Enhancer` port in a `persona` mode, R62) with preview/confirm, recompile, and one-step undo (`prev_json`).
- **Ownership:** `services/ownership.py` — `claim` is a single `UPDATE … WHERE status='listed' AND owner IS NULL` plus the `max_owned_characters` check in the same transaction; `release` deletes persona, sessions and memory; `catalog` and `random` queries exclude non-`listed` rows. `claim_posts` lets the bot edit the channel post to "claimed ✅" best-effort.
- **Mode precedence** (R63): per-user `user_memory` holds `mode: idle|wizard|chat`; the update router checks it before treating typed text as a generation prompt (R6).
- **Privacy:** admin screens read only counters and token totals; nothing in logs or audit contains message text.

## 6. Data model changes (Alembic)

Existing: `0001_initial_schema`, `0002_image_is_nsfw`, `0003_post_caption`, `0004_character_details`.

| Migration | Changes | Rule |
|---|---|---|
| `0005_job_lease` | `jobs`: `lease_owner`, `lease_expires_at`, `heartbeat_at`, `next_attempt_at`, `needed`, `kept_count`, `rounds_done`, `zero_progress_streak`, `run_no`; index on `(status, next_attempt_at, created_at)` | R34, R36, R38 |
| `0006_image_provenance` | `images`: `final_prompt`, `negative_prompt`, `seed`, `provider`, `provider_cfg_ver`, `character_version_id`, `enhanced`, `sha256`, `phash`, `tg_file_id` | R43, R47, R42 |
| `0007_character_versions` | `characters`: `character_id`, `version`, `is_current`; backfill by walking existing `edit_of` chains | R18 |
| `0008_schedules` | new `schedules` table; data-migrate the old scheduler JSON from `settings` | R45 |
| `0009_audit_and_admin` | new `audit_log`; `admins.removed_at` | R44 |
| `0010_users_and_invites` | new `users` (status, locale, note, timestamps); new `admin_invites` (hashed token, role, expiry, usage) | R48, R50 |
| `0011_plans_and_usage` | new `plans` (+ seed `free`, `staff`), `usage_counters`; `users.plan_id/plan_expires_at/limit_overrides_json`; `jobs.requester_user_id/job_scope/reserved_images`; `images.owner_user_id` | R51–R55 |
| `0012_channels` | new `channels`, `channel_topics`; `schedules.channel_id` → FK; migrate old ids as `legacy_unverified` | R56–R59 |
| `0013_character_ownership` | new `character_ownership` (backfill existing characters as `draft`), `claim_posts` | R60–R61 |
| `0014_personas` | new `character_personas` | R62 |
| `0015_chat` | new `chat_sessions`, `chat_memory` | R63–R64 |

SQLite settings (applied on connect): `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`, `wal_autocheckpoint=1000`. To eliminate lock contention (`SQLITE_BUSY`): (1) zero I/O or network calls in open transactions; (2) volatile UI/debounce state maintained in-memory; (3) $\pm 20\%$ randomized heartbeat jitter + lease headroom skip; (4) write retry wrapper with exponential backoff (up to 5 attempts); (5) periodic background passive checkpoints (`wal_checkpoint(PASSIVE)`); (6) turn-end counter batching. Alembic runs in **batch mode** for SQLite `ALTER`. Use only portable SQLAlchemy types so a PostgreSQL move (ADR-0005) is a configuration change, not a rewrite.

---

## 7. Tooling & quality

- **uv** for environments and `uv.lock`; Python pinned in `.python-version` (3.12 or 3.13). `requirements.txt` is generated, not hand-edited.
- **ruff** (lint **and** format), **pyright** strict on `core/` and `services/` (basic elsewhere), **import-linter** (§3), **pre-commit** (ruff, pyright, `gitleaks`).
- **CI** (GitHub Actions): lint → types → import-linter → pytest. No live provider runs (R32).
- Python style: `Protocol` ports, `StrEnum`, pydantic v2 models at boundaries, `asyncio.TaskGroup`, `async with` for every browser/slot lifecycle, `SecretStr` for secrets.
- Secrets live only in `.env`. If a secret ever leaks: **rotate first**; scrubbing history is optional.

---

## 8. Deployment (WSL2) & operations

`make install` (uv sync) · `make migrate` · `make run-bot` / `make worker` · `make reset-db` (after confirmation). systemd unit with `Restart=always`, `RestartForceExitStatus=75` (bot restart from Settings, R49) and `KillSignal=SIGTERM` + `TimeoutStopSec` ≥ `shutdown_grace_seconds` + margin (R37). Enable systemd in `/etc/wsl.conf`. **[VERIFY]** WSL2 may stop the VM when idle; confirm that the service keeps the instance alive, otherwise keep a Windows-side keep-alive. `.wslconfig` memory limits are tuned **after measuring** RAM per slot; the result feeds the starting defaults of `max_browser_contexts`. Health: `waifubot health`, `/status`, JSON logs with `job_id` correlation (R46).

---

## 9. Testing

```bash
cd ~/waifubot && source .venv/bin/activate
python -m pytest tests/ -q
python -m ruff check src/ tests/ alembic/ && python -m ruff format --check src/ tests/ alembic/
python -m pyright
lint-imports
```

- **Fakes** for every port (`tests/fakes.py`): `FakeGateway`, `FakeEnhancer`, `FakeSlotPool`, `FakeClock`.
- **Contract tests** per service; one shared provider contract suite; golden file for prompt determinism.
- **Property tests (`hypothesis`)** for the exact-count gate and pipeline accounting: for any batch composition, `kept + dropped == produced` and the gate outcome is `kept == N`.
- **Concurrency tests:** N workers over a fake slot pool — no job claimed twice, effective concurrency never exceeds the `min(...)`, lease expiry requeues, graceful shutdown releases without consuming attempts, live resize up and down.
- **Registry tests:** every `CallbackData` routed and ≤ 64 bytes (R8); locale parity and placeholder parity (§3.5 of SPEC).
- **Limits/quota tests:** `effective_limits` matrix over every role × plan × override; property tests for reserve/settle/refund (never negative, `settled + refunded == reserved`); superadmin short-circuit.
- **Channel tests:** a `FakeChannelProbe` drives every failure branch of `verify_channel` (including adder-not-admin and the superadmin exemption), health transitions and use-time re-checks.
- **Claim tests:** N concurrent claims → exactly one winner; limit check is atomic with the claim; release wipes persona and memory.
- **Chat tests:** compaction keeps summary + facts within caps (property tests), JSON-contract parse/repair fallbacks, persona-card golden file, mode precedence over R6, no message text in logs.
- **Recorded fixtures** (HTML/JSON) for Perchance selectors; a manual `--confirm-live` smoke test stays separate (R32).

---

## 10. Evolution path (reserved)

| Future feature | Home | Notes |
|---|---|---|
| Web admin panel | `src/waifubot/web/` | FastAPI + Jinja2/HTMX; auth = same Admin table; could later become a Telegram Mini App |
| New providers (Zovla/pixai) | `adapters/providers/` + `config/providers/*.yaml` | port unchanged; no-challenge providers ship first |
| OnlyWaifus live | spike only | needs written permission in `docs/provider-permission.md` |
| Paid NSFW channel button | `bot/handlers/publish.py` | marked **بعداً** |
| AI captions for albums | `adapters/enhance/` (caption mode) | typed captions until then |
| S3 object store | `ImageStore` port | local CAS now |
| PostgreSQL | `db/` + ADR-0005 | `SKIP LOCKED` claim |
| Metrics (Prometheus) | `services/status.py` | after logs/status prove insufficient |
| Plan payments / billing | `services/plans.py` | plans are admin-assigned until then (marked **بعداً**) |
| Ownership transfer | `services/ownership.py` | needs a consent flow on both sides |
| Group chat with characters | `handlers/chat.py` | needs per-group memory and moderation rules |
| Streaming chat replies | `adapters/chat/` | edit-rate limits of Telegram must be handled (R42) |
