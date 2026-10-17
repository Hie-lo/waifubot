# Migration plan: v4.0 → v4.1

Rules of engagement (AGENT.md, R30): **one stage at a time**; each stage ends with a short plain-Persian report, a real-test command, and a **STOP** until approval. Each stage keeps the full test suite green. Test file names below are the names to create or extend.

---

## Stage 1 — Contract fixes & baseline hygiene

Goal: remove contradictions and dead code before moving anything.

- [x] R9 gate: compare `kept == N`; truncate surplus (`surplus_dropped`); update `services/generation.py`
- [x] R38: top-up budget, zero-progress streak, fresh run on manual retry (counters stored on the job)
- [x] R39: enhancer output → `safety` re-check; timeout; soft-fail with explanation
- [x] R26/R27 wording applied in `safety.py` docstrings, `auth.py`, locale keys (`not_authorized`)
- [x] Delete `db/worker.py`; remove unused locale keys
- [x] Docs: adopt the new SPEC / ARCHITECTURE / AGENT / CHANGELOG split
- [x] Tooling baseline: `uv`, `ruff format`, `pre-commit` (+ `gitleaks`), CI skeleton, `SecretStr` for secrets

Real test: `python -m pytest tests/unit/test_generation_service.py tests/unit/test_content_safety.py tests/unit/test_locale.py -q`

## Stage 2 — Layer cleanup

- [x] Create `core/ports.py`; move provider/enhancer/storage interfaces there
- [x] Move `core/providers/**`, `core/http.py`, `core/ai_enhance.py` → `adapters/`; rename `stealth` header naming to "standard browser headers" in code and docs
- [x] Move `telegram_html.py`, `topics.py` → `bot/`
- [x] Add `import-linter` contracts (ARCHITECTURE §3) and pyright (strict on `core`, `services`)
- [x] Composition root in `bot/app.py`

Real test: `lint-imports && python -m pyright && python -m pytest tests/ -q`

## Stage 3 — Durable claiming (lease, heartbeat, reaper, shutdown)

- [x] Alembic `0005_job_lease`
- [x] SQLite pragmas (WAL, busy_timeout, synchronous, foreign_keys) in `db/session.py`
- [x] `services/claiming.py`: atomic claim in `BEGIN IMMEDIATE`; fair round-robin
- [x] Heartbeat task; `services/reaper.py`; runtime `interrupted_notice` (R13)
- [x] Graceful shutdown (R37): stop claiming → grace wait → release leases without consuming attempts

Real test: `python -m pytest tests/unit/test_claiming.py tests/unit/test_reaper.py tests/unit/test_shutdown.py -q`

## Stage 4 — Concurrent execution (R34, R35, R28)

- [x] `core/ports.py`: `ProviderSlotPool` (+ fake and mock implementation)
- [x] `adapters/browser/pool.py`: one Chromium, N isolated contexts, storage-state reuse; Perchance adapter takes a slot instead of owning a page
- [x] `services/workers.py`: `WorkerPool` (TaskGroup), job semaphore, live resize from settings cache
- [x] `services/resources.py`: `MemoryGuard` (soft), `ChallengeGate` (one window at a time, `challenge_wait_seconds`)
- [x] Settings registry: `worker_count`, `max_concurrent_jobs`, `max_browser_contexts`, `provider_concurrency`, `fair_claim`, `min_free_ram_mb`, `challenge_wait_seconds` + panel buttons
- [x] R10 (per-job slot + snapshot + hash dedupe) verified under concurrency

Real test: `python -m pytest tests/unit/test_worker_pool.py tests/unit/test_slot_pool.py tests/unit/test_perchance_adapter.py -q`
Manual measurement (no live provider in CI): run the mock provider with 1/2/3/4 slots and record RAM per slot; write the numbers into the starting defaults.

## Stage 5 — Bot refactor

- [x] Split `bot/commands.py` into `panel.py` + `handlers/*`
- [x] `bot/callbacks.py`: typed `CallbackData` registry (R41) and registry test (R8)
- [x] Per-admin lock, instant `answer_callback_query`, panel versions, edit-failure fallback (R40); panel key by `(admin, chat)`
- [x] Rate limiter + flood-wait handling (R42); `file_id` cache
- [x] Wizard state: versioned pydantic state with TTL in `user_memory`
- [x] Special Character Studio wizard in `bot/handlers/generate.py`: click-only preset composition (R16B) mirroring Vision Lab `sync()` contract

Real test: `python -m pytest tests/test_bot_flow.py tests/unit/test_callbacks_registry.py tests/unit/test_panel_engine.py -q`

## Stage 6 — Data model: provenance, image store, character versions

- [x] Alembic `0006_image_provenance`, `0007_character_versions` (backfill from `edit_of`)
- [x] `adapters/storage/local_fs.py`: content-addressed, atomic, EXIF strip
- [x] pHash + `near_duplicate` reject reason; `phash_distance` setting
- [x] Record provenance (R43)
- [x] `identity.py`/UI: `character_id`, `version`, `is_current`
- [x] Vision Lab preset catalog integration: `config/vision_lab_presets.yaml` (114 characters + 11 option categories)

Real test: `python -m pytest tests/unit/test_image_pipeline.py tests/unit/test_identity.py tests/unit/test_alembic.py -q`

## Stage 7 — Scheduler, audit, admins & users

- [ ] Alembic `0008_schedules`, `0009_audit_and_admin`, `0010_users_and_invites`
- [ ] Multi-schedule scheduler; `app_timezone`; Jalali dates in the Persian UI; `missed_run_policy` (R45)
- [ ] `audit_log` for settings/admin changes; superadmin audit view (R44)
- [ ] `ADMIN_IDS` insert-if-missing; soft-disable on removal
- [ ] `services/admins.py` + `handlers/admins.py` + Settings → Admins (R48): user-picker button, typed ID, one-time invite link, role choice, promote/demote/remove, last-superadmin invariant, notices
- [ ] `services/users.py` + `handlers/users.py` + Settings → Users (R50): overview, paged list + filters, search, user card, block/unblock, note, language, delete record; blocked-user middleware in `bot/auth.py`
- [ ] Locale keys for all new screens in both `fa.yaml` and `en.yaml`

Real test: `python -m pytest tests/unit/test_scheduler.py tests/unit/test_audit.py tests/unit/test_admin_seed.py tests/unit/test_admin_management.py tests/unit/test_user_management.py -q`

## Stage 8 — Observability & operations

- [ ] Structured JSON logs with `job_id`/`admin_id`/`provider`/`slot` context (R46)
- [ ] `/status` (with uptime and last restart) and `waifubot health`
- [ ] `services/system.py` + `handlers/system.py` + Settings → System (R49): restart workers, restart bot (exit code 75, `restart.pending` marker, panel edited after startup, `os.execv` fallback)
- [ ] Hypothesis tests for the exact-count gate; recorded fixtures for Perchance
- [ ] systemd unit with `RestartForceExitStatus=75` + WSL2 idle check **[VERIFY]**; test a real restart from the panel

Real test: `python -m pytest tests/ tests/unit/test_restart_controller.py -q && python -m ruff check src/ tests/ alembic/ && python -m pyright && lint-imports`

---

## Stage 9 — Plans, quotas & user self-service (R51–R55)

- [ ] Alembic `0011_plans_and_usage` (seed built-in `free` and `staff` plans)
- [ ] `core/limits.py` (pure): `Limits`, `effective_limits` — the only superadmin short-circuit (R53)
- [ ] `services/plans.py`, `services/quota.py`: `check_and_reserve` / `settle` / `refund`, day keys in `app_timezone`; wire into `create_job` and the exact-count gate (R54)
- [ ] `bot/auth.py`: `RequestContext` (role, blocked, plan, limits); role-based reply keyboards; staff-only callbacks → `not_authorized`
- [ ] User generation flow: private results, *My images* viewer with delete, `max_stored_images`
- [ ] `claiming.py`: priority → round-robin across requesters → oldest (R55)
- [ ] Screens: *My account*; *Settings → Plans* (create/edit/delete, replacement on delete); plan assignment, expiry and per-user overrides on the user card
- [ ] Locale keys (fa + en) for all new screens and limit messages

Real test: `python -m pytest tests/unit/test_limits.py tests/unit/test_quota.py tests/unit/test_plans.py tests/unit/test_user_generation.py -q`

## Stage 10 — Channels (R56–R59)

- [ ] Alembic `0012_channels`; migrate old schedule channel ids as `legacy_unverified`; `schedules.channel_id` FK
- [ ] `ChannelProbe` port + gateway methods (`get_chat`, `get_chat_member`, bot rights, `request_chat` picker button)
- [ ] `services/channels.py`: `verify_channel` (existence → bot rights → adder check → plan capacity), `assert_usable`, scope handling
- [ ] `ChannelHealth` loop + `my_chat_member` handler: `lost_access`, schedule pause, notifications, re-verify + confirmed resume
- [ ] Screens: *Settings → Channels* and *My channels* (add via picker / forward / typed id, card, re-verify, alias, enable/disable, default, topics, scope, remove with schedule guard)
- [ ] Publish/scheduler use `assert_usable` and the R24 error path; users publish own images to own channels

Real test: `python -m pytest tests/unit/test_channel_verify.py tests/unit/test_channel_health.py tests/unit/test_channel_scope.py tests/unit/test_publish.py -q`

## Stage 11 — Character ownership & claiming (R60–R61)

- [ ] Alembic `0013_character_ownership` (backfill existing characters as `draft`) and `claim_posts`
- [ ] `services/ownership.py`: atomic claim with `max_owned_characters` in the same transaction; release; revoke/reassign
- [ ] Staff publish option "claimable" (deep-link button on channel posts) and `listed` marking; best-effort "claimed ✅" edit
- [ ] User screens: catalog (viewer edits in place), 🎲 random with confirmation, *My characters*, create-in-DM (plan-limited, generation charged to the owner)
- [ ] User card (R50): owned characters list, revoke/reassign; `claim_enabled` toggle

Real test: `python -m pytest tests/unit/test_ownership.py tests/unit/test_claim_race.py tests/test_bot_flow.py -q`

## Stage 12 — Persona & character chat (R62–R66) — last development stage

- [ ] Alembic `0014_personas`, `0015_chat`
- [ ] `core/persona.py` and `core/chat_memory.py` (pure): fields, card compile + hash, budgets, JSON contract + repair
- [ ] `ChatModel` port; `adapters/chat/anthropic.py` with prompt caching **[VERIFY minimum cacheable length and TTL]**, timeouts, usage reporting; env `AI_CHAT_MODEL`, `AI_CHAT_COMPACT_MODEL`
- [ ] `services/personas.py`: button-driven field editing, **AI Enhance** (persona mode) with preview/confirm, recompile, one-step undo, canon auto-fill for known characters
- [ ] `services/chat/*`: engine pipeline (R63), debounce merge, memory load/append, background compaction with per-session lock, usage counters, safety checks on input and output (R66)
- [ ] Chat mode in the update router (precedence over R6), chat panel (end, persona, memory view/delete fact/reset, switch character), `/endchat`
- [ ] Settings knobs (R65), `chat_enabled`, concurrency semaphore, cost-alert-only threshold
- [ ] Privacy: no message text in logs/audit; wipe on release, delete character, delete user
- [ ] **Final review pass** (AGENT.md checklist), SPEC ↔ ARCHITECTURE ↔ CHANGELOG consistency check, CHANGELOG finalization

Real test: `python -m pytest tests/unit/test_persona.py tests/unit/test_chat_memory.py tests/unit/test_chat_engine.py tests/unit/test_compaction.py tests/test_bot_flow.py -q && python -m ruff check src/ tests/ alembic/ && python -m pyright && lint-imports`
