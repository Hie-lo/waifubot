# Changelog

## v4.1 (2026-10-17)

**Concurrency & durability**
- Concurrent job engine: `WorkerPool` of single-job workers; effective concurrency = `min(worker_count, max_concurrent_jobs, max_browser_contexts, provider_concurrency)`; live resize; fair round-robin queue (R34).
- Isolated provider slots; one Chromium with one `BrowserContext` per job; warm storage-state reuse; soft `MemoryGuard` (R35).
- Atomic claim with lease + heartbeat + runtime reaper (R36); graceful shutdown that releases jobs without consuming attempts (R37).
- Top-up budget, zero-progress guard, idempotent top-up (R38); `ChallengeGate` serializes challenge windows (R28).

**Contract fixes**
- R9: gate is `kept == N`; surplus is truncated and logged (the old "≥" ambiguity is gone).
- R10: three-layer protection against stale/cross-job images (slot isolation + snapshot + hash dedupe).
- R26 follows the v4.0 changelog: **adult restriction removed, minor protection kept**; R27 = no silent dead ends (`not_authorized`). Previously the numbers in the text and the changelog disagreed.
- R8: callback audit is now an automated registry test instead of a hard-coded count.
- R13: interruption notices also fire from the runtime reaper.
- R18: character versions use `character_id` + `version` + `is_current` (keeps `edit_of`).
- R39: AI-enhance output is re-checked by the existing safety rules and fails soft with a timeout.

**Platform & UX**
- R40 panel concurrency safety (per-admin lock, versioned panels, edit fallback); R41 typed callbacks; R42 Telegram rate limiting + `file_id` cache.
- R43 provenance per image; R44 audit log and insert-only admin seeding; R45 multi-schedule scheduler with timezone, Jalali dates and missed-run policy; R46 structured logs, `/status`, `waifubot health`; R47 content-addressed storage, EXIF strip, perceptual-hash dedupe.

**Administration**
- R48: admins are added/removed in *Settings → Admins* (user-picker button, typed ID, or one-time invite link), with role choice, promote/demote and a last-superadmin invariant.
- R49: *Settings → System* restarts the workers or the whole bot (graceful, exit code 75 under systemd, `os.execv` fallback, panel updated after startup).
- R50: user management for admins — registry of everyone who opens the bot, overview, search, block/unblock, notes, per-user language, delete record.
- Migration `0010_users_and_invites`.

**Users, plans & channels**
- R51: users get self-service (generate with private results, own characters, chat, account) within plan limits; role-based keyboards and a permission matrix.
- R52–R55: admin-managed plans (`free`, `staff` built-in), per-user overrides and expiry, atomic reserve/settle/refund quotas (users pay only for delivered images), priority + per-requester fair queue. **Superadmin has no limits** (single short-circuit in `effective_limits`).
- R56–R59: channel registry with verification (bot rights + **permission check of the person adding**, superadmin exempt from the ownership check), management, health monitoring with schedule pausing, shared/private scope, use-time re-checks, user-owned channels (plan-gated).
- Migrations `0011_plans_and_usage`, `0012_channels`.

**Character ownership & chat (final development stage)**
- R60–R61: ownership on stable `character_id`, atomic first-come claim (channel button, catalog, random), plan cap on owned characters, release/revoke.
- R62: separate persona (traits, editable, **AI Enhance** with preview, undo); R14 unchanged.
- R63–R66: DM chat mode, ultra-compact memory (persona card + summary + facts + short window, compaction in one cheap call), cost controls (env-only models, prompt caching, reply cap, concurrency, alert-only threshold), safety and privacy rules.
- Migrations `0013_character_ownership`, `0014_personas`, `0015_chat`.

**Architecture & process**
- Adapters moved out of `core/` into `adapters/`; `core/ports.py` introduced; import-linter enforces layering (ADR-0001).
- `bot/commands.py` split into `panel.py` + `handlers/*`; `db/worker.py` removed.
- Tooling: uv, ruff format, pyright, pre-commit + gitleaks, CI, hypothesis tests.
- Documentation split into `SPEC.md` (behavior), `ARCHITECTURE.md`, `AGENT.md` (process R29–R33), `CHANGELOG.md`, `docs/adr/`. Hard-coded counts removed from documents. "stealth headers" wording replaced by "standard browser headers"; "anti-captcha providers" replaced by "providers that may present a challenge" (policy R28 unchanged).
- Migrations planned: `0005_job_lease` … `0009_audit_and_admin`.

## v4.0 (2026-10-17)

Multi-admin model; bottom-keyboard = new panel / in-menu = same panel (R2–R3); back/close everywhere (R4); viewer edits-in-place (R5); fast typing (R6); **exact image counts 2/4/6/8/16 with undercount gate + auto top-up + partial_count** (R9–R11); stale-gallery exclusion (R10); character = appearance facts + scene at generation (R14–R15); known characters (R16); save-button policy (R17); edit = new version (R18); AI enhance at both confirms + env-only model (R20–R21); settings fully editable with safety ceilings (R25); adult restriction removed / minor protection kept (R26); interruption notices (R13); toggle settings buttons; panel close; full architecture documented.

## v3.1

Original MVP spec (see `docs/SPEC-v3.1-archive.md`).
