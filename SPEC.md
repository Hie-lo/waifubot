# WaifuBot — Specification v3.1

**Product:** a single-admin Telegram bot (Persian UI) that generates SFW anime character images through a replaceable provider, lets the admin curate them, and publishes approved images to one configured channel, manually or on a schedule.

**Environment (initial):** Debian on WSL2, 16 GB RAM, Intel i5-12400, no usable GPU inside WSL.

**Companion file:** `AGENT.md` holds the working rules. This file holds what to build.

**How to read this document:** numbers marked *starting default* are guesses to be tuned after measurement, not rules. Items marked **[VERIFY]** are external facts written from memory that must be checked against official documentation or a real test before the code depends on them.

---

## 1. Goal, scope, and success

### 1.1 Core workflow

`generate a batch (e.g. 4 images) → admin reviews → approved images enter a pool → published manually, or by the scheduler from the pool only`

Approval happens once per image, at review time. Nothing unreviewed is ever published, including by the scheduler.

### 1.2 In scope for the MVP

- A replaceable image-provider interface; the Perchance text-to-image page is the first candidate, not a permanent dependency.
- Deterministic, free prompt construction from templates and preset pools.
- Stored characters with a fixed written identity, separate from scene choices.
- Image extraction, validation, hashing, and atomic storage.
- A Telegram admin flow: generate, review, approve or reject, save character, publish.
- A durable job queue, then a scheduler that only enqueues and posts from the approved pool.
- Persian UI strings from `locales/fa.yaml`.
- Logs, safe errors, disk limits, and recovery procedures.

### 1.3 Out of scope (until separately approved)

Conversational characters, per-user memory, images generated from chat, public multi-user access, payments or paywalls, NSFW, model training or a local diffusion model, automatic image scoring, a web dashboard, and Ollama or any LLM in the prompt path.

### 1.4 MVP success criteria (the user should confirm or change these)

The MVP is worth continuing only if, in the pilot, all hold:

- At least 70% of generation jobs produce one or more images that pass validation (*starting default*).
- At least 1 in 4 generated images is judged postable by the admin (*starting default*).
- Curation takes the admin no more than about 10 minutes per day at the target posting rate (*starting default*).
- The bot runs unattended for the pilot period without runaway generation, duplicate posts, or disk growth.

If these fail, the likely cause is the provider or the consistency approach, and the decision is to change the provider, not to add features.

---

## 2. Assumptions to verify before building on them

| # | Assumption | Why it matters | How to verify |
|---|---|---|---|
| A1 | Perchance support's written approval covers this page, this automation method, this rate, and visible or headless mode | Everything depends on it | The user supplies the written reply; the agent records a non-sensitive summary in `docs/provider-permission.md` |
| A2 | A visible Chromium window opens inside WSL2 (WSLg) | The proof-of-concept uses a visible browser | Sprint 0 check: confirm `DISPLAY` and that a window actually opens |
| A3 | Headless operation is permitted and works | A server deployment has no display | Only if A1 allows it; test separately after approval |
| A4 | WSL2 memory limit leaves room for Chromium plus Python | Default limits are a fraction of host RAM **[VERIFY]** | Measure peak RAM in Sprint 0; set the limit deliberately in `.wslconfig` |
| A5 | Telegram limits: photo about 10 MB, width plus height at most 10,000, aspect ratio at most 20:1, caption at most 1,024 characters, `callback_data` at most 64 bytes | Publishing and callbacks must respect them | **[VERIFY]** against the current Bot API documentation |
| A6 | Library APIs: `python-telegram-bot` lifecycle hooks, SQLAlchemy 2.x async, Playwright async, APScheduler stable major | Wrong version examples cause subtle bugs | **[VERIFY]**; pin tested versions and follow the installed version's documentation |
| A7 | Perchance's terms allow publishing generated images to a public channel | Redistribution can differ from personal use | **[VERIFY]** in the provider's terms and the support reply |
| A8 | The provider's real output behavior: single final result or progressive images, formats, whether it returns a seed or accepts negative prompts | Shapes the provider interface and schema | Sprint 0 spike report |

---

## 3. Provider rules

- The existing Playwright proof-of-concept is a **reference only**. The agent reads it, never runs it, until Sprint 0 is approved. Its known weaknesses in `SPEC.md` section 9.3 come from the blueprint author's description; the agent must confirm each against the real script before relying on the list.
- Respect the actual scope of the provider's permission. Do not assume permission for another page, a higher rate, headless mode, commercial use, or public redistribution unless the written reply covers it.
- On a CAPTCHA, challenge, rate limit, or access-denied page, stop and notify the admin. Do not circumvent.
- Site-specific selectors live in one adapter module. Selectors are a baseline to verify, never an assumption.
- Live provider checks are manual and opt-in, kept in `tests/manual/`. CI uses the mock provider only.
- If the provider is a no-go (section 14, Sprint 0), the replacement shortlist is judged on: automation or API permission, terms for public posting and commercial use, rate limits, output quality for anime styles, seed or reference-image support, and cost. Verify current terms and limits for each before choosing; do not assume any candidate is acceptable.

---

## 4. Architecture

**Stack:** Python (record the exact version in the README), `python-telegram-bot` async, Playwright **async** API, SQLAlchemy 2.x async with `aiosqlite`, Alembic from the first schema, Pillow for validation, `pytest`, `ruff`. APScheduler is added only in Sprint 4, pinned to the stable major version actually installed. **[VERIFY]**

**Process model:** one process, one event loop. The Telegram `Application` owns the loop. The generation worker is a single asyncio task started in the application's post-init hook and stopped in its post-shutdown hook. Handlers only write jobs and read state; they never touch the browser or the provider.

**Concurrency:** exactly one generation at a time. One database session per unit of work, never shared between tasks. Use `to_thread` only for genuinely blocking work such as Pillow on large images.

**Browser rules:**

- Async Playwright only; never mix it with the sync API in one lifecycle.
- Explicit timeouts on every operation; prefer locator auto-waiting over sleeps.
- Plain fill and click first. Forced clicks and character-by-character typing only if a controlled test proves they are required and the permission scope allows it.
- No bare exception swallowing; every failure gets a category.
- Restart the browser after a number of jobs (*starting default*: 10) and after any error, to contain memory growth.
- Cancelling an asyncio task is not proof that the browser operation stopped; rely on the provider's own timeout and cleanup.

**Deployment reality:**

- Development runs on WSL2. WSL is not an always-on host; the bot stops when the PC sleeps or WSL shuts down. Document this.
- A server deployment has no display, so it needs headless operation. That depends on assumptions A1 and A3. If headless is not permitted, an always-on deployment is not possible with this provider, and that is a reason to change provider, not to hide the browser.
- Treat the always-on question as an explicit decision at the end of the pilot.

**Settings and secrets:**

- Secrets and hard limits come from environment variables: bot token, admin IDs, provider enabled, NSFW disabled, maximums, destination chat ID.
- Admin-tunable values (posting interval, daily cap, images per job, default style, retention) live in the `settings` table, each with a code-defined type, range, and default.
- A database value can never override a hard limit or enable a disabled provider.
- `.env.example` contains placeholders only.

---

## 5. Data model (5 tables)

UTC timestamps everywhere; local time only in the UI. Foreign keys enabled. Add indexes only where listed.

**`characters`** — `id`, `name`, `identity_prompt` (fixed visual identity), `style_lock`, `version`, `parent_id` (previous version, nullable), `is_active`, `created_at`.
Identity text is immutable per row. A change creates a new row pointing to its parent.

**`jobs`** (durable queue) — `id`, `status` (`pending`, `running`, `succeeded`, `failed`, `cancelled`), `priority` (manual 1, scheduled 10), `filters_json` (request snapshot), `prompt_snapshot`, `template_version`, `character_id` (nullable), `requested_by`, `idempotency_key` (unique), `attempt_count`, `max_attempts`, `next_attempt_at`, `error_code`, `error_summary` (no secrets), `created_at`, `started_at`, `finished_at`.
Index on `(status, priority, created_at, id)`.

**`images`** — `id`, `job_id`, `character_id` (nullable), `provider`, `file_path` (relative, under managed storage), `mime_type`, `width`, `height`, `file_size`, `sha256`, `status` (`pending_review`, `approved`, `rejected`, `published`, `missing`), `seed` (nullable; stored only if the provider genuinely returns it), `created_at`.
Index on `sha256`; unique per job.

**`posts`** — `id`, `image_id`, `chat_id`, `telegram_message_id` (nullable), `status` (`pending`, `sent`, `failed`, `unknown`), `attempt_count`, `last_error`, `created_at`, `sent_at`.
Unique on `(image_id, chat_id)`. The destination comes from configuration.

**`settings`** — `key`, `value_json`, `updated_at`, `updated_by`.

**Deliberately not in the MVP:** a `destinations` table (one channel in config), `character_references` (add when the provider supports reference images), `admin_audit` (structured logs cover a single admin), and content-rating columns (everything is SFW).

**Design notes:**

- Provider-dependent fields (`seed`, dimensions, formats) are nullable or derived from validated files, so the schema does not assume what the provider returns. Sprint 0 findings are applied before the schema is frozen.
- Deduplication by SHA-256 only prevents re-collecting the same image while polling one page in one job. Separate generations are almost never byte-identical, so it does not detect similar images. Perceptual hashing is a later option.

---

## 6. Prompt engine and character identity

**Deterministic and free.** No paid API, no LLM.

Flow: load character version → load style lock → select compatible presets (admin-chosen or weighted random) → validate combinations → render the provider-specific template → run content checks (section 8) → store the exact prompt, preset IDs, and template version in the job.

**Presets** live in `config/tags_pool.yaml`: stable IDs, labels, prompt fragments, optional provider mappings, compatibility lists, optional weights. Malformed YAML fails loudly at startup and never falls back silently. Labels are localizable; prompt fragments need not be Persian.

**Prompt structure:** fixed identity, then fixed style, then variable scene (pose, expression, outfit, background, camera, lighting), then provider-supported quality constraints. Wording is adapted to the provider after Sprint 0 shows which fields it supports.

**Consistency, honestly.** Repeating the same text makes the *instructions* consistent, not the *face*. Strategy, in order:

1. Use a provider-supported seed or reference image if one exists.
2. Otherwise use a fixed, rich identity block (hair, eyes, distinctive marks) and a locked style, in the same position in every prompt.
3. If results fall below the threshold, the fix is a different backend that supports reference conditioning, for example a self-hosted workflow on a GPU host. That is a separate approved decision, not a prompt tweak.

**Study protocol:** 20 images of one canonical character across varied poses, outfits, and backgrounds. A human rates each 1–5 on face shape, hair, eyes, distinctive features, and style. **Set the pass threshold before reviewing results.** Proposed *starting default*: at least 70% of images average 4 or more, and none below 3 on hair or eyes. Below that, call it "approximate consistency".

**Save Character** works only from real job metadata: read the job's prompt snapshot and presets, ask the admin for a name, show the proposed identity text for confirmation or editing, then save a new version. Never infer a prompt from pixels. Externally supplied images are stored as manual references with any description marked as user-confirmed.

---

## 7. Presets and captions

- Persian captions are built from templates in `fa.yaml`, with placeholders filled from stored data. Check mixed Persian and English text visually, because right-to-left rendering of placeholders can misorder punctuation.
- Presets are data, not logic. Keep the style lock separate from scene variables.
- Incompatible combinations are rejected, not silently mixed.

---

## 8. Content safety

- SFW only. The NSFW switch is a hard environment setting with no Telegram override.
- **Human review is the primary control.** Every image is reviewed by the admin before it can reach the pool, and the scheduler posts only from the pool. Automated checks reduce risk but do not guarantee safety: a text rule cannot make a provider draw an adult, and keyword lists can be bypassed.
- Every character must be explicitly adult. `identity_prompt` must state an adult age and adult features, validated when a character is saved.
- A configurable blocklist of minor-coded terms is applied to the **final** prompt, including any admin-typed text. A match rejects the job with a clear error.
- A random SFW/NSFW ratio is never a safety control.
- Existing-IP characters ("known" characters) are excluded from the MVP; publishing derivative images of trademarked characters to a public channel is a separate question.
- Any future adult-content phase requires a separate review of provider permission, platform rules, access control, age assurance, and applicable law.

---

## 9. Provider interface and image pipeline

### 9.1 Interface (conceptual)

`health_check()`, `generate(request)`, `close()`. The request carries prompt, image count, style mapping, timeout, and safe metadata. The result carries status, validated asset metadata, elapsed time, provider name, job ID, and a normalized error code. The shared interface lives in `core/image_provider.py`; the Perchance adapter in `core/providers/`. The Telegram layer never knows about the site's DOM.

**The interface is frozen only after the Sprint 0 report is read.** If the provider delivers images progressively rather than as one final result, the interface must allow that. The mock provider is shaped by the real behavior, and can simulate timeouts, malformed data, partial results, and challenge pages.

### 9.2 Per-candidate validation

Parse the Data URL and MIME type → decode Base64 in Python → SHA-256 over the decoded bytes → open and verify with Pillow → record real format, width, height, size → enforce configurable minimum and maximum limits → write to a `.part` file and rename atomically → use a generated UUID filename, never page-supplied text → only then mark the image `pending_review`. A partial or failed file is never visible as ready.

### 9.3 Known weaknesses of the proof-of-concept (to confirm against the real script)

- A built-in `hash` of the last 200 characters is not a stable fingerprint; use SHA-256.
- Saving every image as `.jpg` ignores the real format; derive the extension from validation.
- Blanket exception swallowing hides systematic failure and makes a broken page look like waiting.
- Scanning every canvas picks up UI canvases and duplicates.
- Re-sending large Base64 payloads on a short poll interval wastes memory.
- Parsing a seed from an image title is unreliable unless documented.
- Fixed sleeps, forced clicks, and automation launch flags are not production behavior.
- Target count and timeout are hardcoded; they must be configuration with hard maximums.

---

## 10. Jobs, worker, and scheduler

**Claim rule (SQLite):** the worker claims a job with a conditional update that sets `running` only where the job is still `pending`, and proceeds only if exactly one row changed. With one process and concurrency 1 this is sufficient.

**Order:** priority ascending, then created time, then ID.

**Transitions** are validated in one place: `pending → running → succeeded | failed`; `pending → cancelled`; `failed → pending` for a scheduled retry only while attempts remain. Generation and review are separate: a job that produced images is `succeeded`; review state lives on `images`.

**Retries (*starting defaults*):** at most 2 attempts, backoff 30 seconds then 120 seconds, transient errors only. Permanent errors and `blocked_by_provider` never retry.

**Circuit breaker (*starting default*):** after 3 consecutive provider failures, pause automatic generation and notify the admin. Only a person resumes.

**After a restart:** any job left `running` becomes `failed` with `error_code=interrupted` and is **not** retried automatically; the admin can requeue it. Startup also cleans up what that job left behind: `.part` files in temp storage, and image rows that point to missing files are marked `missing`.

**Idempotency:** the request key is derived from the Telegram update or callback ID, so a double tap cannot create two jobs.

**Scheduler (Sprint 4):** enqueue-only. Stable job IDs with replace-on-restart, a single running instance per job, coalescing on, and a short misfire window so downtime never causes a burst. Jobs: `auto_gen` (enqueue one if enabled and under the daily cap and disk floor), `post` (publish the next approved image), `cleanup`, `health`. The daily cap is counted from the database, never from memory. Health checks never generate an image.

---

## 11. Telegram admin flow

**Access:** admin IDs from `ADMIN_USER_IDS`. Authorize on the server for every command and callback; a hidden button is not authorization. Reject others silently with a safe log line. No public user flow and no user handler in the MVP.

**Commands:** `/start`, `/help`, `/status`, `/generate`, `/inbox`, `/queue`, `/pool`, `/publish`, `/settings`, `/pause`, `/resume`, `/characters`.

**Generation wizard (short):** style → character (or one-off original) → image count → confirm → job ID. Anything left unchosen is randomized.

**Review inbox:** each finished image is sent to the admin as a preview with Approve, Reject, and Save Character. Approved images enter the pool. `/pool` lists approved, unpublished images; `/publish` lets the admin pick one from that list to post now. The scheduler posts the oldest approved image on the interval.

**Callbacks:** carry a short opaque ID (the length limit is small, A5). On every press, re-validate the caller, the object, and its current state. A stale or tampered callback does nothing.

**Publishing:**

- Check file type and size against the limits (A5) before sending. Photo sends recompress; use a document send when the original must stay lossless.
- Generation and publication are separate; a send failure never triggers regeneration.
- Every attempt is recorded in `posts`. If a send times out and the outcome is ambiguous, set `unknown` and notify the admin. The admin resolves it with two buttons: "it was sent" (the admin supplies or confirms the message) or "it was not sent" (back to `pending`). Never auto-resend. The unique key prevents scheduling the same publication twice but cannot promise exactly-once delivery.
- A spoiler flag is only a blur effect, not a paywall.

**i18n:** every user-visible string, including buttons and callback answers, comes from `locales/fa.yaml`. A startup check fails on missing keys. Logs stay in English.

---

## 12. Storage, security, and operations

- `storage/temp/` holds `.part` files and previews with a short TTL (*starting default*: 24 hours). `storage/images/` holds accepted images.
- Cleanup deletes only expired temp files and confirmed orphans. Final images follow a separate configurable retention. An image referenced by a character, an unpublished approved image, or a post is never deleted by TTL.
- Check free disk before every generation. Below a hard floor (*starting default*: 2 GB), pause generation and notify the admin.
- Rotate logs; keep job history bounded.
- Never log Base64 data, cookies, tokens, or full environment dumps. Log job ID, provider, duration, attempt, result, and normalized error code.
- Keep the bot token only in the environment; restrict file permissions on `.env`, the database, the browser profile, and storage; ignore them in Git. If the token is ever exposed, revoke and replace it.
- Graceful shutdown: stop accepting jobs, let the active job finish or time out, close the browser, commit final states.
- The README documents setup, configuration, start, stop, recovery, backup, and restore of the database and character data.

---

## 13. Testing

The test environment has **no network**: block outgoing connections in tests so nothing can reach a provider or Telegram by accident.

**Unit tests:** prompt building and exact identity preservation; preset compatibility and malformed YAML; settings validation and hard limits; locale loading and missing keys; job transition table and retry policy; Data URL parsing, MIME mismatch, malformed bytes, size limits; atomic save with no partial file left; cleanup never deleting a referenced file; blocklist and adult-character validation; admin-only authorization and callback ownership.

**Integration tests (mock provider, temporary SQLite):** a job runs end to end and images land as `pending_review`; foreign keys and uniqueness behave; a duplicate request creates one job; a stale `running` job becomes `interrupted` after restart and its leftovers are cleaned; a provider timeout retries a bounded number of times; a failed send does not regenerate; an ambiguous send becomes `unknown` and both resolution buttons work.

**Manual live checks (opt-in):** one visible-browser smoke run, then controlled generations at the permitted rate. Record success rate, median and maximum runtime, valid image count, validation errors, and peak RAM and disk. Test headless mode only if permitted.

---

## 14. Sprint plan and gates

### Sprint 0 — Provider feasibility spike (explicit approval required; live browser)

Throwaway scripts under `tools/spike/`, not application code. The user places the existing proof-of-concept and the written support reply where the agent can read them.

- Read the proof-of-concept and confirm or correct the weakness list in section 9.3.
- Record the permission scope in `docs/provider-permission.md`; **[VERIFY]** A1 and A7.
- Check A2 (window opens under WSL2) and A4 (memory) on the real machine.
- Run controlled generations, **no faster and no more than the permitted rate** (*starting default*: 10 runs, spaced out). Record success rate, runtime, formats, validity, and RAM. Resolve A8.
- Test headless only if A1 permits it.
- Decide go or no-go. On no-go, shortlist replacements using the criteria in section 3.

**Exit:** a one-page spike report with a decision. Stop.

### Sprint 1a — Foundation (no browser, no network)

- Repository structure, `.gitignore`, `.env.example`, configuration validation, runtime directories, README setup.
- The 5 tables, Alembic baseline migration, schema inspection command, relationship and constraint tests.
- Locale loader and startup key check.

**Exit:** clean checkout sets up; schema documented and tested; tests pass. Stop.

### Sprint 1b — Vertical slice with the mock provider

- Provider interface, shaped by the spike report, and the mock provider.
- Preset loader, deterministic prompt builder, content-safety checks.
- Image validation and atomic-save pipeline.
- CLI: initialize database, add a character, run a mock generation.

**Exit:** the CLI creates a job and stores validated mock images; unit tests pass; the interface decisions are explained against the spike findings. Stop.

### Sprint 2 — Real Playwright provider

- Adapter using the async API, selectors isolated, hard timeouts, explicit configuration gate.
- Reuse the 1b pipeline. Add the manual live command.
- Small live smoke test through the real adapter (*starting default*: 3 runs), not a repeat of the full measurement, within the permitted rate and with notice to the user beforehand.

**Exit:** real images validated and saved; timeouts and page changes produce actionable errors with no uncontrolled retries. Stop.

### Sprint 3 — Telegram admin bot (manual flow)

- Application setup, admin authorization, Persian commands and wizard.
- Worker inside the bot lifecycle, durable jobs, the claim rule.
- Preview, approve, reject, `/inbox`, `/pool`, manual `/publish` to a **test** destination, post state and message ID, ambiguous-send resolution.

**Exit:** an admin requests, reviews, approves, and publishes to the test destination; state survives restart. Stop.

### Sprint 4 — Automation

- Scheduler, daily cap, disk floor, pause and resume, circuit breaker.
- Retry and backoff, stale-job recovery and cleanup, safe settings updates.
- Scheduled posting from the approved pool.

**Exit:** a restart or transient error never causes duplicate or runaway generation; caps hold under repeated firing. Stop.

### Sprint 5 — Characters and consistency

- Versioned characters, Save Character from real job metadata, restart persistence.
- The 20-image consistency study with the threshold set beforehand.
- Decision: acceptable, or escalate to a reference-capable backend.

**Exit:** documented result and decision. Stop.

### Sprint 6 — Pilot (separately approved)

A 24-hour or agreed pilot against the section 1.4 criteria, with CPU, RAM, and disk measurement; backup and restore drill; network and provider failure drills; disk-pressure auto-pause; a decision on always-on deployment.

---

## 15. Definition of done

- [ ] Setup, start, stop, and recovery are documented for the target environment.
- [ ] Every user-facing string comes from `locales/fa.yaml`.
- [ ] The provider is behind an interface and can be disabled by configuration.
- [ ] Permission scope is recorded and respected; live runs stayed within the permitted rate.
- [ ] Candidate images are decoded, validated, hashed, and saved atomically.
- [ ] Prompt generation works with no LLM and no paid API; fixed identity text is preserved exactly.
- [ ] A saved character can be selected after a restart.
- [ ] Jobs survive restart; stale-job recovery and leftover cleanup are tested.
- [ ] An admin can request a job, review it, and publish an approved image to a test destination with state recorded.
- [ ] Duplicate requests are blocked; ambiguous sends are handled and resolvable.
- [ ] Scheduler limits, pause and resume, and cleanup rules are tested; cleanup never deletes needed files.
- [ ] Provider, Telegram, database, disk-pressure, and restart failures have tests or documented manual checks.
- [ ] Every **[VERIFY]** item is resolved and recorded.
- [ ] The section 1.4 criteria were evaluated in the pilot.
- [ ] No out-of-scope feature was built.

---

## 16. Decisions needed from the user

**Before Sprint 0 (blocking):** the written Perchance support reply (page, method, rate, visible or headless, public posting); the proof-of-concept script; whether WSLg works on this machine.

**Before Sprint 3:** admin Telegram IDs and a test channel ID.

**Before Sprint 4:** posting interval, daily cap, images per job, retention for final images, and whether captions are Persian only.

**Worth deciding early:** whether the Windows host has a GPU for a reference-capable backend, whether the channel is public or commercial, and whether the section 1.4 success criteria are the right ones.

---

## 17. Future phases and their preconditions

Each phase needs its own scope and approval.

- **Chat and personality:** choose and budget an LLM first; the free-provider setup cannot carry it.
- **Per-user memory:** consent and deletion controls designed before anything is stored.
- **Images from chat:** reuse the same character record; requires a stable consistency result.
- **Credits, subscriptions, paid media:** the provider's terms must allow commercial use. A free provider driven through browser automation almost certainly cannot underpin a paid product, so this phase probably means a different image backend. Re-check the current Telegram Bot API and library version, and test payment verification, idempotent fulfillment, refunds, and access control.
- **Adult content:** only after legal, platform, provider, and age-assurance review; never involving minors or age-ambiguous characters.

---

## Final principle

Prove the riskiest assumption first, then build the shortest measurable path: **feasibility spike → foundation → mock-provider vertical slice → real provider → Telegram review and manual publish → scheduled posting from the approved pool → consistency study and pilot.** Keep a layer only when something real needs it.
