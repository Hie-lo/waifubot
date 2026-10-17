# WaifuBot — Specification v4.1

**Product:** a multi-admin Telegram bot (Persian-first UI, full English locale) that generates mostly anime character images through replaceable providers (which may present a challenge), lets admins curate them into a pool, and publishes approved images — directly to channels, as albums to groups, or on a schedule from the pool. It carries a character-identity system (appearance facts separate from scenes), an AI-enhance step for prompts, NSFW support with spoilers, a **concurrent job engine** (several jobs generate at the same time, each in its own isolated provider slot), **plans with per-user limits**, **channel management with permission checks**, and a **character chat companion** (ownership, persona, compact memory).

**Environment:** Debian on WSL2, 16 GB RAM, Intel i5-12400, no usable GPU inside WSL (upgradeable).

**Status:** this document is the single source of truth for **what the product does** (behavior contract). Other concerns live elsewhere:

| Concern | Where |
|---|---|
| Code layout, layering, data model, concurrency design, deployment | `ARCHITECTURE.md` |
| How agents/humans work: process rules R29–R33, tooling, definition of done | `AGENT.md` |
| Version history | `CHANGELOG.md` |
| Why a decision was made | `docs/adr/` |
| Staged plan v4.0 → v4.1 | `docs/MIGRATION-v4.0-to-v4.1.md` |

It supersedes **Specification v4.0** (git history) and **v3.1** (`docs/SPEC-v3.1-archive.md`).

> **Golden rule:** the user's word supersedes this file. When a live instruction contradicts anything here, follow the instruction and report the conflict in the delivery report.

*How to read:* numbers marked *starting default* are tunable guesses. **[VERIFY]** marks external facts that must be checked before code depends on them. **Rule ids (R-numbers) are stable and never reused**; new rules are appended at the end (R34+). Counts such as "N tests" or "N callbacks" are deliberately not written in this file — tests enforce them.

---

## 1. Product, roles, scope

### 1.1 Core workflow

```
user scene + character data → prompt (+ optional AI enhance) → job
→ provider generates an EXACT image count (jobs run concurrently, one slot each)
→ pipeline validates → admin reviews
→ approved images enter the pool → published (direct / album / schedule)
```

Approval happens once per image, at review time. Nothing unreviewed is ever published, including by the scheduler — the only exception is a user publishing their own private results to their own verified channel (R59).

A second flow sits beside it: owned characters → persona → chat (R60–R66).

### 1.2 Roles

| Role | Powers |
|---|---|
| **superadmin** | everything, with **no limits of any kind** (R53) + admin management (R48) + system controls (R49) + audit log view |
| **admin** | generate, review, pool, publish, characters, queue, settings, scheduler, user management (R50), plans (R52), channels (R56–R59); runs on the built-in `staff` plan |
| **user** | self-service within plan limits (R51): generate with private results, own characters, claim, chat, account; can be blocked (R50) |

Admins are stored in the DB (`is_superadmin`). Env `ADMIN_IDS` **seeds missing admins only** (insert-if-missing, see R44). Admins are also added and removed inside the bot (*Settings → Admins*, R48). `/manage` is the hub; every item is button-driven.

**Permission matrix** (a ✓ is always subject to the plan limits of R52 unless the role is superadmin, R53):

| Capability | user | admin | superadmin |
|---|---|---|---|
| start / help / My account | ✓ | ✓ | ✓ |
| Generate images | ✓ plan, private results (R51) | ✓ staff plan | ✓ no limits |
| Own characters: create in DM, claim | ✓ plan (R60–R61) | ✓ | ✓ no limits |
| Persona editing + chat with owned characters | ✓ plan (R62–R64) | ✓ | ✓ no limits |
| Add channels | only if the plan allows (R56–R59) | ✓ after the R57 check | ✓ exempt from the R57 ownership check |
| Review, pool, publish, scheduler | ✗ | ✓ | ✓ |
| Settings, plans, user management | ✗ | ✓ (R50, R52) | ✓ |
| Admin management, system controls, audit | ✗ | ✗ | ✓ (R48, R49, R44) |

### 1.3 In scope

Provider abstraction (mock + Perchance now; Zovla/pixai and OnlyWaifus later), prompt building from character facts + user scene, image pipeline (validation, hashing, atomic storage), full Telegram admin UX (including admin management, user management and restart controls), **concurrent job engine** (queue, workers, slots, leases, exact-count enforcement), pool + direct + scheduled publishing (channels and group albums), character identity system with versioning, AI-enhance step, NSFW handling with spoilers, **plans and per-user quotas, channel registry with permission checks, character ownership (claim/create) and character chat with persona and compact memory**, durable state (SQLite + Alembic), Persian/English locales, structured logs, safe errors, recovery, challenge policy.

### 1.4 Out of scope (for now)

Web admin panel (path reserved), paid NSFW channel button (marked **بعداً**), AI captions for albums (typed captions now), multi-provider routing, payments/billing (plans are assigned by admins; marked **بعداً**), ownership transfer between users, characters chatting in groups, voice chat, streaming replies, automatic challenge solving (explicitly forbidden — see R28).

---

## 2. Rules — the contract

These are binding behavior rules. Every change must preserve them; the review pass checks them. (Process rules R29–R33 moved to `AGENT.md`; their ids are unchanged.)

### 2.1 UI & navigation

- **R1 — Interactive-first.** Every action is a button/menu where possible; typed input only where the user must type (prompts, names, captions, scenes). Toggles are buttons, never "type 1/0".
- **R2 — Bottom keyboard = sections.** The reply keyboard holds the main sections (labels from locales). Pressing a bottom button — or typing a command — **starts a NEW panel message** under the user's message.
- **R3 — One panel per menu.** Every interaction *inside* a menu (inline buttons, wizard steps, typed dialog answers) **edits the same panel message in place**. Tracked in `PanelMemory` keyed by admin **and chat** (see §4.2).
- **R4 — Back & close.** Every screen except the main menu has a working **back** button; every panel has a **close** button (deletes the panel message, hides the reply keyboard). The main reply keyboard is non-persistent (`is_persistent=False`).
- **R5 — Viewer edits in place.** Pool and inbox viewers: prev/next/toggles **edit the same message** (`edit_message_media` / `edit_message_caption`); closing strips the buttons and leaves the photo. Never a new photo per navigation press.
- **R6 — Fast typing.** A new typed prompt while a wizard is open silently abandons the old wizard and starts fresh with the new text (long prompts supported). In chat mode typed text goes to the character instead (R63).
- **R7 — No dead ends.** Every rejection/error is explained (locale text) with an actionable button when one exists.
- **R8 — Callback audit (automated).** Every callback is a typed `CallbackData` (R41) registered in one registry. A test enumerates the registry and fails if any entry is unrouted or encodes to more than 64 bytes.

### 2.2 Generation & image counts

- **R9 — Exact counts.** Supported counts are exactly the site's options **2/4/6/8/16** (`SITE_COUNTS`). A job succeeds **only when exactly N valid images are kept**.
  - Pipeline rejects (malformed/oversized/duplicate/near-duplicate) and slow-partial provider batches count as **undercount**.
  - `error_code = partial_count` (or `no_valid_images` when zero) with a summary line (`undercount: got/need (rejected: …)`).
  - **Already-produced images are kept** and shown.
  - If the provider returns **more** valid images than needed, the first `needed` (in provider order) are kept; the surplus is dropped, never saved, and logged as `surplus_dropped`. The gate therefore compares `kept == N`, never "≥".
  - An automatic **top-up requeue** requests only the missing count (`needed = N − kept`) and continues until exactly N — the default path (site glitches self-heal), bounded by the progress rules of R38.
  - When attempts run out: `failed` + retry button; manual retry starts a fresh run (R38) and keeps topping up.
- **R10 — No stale or cross-job gallery images.** Defense in depth, all three required: (1) every job runs in its own isolated slot (R35) so galleries are never shared; (2) before clicking generate, the provider snapshots the page gallery and excludes any pre-existing image URL; (3) the pipeline dedupes by content hash against the job's kept images and the recent pool (R47).
- **R11 — Pipeline cannot shrink the contract.** Everything the filter drops is accounted for at the job gate (R9).
- **R12 — Retry + notify.** Failed/partial jobs retry up to configured attempts with `RETRY_BACKOFF_SECONDS` backoff; job completion/failure always notifies the admin(s).
- **R13 — Interruptions announced.** Recovered interrupted jobs are announced to admins (`interrupted_notice` per job) — at startup **and** whenever the runtime reaper requeues an expired lease (R36).

### 2.3 Characters & identity

- **R14 — Character data = appearance facts ONLY:** name, anime, age, height, hair/eye/skin color, face shape, body type, body-part sizes (NSFW). No actions, expressions, poses, clothes, or scenes in character data. Personality lives in the separate persona (R62).
- **R15 — Scene at generation.** The user types the scene/action per generation; prompt build combines **facts first, scene after** (facts override templates, never the reverse).
- **R16 — Known characters.** The user types name + anime (example provided in the dialog); AI-enhance may append canonical details.
- **R16B — Special Character Studio (Vision Lab presets).** A dedicated click-only generation mode exists alongside free typing. The user selects from the catalog of 114 known characters (`config/vision_lab_presets.yaml`) and chooses modular preset options (outfit, pose, expression, location, atmosphere, camera, art style, hair/bangs, fantasy race). The final prompt is composed deterministically by joining non-empty selected tags with `, ` (mirroring Vision Lab's `sync()` contract). No typed prompt is accepted in this mode (prevents site prompt-reset anomalies). Standard quotas and image counts (R9, R54) apply identically.
- **R17 — Save button policy.** The "save character" button appears **only** on generated-image jobs. Jobs generated *from* a saved character never show it.
- **R18 — Edit = new version.** Editing a saved character creates a new version of the same character: stable `character_id`, integer `version`, `is_current` flag, label "نسخه N". The previous version stays alive and linked (`edit_of` = parent version).
- **R19 — My-characters screen** lists all characters with version info; create/edit are full button flows with confirmation and back buttons.

### 2.4 AI enhance

- **R20 — Both confirmations.** The "🪄 AI Enhance" button appears at the **character-prompt confirm** *and* the **final-prompt confirm**. Enhancement preserves every user detail (style, flags, subject, scene) and only adds canonical details for known characters (name + anime given), nothing for unknown ones.
- **R21 — Model is env-only.** `ai_enhance_enabled` is a 0/1 toggle button in the bot settings; **`ai_enhance_model` is not a bot setting** — it is read from `AI_ENHANCE_MODEL` (Claude models; Haiku recommended). Same for `ANTHROPIC_API_KEY`.
- **R39 — Enhance is re-checked and fails soft.** The enhancer's output passes the existing `safety` hard rules (minor protection and forbidden-name base, R26) before it reaches the prompt; if it fails, the original text is kept and the admin is told why (R7). The call has a timeout (`ai_enhance_timeout_seconds`); on API error or timeout the original text is kept with an explanation and a retry button. Token usage is logged.

### 2.5 NSFW & publishing

- **R22 — NSFW per generation**, toggle button + checkbox confirmation; NSFW images post with a spoiler (`has_spoiler=True`); a mixed album may contain spoiler and non-spoiler images together (spoiler is per-image).
- **R23 — Group albums:** 2–10 images per album, typed caption (AI captions later), group/topic picker, per-image delete with live count, split preview when > 10.
- **R24 — Publish errors are user-visible** (`publish_too_large` with guidance; `publish_failed` on API failure).
- **R42 — Telegram rate limits.** All outbound Telegram calls go through one rate limiter; flood-wait (`retry_after`) is honored with backoff; album and scheduler sends are paced; the Telegram `file_id` is cached after the first upload and reused for re-publishing (no repeated upload of the same image).

### 2.6 Settings, safety, administration

- **R25 — Everything is changeable in the bot.** The settings panel exposes every runtime knob; `.env` supplies *defaults and operational secrets only*. Code-level **safety ceilings** bound user input (§4.3). Values save on change and apply immediately (no restart; in-process cache is invalidated on write). Every change is audited (R44).
- **R26 — Content policy.** There is **no adult-content restriction**: NSFW generation is governed only by the per-generation toggle and confirmation (R22). **Minor protection is kept**: the `safety` hard rules reject any prompt or character that depicts or implies a minor, regardless of the NSFW flag, and the forbidden-name base (real people) stays in force. No other restrictions are added.
- **R27 — No silent dead ends.** Unauthorized actions answer with `not_authorized` (never silence).
- **R40 — Panel concurrency safety.** Updates from one admin are handled one at a time (per-admin lock); every callback is answered immediately; stale or duplicate presses (panel version/nonce mismatch) are ignored idempotently with a gentle refresh; edit failures are handled: "message is not modified" is ignored, "message not found" creates a new panel and updates `PanelMemory`.
- **R41 — Typed callbacks.** All callback data is produced and parsed by typed `CallbackData` classes (compact, versioned prefix, ≤ 64 bytes) — no hand-built strings.
- **R44 — Audit & admin seeding.** Settings changes and admin management are written to `audit_log` (who, what, old → new, when), viewable by superadmin. `ADMIN_IDS` inserts admins that are **missing** and never re-promotes or demotes anyone; an admin removed in the bot is soft-disabled (`removed_at`) and is not re-seeded at startup. The add/remove flows are specified in R48.
- **R46 — Observability.** Logs are structured (JSON file + console) and carry `job_id`, `admin_id`, `provider`, `slot` when known. Admins have `/status`: queue depth, running jobs and slots, effective concurrency, free RAM, last errors, current worker configuration, uptime and last restart. The CLI has `waifubot health`.

### 2.7 Concurrency & job engine

- **R34 — Concurrent jobs.** Several jobs execute at the same time. A *worker* is an asyncio task that claims and runs **one job at a time**; concurrency comes from running several workers. Effective concurrency = `min(worker_count, max_concurrent_jobs, max_browser_contexts, provider_concurrency)`. Idle surplus workers cost nothing. Limits are editable in the panel (R25) and apply without restart: raising them adds capacity immediately; lowering them lets running jobs finish (no job is killed to lower a limit). The queue is fair by default: plan priority first (`queue_priority`, R55), then round-robin across **requesters** (`fair_claim`, starting default on), then oldest first — so no single admin's or user's big batch can starve others.
- **R35 — One slot per job.** Provider resources are acquired through `ProviderSlotPool.acquire()` (async context manager); a slot is exclusive to one job at a time. For browser providers: one Chromium process, one isolated `BrowserContext` per slot; warm contexts persist their storage state so sessions are **reused across jobs (R28) but never shared concurrently**. A soft `MemoryGuard` opens no new slot while free RAM is below `min_free_ram_mb`; affected jobs wait and admins are notified once. The guard never changes the ceilings.
- **R36 — Atomic claim + lease.** Claiming is one atomic statement inside an immediate transaction with async retry on `SQLITE_BUSY` (exponential backoff). A running job holds a lease (`lease_owner`, `lease_expires_at`) renewed by a heartbeat every `heartbeat_seconds` (with $\pm 20\%$ jitter to avoid lockstep contention). A reaper requeues jobs whose lease expired (counts as one attempt and is announced, R13). Correctness never depends on startup recovery alone.
- **R37 — Graceful shutdown.** On SIGTERM/SIGINT the bot stops claiming, waits up to `shutdown_grace_seconds` for running jobs, releases unfinished leases back to `pending` **without consuming an attempt**, closes slot pools and the browser, then exits. A second signal exits immediately.
- **R38 — Top-up budget & progress.** A run allows `topup_rounds` top-up rounds (starting default 6). A round that adds zero new valid images increments a zero-progress streak; reaching `zero_progress_limit` (starting default 3) ends the run as `failed` with a retry button. A manual retry starts a fresh run (counters reset; total rounds stay visible). Top-up is idempotent (content-hash dedupe against already-kept images, R10).

### 2.8 Data, scheduling, challenges

- **R43 — Provenance.** For every image the system stores: final prompt, negative prompt, seed (if known), provider + provider-config version, character version id, job id, enhance flag.
- **R45 — Scheduler.** Schedules live in a `schedules` table (several channels, each with its own state). Timezone is `app_timezone` (starting default `Asia/Tehran`); Persian UI shows Jalali dates. Missed-run policy after downtime: `missed_run_policy = skip | catch_up_one` (starting default `catch_up_one`: at most one catch-up post per schedule, never a burst). Only reviewed pool images are scheduled.
- **R47 — Image store.** Files are content-addressed (sha256 path) and written atomically; EXIF is stripped. Duplicate detection = exact hash **and** perceptual hash (Hamming distance ≤ `phash_distance`, starting default 6, `0` disables) against the job's kept images and the recent pool; a near-duplicate counts as undercount with reason `near_duplicate` (R9).
- **R28 — Challenges are never solved automatically.**
  - The provider's own options (solve 1×, enable 1×, solve+enable) are offered first.
  - The challenge window opens **only** for an actual challenge, and the same single window/tab is reused.
  - The human hover-checkbox is the **last fallback**.
  - A failed challenge stops with a retry button.
  - No automated puzzle/text-challenge solving, ever.
  - With concurrency, challenges are serialized by one `ChallengeGate`: one window at a time; other jobs that need it wait up to `challenge_wait_seconds` and then go to `retry_wait`.
  - Playwright **async API only**. Provider sessions are reused (R35).
  - Every provider has an entry in `docs/provider-permission.md` (permitted / spike-only); live use requires the entry.

### 2.9 Administration, users, system control

- **R48 — Admin management in Settings.** *Settings → Admins* (superadmin only) lists admins with role and status. Everything is button-driven (R1), edits one panel in place (R3) and has back/close (R4).
  - **Add an admin** in any of three ways: (a) a **"pick a user" button** that uses Telegram's built-in user picker (no typing); (b) a typed numeric Telegram ID; (c) a **one-time invite link** (`?start=adm_<token>`) that is single-use, expires after `invite_ttl_hours`, and carries the role chosen at creation — when it is used, the person becomes an admin and the creating superadmin is notified.
  - The role (admin / superadmin) is chosen while adding, with a confirmation screen.
  - Per admin: promote/demote (admin ↔ superadmin), remove (soft-disable `removed_at`; adding the same person again restores them), last-activity info.
  - **Invariant:** at least one active superadmin always exists — the last superadmin cannot be demoted or removed.
  - A removed admin loses access immediately; their open panels answer `not_authorized` (R27).
  - Every action is audited (R44); the target person gets a short notice when added or removed.
- **R49 — System controls.** *Settings → System* (superadmin only):
  - **🔄 Restart workers:** graceful (R37 steps) re-creation of the `WorkerPool`, slot pools and browser; the bot keeps answering meanwhile.
  - **🔁 Restart bot:** confirmation screen → graceful drain (R37) → the process exits with code `75` and the supervisor restarts it (systemd `Restart=always` with `RestartForceExitStatus=75`). If no supervisor is detected, the bot re-executes itself (`os.execv`) and tells the admin that it did. Telegram updates received during the restart are not dropped (`drop_pending_updates=False`).
  - A `restart.pending` marker (requester, panel coordinates, time) is stored; after startup the **same panel** is edited to "restarted ✅" with uptime and the number of recovered jobs, and R13 notices go out for interrupted jobs.
  - A restart request while one is already in progress is ignored with an explanation (R7). Both actions are audited (R44).
- **R50 — User management (all admins).** Every person who opens the bot is recorded in `users`. *Settings → Users* (button flows, edit in place):
  - **Overview:** total users, active today, new this week, blocked.
  - **List** with paging and filters (active / blocked / new) and **search** by id, username or name (typed).
  - **User card:** details, block / unblock (optional typed reason), a note, per-user language (fa / en), delete record, and — superadmin only — "promote to admin", which hands over to the R48 flow.
  - A blocked user receives a short `blocked_notice` once per session and is otherwise ignored; the check runs before any handler. Admins cannot be blocked from here (change their role in R48 first).
  - Deleting a record removes the profile; if the person returns they are recorded as new.
  - Every action is audited (R44).

### 2.10 Users, plans & quotas

- **R51 — User self-service.** Anyone who opens the bot and is not blocked is a *user* and gets, within the limits of their plan, the capabilities of the user role:
  - 🎨 **Generate** with the same wizard (style → prompt/character → flags → count → NSFW → confirm; R6, R9, R15, R22), limited to the counts the plan allows. Results are **private**: delivered to the requester and kept in **My images** (viewer with delete, R5). They never enter review, the pool, any channel or another user's view — except when the user publishes them to their own verified channel (R59).
  - 🧬 **Own characters:** create in DM and claim from the catalog (R60–R61).
  - 💬 **Chat** with owned characters (R62–R64).
  - 👤 **My account:** plan, usage today with reset time, limits, owned characters, own channels.
  - The reply keyboard differs by role (labels from locales); a user never sees staff sections, and staff-only callbacks answer `not_authorized` (R27). R26 applies to everyone.
- **R52 — Plans.** A *plan* is a named bundle of limits (§3.6). Admins create, edit and delete plans in *Settings → Plans* and assign them from the user card (R50), optionally with an expiry (afterwards the user returns to `default_plan_id`). Any single limit can be overridden per user on the user card. `null` = unlimited. The built-in `free` plan (default for new users) and the built-in `staff` plan cannot be deleted. Deleting another plan that still has users requires choosing a replacement. Changes apply immediately and are audited (R44). Lowering a limit never deletes what a user already has; they simply cannot add more.
- **R53 — Superadmin has no limits.** For the superadmin role **no limit of any kind applies to any activity**: plan quotas, per-user rate limits, cooldowns, character and channel caps, queue weights. Every limit check short-circuits for this role in exactly one place (`effective_limits`, ARCHITECTURE §5A). The technical capacity ceilings of §3.3 remain code constants that protect the host, and the content rules of R26 apply to everyone because they are not activity limits. Admins run on the built-in `staff` plan (starting default: unlimited on every field, `queue_priority` 10), which a superadmin may edit.
- **R54 — Quota accounting.** Limits are enforced where an action starts, atomically, through one function `check_and_reserve(user, kind, amount)` that every entry point uses (no handler counts on its own):
  - *Generation:* at `create_job` the requested image count is **reserved** against `daily_image_budget`, `max_images_per_job`, `max_concurrent_jobs` and `max_stored_images`; at the exact-count gate (R9) the reservation is **settled** to the kept count and the rest refunded; failed or cancelled jobs refund everything — users pay only for delivered images.
  - *Chat:* each accepted user message consumes one `daily_chat_messages` unit.
  - *Characters and channels:* `max_owned_characters` / `max_channels` are checked inside the same transaction as the claim, create or add.
  - Days are cut in `app_timezone`; counters live in `usage_counters`. A limit hit produces a locale message naming the limit and its reset time with an actionable button (R7).
- **R55 — Queue priority & fairness.** Jobs carry `requester_user_id` and `job_scope` (`staff|user`). Claim order: the requester plan's `queue_priority` (descending), then round-robin across requesters, then oldest first (R34).

### 2.11 Channels

- **R56 — Channel registry.** Every channel or group the bot posts to is registered in `channels`; raw ids are never typed into schedules. *Settings → Channels* (admins) and *My channels* (users whose plan allows, R59) are button-driven (R1). Add by any of: (a) Telegram's **chat-picker button** (`request_chat`) filtered to channels/groups; (b) forwarding a message from the chat; (c) a typed `@username` or numeric id. On add, the bot verifies **in this order** and explains each failure with the exact fix (R7):
  1. the chat exists and the bot can see it;
  2. the bot is an **administrator with the rights it needs** — channels: post messages (+ edit messages for claim marks); groups: send messages and media (+ manage topics for the topic picker);
  3. the person adding passes R57.

  On success it stores kind, title, username, `verified_at`, a snapshot of the bot's rights and status `active`. Forum groups also store their topics (id + title) for the topic picker (R23).
- **R57 — Adder permission check.** *Who adds* matters: the person must be the **creator or an administrator** of that channel/group, verified with `get_chat_member(chat, adder)` (for channels: creator, or admin with the post right). A non-member or plain member is rejected with `channel_not_yours`. (a) **Superadmin** is exempt from this ownership check (R53) — the bot-rights check of R56 step 2 is a technical necessity and always runs; (b) **admins** must pass it; (c) **users** must pass it and have plan capacity (`max_channels`, R52). The adder and their role in the chat are recorded (`added_by`, `adder_role_in_chat`).
- **R58 — Channel management & health.** Per channel: view card; **re-verify now**; alias; enable/disable; default channel (per owner); topics; scope (R59); remove (blocked while an active schedule uses it — the bot offers to pause or reassign); list of schedules using it. **Health:** the bot handles `my_chat_member` updates and a periodic `channel_recheck_hours` task (starting default 12) re-verifies every active channel. When the bot loses rights the channel becomes `lost_access`, its schedules are **paused, not deleted**, and the owner and admins are notified with a fix button; after rights return and a re-verify succeeds the channel is `active` again and schedules resume only after confirmation. Channel ids that existed in v4.0 schedules are migrated as `legacy_unverified` and verified on first use.
- **R59 — Channel scope & use.** Each channel has `owner_user_id` (the verified adder) and a `scope`: `private` (owner only) or `shared` (all admins may publish and schedule to it; set by the owner or a superadmin). Staff may use shared channels and their own private ones; superadmin may use all. Using a channel one does not own triggers a **fresh R57 check at that moment** (an old grant never carries over). Users with channel capacity may publish **their own** images or characters to **their own** channels directly (no scheduler, no pool), paced by R42. Every publish first checks that the bot can still post and reports failures with R24.

### 2.12 Character ownership & chat

- **R60 — Ownership model.** Ownership attaches to the stable `character_id` (R18), so editing appearance (a new version) never changes the owner. States: `draft` (staff only) → `listed` (claimable) → `claimed` (one owner), or `private` (created by a user in DM, owned from the start). Exactly **one owner at a time**; only the owner (and staff) can chat with the character, edit its persona or release it. The owner can also create appearance versions (generation costs the owner's quota, R54) and **release** it: a claimed character returns to `listed`, a private one is deleted after confirmation; in both cases its persona and chat memory are deleted (R66).
- **R61 — Claiming.** A character is claimable when staff publish it with the **claim option** (the channel post gets a "💬 Chat / Claim" deep-link button) or mark it `listed`. Users claim by: (a) the deep-link button on a channel post (`?start=char_<token>`); (b) the in-bot **catalog** of unclaimed characters (viewer, edits in place, R5) with a *Claim* button; (c) **🎲 Random** (a random unclaimed character, with confirmation). A claim is **atomic first-come-first-served** (`UPDATE … WHERE status='listed'`) and checks `max_owned_characters` in the same transaction (R54); the loser of a race gets `already_claimed` with a button to the catalog (R7). After a claim, the post's button is edited to "claimed ✅" best-effort (failures ignored). Superadmin ignores the claim limit (R53). Admins can revoke or reassign ownership from the user card (R50), audited (R44). `claim_enabled` is a settings toggle.
- **R62 — Persona.** Personality is a **separate persona**; R14 is untouched (character facts = appearance only). Fields: `display_name`, `speaking_style`, `temperament`, `backstory` (short), `likes`, `dislikes`, `relationship_to_user` (friend / partner / rival / mentor / custom), `quirks`, `avoid_topics` (user-defined), `language` (default = user locale), `opening_message`, free `notes`. It is edited by buttons, one field per screen (R1, R3, R4), and has a **🪄 AI Enhance** button with the same contract as R20: every user detail is preserved; for known characters it adds canonical personality from name + anime; for unknown ones it only tidies and expands wording, inventing nothing. The result is shown as a preview with Save / Edit / Cancel. The bot compiles the persona into a compact **persona card** (starting default ≤ `persona_card_tokens` = 300, stored with its hash) used by every chat prompt; editing recompiles it and keeps the previous version for one-step undo. For known characters, first setup offers *auto-fill from canon* (AI Enhance). Persona text passes R66.
- **R63 — Chat mode.** In the user's DM, *💬 Chat* lists owned characters; choosing one opens the **chat panel** (end chat, persona, memory, switch character) and enters **chat mode**: typed text goes to the character and the reply arrives as a normal message with a typing indicator. Chat mode ends when any bottom-keyboard section is pressed, on `/endchat`, or via the panel's end button. While it is active **typed text is chat, not a generation prompt** (this overrides R6; R2–R4 keep their meaning for panels). Chat is private-DM only. Rapid consecutive messages are merged (`chat_debounce_ms`, starting default 1200) into one model call. If chat is disabled (`chat_enabled`), a limit is hit, or the model fails, the user gets an explained message with a button (R7) — nothing is silently dropped.
- **R64 — Chat memory (compact by design).** For each (owner, character) session the prompt is built from four small parts: (1) the **persona card**; (2) a rolling **summary** (≤ `chat_summary_tokens`, starting default 300); (3) a **facts list** of durable things about the user, ≤ `chat_facts_max` one-line items (starting default 20); (4) the last `chat_window_turns` raw turns (starting default 8). Dialogue text is normalized with Unicode NFC. Persian token estimation applies a calibrated upper-bound ($\lceil \text{chars} \times 1.4 \rceil$ or $\lceil \text{bytes} \times 0.70 \rceil$). **Compaction:** triggers under a dual condition — every `chat_compact_every_turns` turns (starting default 10) OR when the window exceeds its token budget (`chat_window_tokens`); one cheap model call receives (old summary + facts + the turns leaving the window) and returns strict JSON `{summary, facts_add, facts_remove}` (with regex extraction fallback); those turns are then dropped — **raw history is never stored beyond the window**. Summary + facts are hard-capped by the plan's `chat_memory_tokens` (R52); on overflow the compaction call is given the target size. No vector database. The user can **view, delete single facts, and reset** memory in the chat panel.
- **R65 — Chat cost controls.** Models are env-only like R21: `AI_CHAT_MODEL` (recommendation: Haiku-class) and `AI_CHAT_COMPACT_MODEL` (default = the same). Controls: reply cap `chat_max_reply_tokens` (starting default 300), `chat_timeout_seconds`, global concurrency `chat_max_concurrency` (starting default 4, a semaphore as in R34), per-plan `daily_chat_messages` (R54), **prompt caching** on the consolidated stable prefix (system instructions + safety + persona card + facts + few-shot dialogue, calibrated to cross Anthropic's minimum $\ge 1,024$-token threshold), and per-user token usage in `usage_counters` (batched per turn). `chat_cost_alert_usd` only **alerts** the superadmin and never blocks anyone. `chat_enabled` is a global toggle.
- **R66 — Chat safety & privacy.** The existing hard `safety` rules (R26: minor protection, forbidden-name base) apply to persona text, user messages and model replies; no other chat restriction is added. A reply that fails the check is replaced by a short in-character refusal and logged without its text. Chat content is private: admins see counts and token totals, never message text or memory. Persona, memory and sessions are deleted when the owner releases the character, when the character is deleted, and when the user's record is deleted (R50). Logs never contain message text (R46).

---

## 3. Contracts

### 3.1 Job lifecycle

`pending → running → succeeded | retry_wait | failed`, plus `cancelled` (allowed while not running). `partial_count` and `no_valid_images` are **error codes**, not statuses. Extra transitions: `running → pending` (graceful release, R37), `running → retry_wait` (expired lease or partial, R36/R9), `retry_wait → pending` when `next_attempt_at` is due. Success requires `kept == N` at the gate.

### 3.2 JSON shapes

- `Character.details_json`: `{traits{…}, extras, mode: known|manual|precise, name, known_name, known_anime}` — version info is in columns (`character_id`, `version`, `is_current`, `edit_of`), not in JSON.
- View state (pool/inbox): `{page, total, img_id, visible: [c,m,s,d,sp], caption}`
- Panel memory: key `settings.panel:<admin_id>:<chat_id>` → JSON `[message_id, panel_version]`
- AI enhance request: `{details_json, scene_prompt, name, known_name, known_anime}`
- Schedule row (table `schedules`): `{id, channel_id, topic_id, post_count, random_order, min_gap_minutes, max_gap_minutes, next_at, running, missed_run_policy}`
- Audit row: `{id, at, admin_id, action, target, old, new}`
- User row (table `users`): `{telegram_id, username, name, locale, first_seen, last_seen, status: active|blocked, block_reason, note, joined_via}`
- Admin invite (table `admin_invites`): `{token_hash, role, created_by, expires_at, used_by, used_at}`
- Restart marker: key `restart.pending` → `{requested_by, chat_id, message_id, at}`
- User row additions: `{plan_id, plan_expires_at, limit_overrides_json}`; usage row (table `usage_counters`): `{user_id, period_key, images, jobs, chat_messages, tokens_in, tokens_out}`
- Plan row (table `plans`): `{id, name, limits{…per §3.6}, is_builtin, created_by}`
- Channel row (table `channels`): `{id, tg_chat_id, kind: channel|group|supergroup, title, username, alias, owner_user_id, scope: private|shared, status: active|disabled|lost_access|legacy_unverified, bot_rights_json, verified_at, added_by, adder_role_in_chat}`; topics in `channel_topics`
- Ownership row (table `character_ownership`): `{character_id, status: draft|listed|claimed|private, owner_user_id, claimable, claimed_at, released_at}`; claim posts in `claim_posts` `{character_id, chat_id, message_id}`
- Persona row (table `character_personas`): `{character_id, version, fields_json, notes, card, card_hash, prev_json}`
- Chat (tables `chat_sessions`, `chat_memory`): `{session_id, user_id, character_id, last_at}` / `{session_id, window[…], summary, facts[…], mem_tokens, turn_count, since_compaction, version}`

### 3.3 Settings registry & ceilings

All of these are editable in the panel (R25). *Starting defaults* are guesses to be measured; ceilings are code constants.

| Key | Meaning | Default | Ceiling |
|---|---|---|---|
| `worker_count` | worker loops (R34) | 4 | 100 |
| `max_concurrent_jobs` | global running-job limit | 3 | 20 |
| `max_browser_contexts` | slot pool size (R35) | 3 | 20 |
| `provider_concurrency` | per provider (from `config/providers/*.yaml`) | 2 | = `max_browser_contexts` |
| `fair_claim` | round-robin across admins | on | — |
| `min_free_ram_mb` | soft memory guard | 1500 | — |
| `heartbeat_seconds` / `lease_ttl_seconds` | lease timing (R36) | 15 / 60 | — |
| `shutdown_grace_seconds` | R37 | 60 | — |
| `topup_rounds` / `zero_progress_limit` | R38 | 6 / 3 | — |
| `challenge_wait_seconds` | R28 gate wait | 120 | — |
| `ai_enhance_enabled` / `ai_enhance_timeout_seconds` | R20 / R39 | on / 30 | — |
| `phash_distance` | R47 | 6 | 64 |
| `app_timezone` / `missed_run_policy` | R45 | Asia/Tehran / catch_up_one | — |
| `invite_ttl_hours` | admin invite validity (R48) | 24 | — |
| `default_plan_id` | plan for new users (R52) | `free` | — |
| `claim_enabled` | master switch for claiming (R61) | on | — |
| `channel_recheck_hours` | R58 health re-verification | 12 | — |
| `chat_enabled` | master switch for chat (R65) | on | — |
| `chat_max_concurrency` | simultaneous model calls (R65) | 4 | 50 |
| `chat_window_turns` / `chat_compact_every_turns` | R64 | 8 / 10 | — |
| `chat_summary_tokens` / `chat_facts_max` / `persona_card_tokens` | R64 / R62 | 300 / 20 / 300 | — |
| `chat_max_reply_tokens` / `chat_timeout_seconds` / `chat_debounce_ms` | R65 / R63 | 300 / 30 / 1200 | — |
| `chat_cost_alert_usd` | alert-only threshold (R65) | off | — |
| (existing) retries, sizes, scheduler tick, pool caps | unchanged from v4.0 | — | — |

### 3.4 Environment (defaults & secrets only — R25)

`TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `AI_ENHANCE_MODEL`, `AI_CHAT_MODEL`, `AI_CHAT_COMPACT_MODEL`, `PROVIDER`, `PROVIDER_CONFIG_PATH`, `POOL_DIR`, `LOG_LEVEL`, `RETRY_MAX_ATTEMPTS`, `RETRY_BACKOFF_SECONDS`, `SCHEDULER_TICK_SECONDS`, `MAX_IMAGE_MB`, `ALLOWED_MIME_TYPES`, `CHALLENGE_SLEEP_SECONDS`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH`, `POOL_CAP_ITEMS`, `POOL_CAP_BYTES`, plus the defaults for every key in §3.3. Secrets are loaded as `SecretStr` and never logged.

### 3.5 Locale policy

All user-facing text goes through `bot/messages.py` (`t(key, **fmt)`). `REQUIRED_KEYS` are the minimum both `fa.yaml` and `en.yaml` define. Tests enforce: key parity, **identical placeholder sets** per key across both files, and no unused keys. Mixed Persian/Latin strings use RTL isolation marks where needed. Adding a key = update both files.

### 3.6 Plans & limits

Fields of a plan (R52). `null` = unlimited. Admins edit all of them in *Settings → Plans*; starting defaults are guesses to measure.

| Field | Meaning | `free` (default plan) | `staff` (admins) |
|---|---|---|---|
| `max_owned_characters` | claimed + private characters (R60–R61) | 3 | unlimited |
| `daily_image_budget` | images per day (R54) | 8 | unlimited |
| `max_images_per_job` | largest allowed count of 2/4/6/8/16 | 4 | 16 |
| `max_concurrent_jobs` | running + pending jobs per user | 1 | unlimited |
| `max_stored_images` | capacity of My images | 100 | unlimited |
| `daily_chat_messages` | accepted chat messages per day | 60 | unlimited |
| `chat_memory_tokens` | cap for summary + facts (R64) | 500 | 1500 |
| `max_channels` | channels a user may add (R59) | 0 | unlimited |
| `queue_priority` | higher = claimed first (R55) | 0 | 10 |

The superadmin is not subject to any field (R53); for the chat-memory size the superadmin simply uses the system defaults of §3.3, which is a design parameter, not a quota.

---

## 4. Known deferred items

`.wslconfig` tuning after RAM measurement (feeds the starting defaults of §3.3); live-run announcement protocol before any `--confirm-live`; paid NSFW button; plan payments/billing; ownership transfer; AI captions; web panel. These are open asks — nothing else is pending.
