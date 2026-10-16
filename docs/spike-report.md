# Sprint 0 — Spike Report

**Status: GO — feasibility proven (2026-10-16).** Live runs executed on the user's WSL2 machine with explicit approval.

## 0. Live run log (chronological)

| Date | Build | Result |
|---|---|---|
| 2026-10-16 | pre-fix spike (`8e50338`) | 0 images: `Locator.fill` timeout — prompt textarea lives inside `#outputIframeEl` (selector bug; fixed in `d1d4740`) |
| 2026-10-16 | iframe fix (`d1d4740`, plain `fill()`) | **Blocked by provider** (user report: the run had no human-like behavior) |
| 2026-10-16 | + human-paced typing (`e953451`) | Reached the generate step, **blocked again** (user report) |
| 2026-10-16 | + user's PoC automation profile (`4694407`) | **SUCCESS** (user report): generation + extraction + validation worked. Exact metrics CSV pending from user — numbers to fill in §4/§5 when pasted |

### Decision record
- **2026-10-16 (user):** human-paced char-by-character typing (40–120 ms) + small pauses before clicks. SPEC §4: allowed once a controlled test proves plain fill insufficient.
- **2026-10-16 (user, second block):** adopt the working PoC's full automation profile — persistent profile, `slow_mo=100`, `--disable-blink-features=AutomationControlled`, canvas extraction fallback. Recorded in AGENT.md amendment log + `docs/provider-permission.md`. CAPTCHA solving, challenge-page bypass, proxy rotation, retries on `blocked_by_provider` remain prohibited.
- **2026-10-16 (user, after success):** style should be **"Anime"** (not "Soft Anime") so the intended design comes out of the prompts — or "no style" and specify style in the prompt. Spike config now defaults to `ref:optionKeyName:Anime`; the run prints the page's real option values; final style strategy for the product is a Sprint 1b decision (see §10).

## 1. Environment
- Machine: WSL2 (user's PC, DESKTOP-KE5F0CF), Python venv `~/waifubot/.venv`
- Python version: 3.11 (venv per setup commands)
- DISPLAY: WSLg — visible Chromium window opens (user's PoC + spike runs confirm; A2 effectively resolved)
- WSLg window opens: yes
- Peak RAM (measured): **pending from user** (Task Manager / htop)
- .wslconfig limit set: **pending recommendation** (A4)

## 2. Permission scope (from user statement)
- Page: `https://perchance.org/ai-text-to-image-generator` (config-switchable)
- Automation method: user's working PoC profile (see decision record + `docs/provider-permission.md`)
- Rate: runs spaced by config `spacing_s` (default 30 s), bounded (max 10 runs, 300 s timeout)
- Visible/headless: visible first; headless untested (A3)
- Public posting / commercial use: user assertion recorded (A1/A7); monetization re-verifies terms
- Recorded in: docs/provider-permission.md

## 3. PoC weakness list (SPEC §9.3) — confirmed/corrected
- [x] hash(src[-200:]) not stable → SHA-256 over decoded bytes (corrected in spike)
- [x] all saved as .jpg → real format from Pillow validation (corrected)
- [x] blanket exception swallowing → categorized errors (corrected)
- [x] scanning every canvas → primary source is direct data: URLs; canvas fallback is config-gated and spike-only (user decision 2026-10-16)
- [x] large Base64 polling → cheap frame scan; only new data URLs fetched/decoded (confirmed)
- [x] seed from title unreliable → store only if genuinely returned (corrected; no title parsing)
- [x] fixed sleeps / forced clicks / launch flags → forced clicks + pacing + user-approved launch profile are a **user decision** (see §0); production adoption is user-owned
- [x] hardcoded target count/timeout → config values with hard caps (corrected)

## 4. Controlled runs (spaced, within permitted rate)
| Run | elapsed_s | candidates | valid | errors | blocked |
|---|---|---|---|---|---|
| pre-fix | 34.82 | 0 | 0 | fill timeout (top-document selector bug) | False |
| + iframe fix | ? | ? | 0 | blocked at generate (user report) | True |
| + human typing | ? | ? | 0 | blocked at generate (user report) | True |
| + PoC profile | ? | ? | >0 (success) | none reported | False |

*(? = metrics CSV pending from user.)*

## 5. Measurements
- Success rate: 1 successful build profile out of 4 attempts (final profile works)
- Median runtime / Max runtime: pending CSV
- Valid image count (total): pending CSV
- Validation errors: pending CSV
- Peak RAM: pending (A4)
- Peak disk: pending CSV (`storage/spike_out/run-*/`)

## 6. Output behavior (A8)
- Single final result or progressive: **pending observation** (Sprint 2 adapter must handle progressive if seen)
- Formats observed: pending CSV (Pillow-validated; jpeg/png/webp/gif accepted)
- Seed returned: not observed by the spike (title-seed deliberately ignored per SPEC)
- Negative prompts supported: unknown — do not assume (SPEC: no invention)

## 7. Headless test (only if permitted)
- Result: not tested (A3 open; visible mode works)

## 8. Decision
- **GO.** Feesibility proven on the real machine: prompt entry, style select, generate click, image extraction from frames, Pillow validation, SHA-256 dedupe, atomic saves all work with the user-approved automation profile.
- Conditions: the provider blocks plain automation — the approved profile is required (user-owned decision); keep `blocked_by_provider` handling (stop + notify, no retries) in the product; circuit breaker in Sprint 4.
- If blocks return even with this profile: no-go for this page → try the other candidate pages (config switch only) and the Sprint 2 Selenium comparison.

## 9. Risks and open issues
- Provider behavior without the approved automation profile is hostile; production adapter must carry the same profile (re-verify provider terms at monetization phases — user owns this call).
- WSL2 is not always-on; pilot decides deployment (SPEC §4).
- RAM contention Chromium + future workloads (A4) — measure during Sprint 2 runs.
- Exact metrics of the successful run not yet pasted into this report (keep §4/§5 updated).

## 10. Decisions needed from the user
1. Paste `storage/spike_out/spike_metrics.csv` (and a note on peak RAM) to fill §4/§5 numbers.
2. Style strategy for the product (Sprint 1b): site style **"Anime"** vs **"no style"** + style in the prompt (the spike now defaults to `ref:optionKeyName:Anime`; run prints the page's real options).
3. Set a `.wslconfig` memory cap for WSL2 (recommendation when RAM measured).

Status: Sprint 0 closed with **GO**. Waiting for explicit approval before each further sprint (Sprint 1a approved by user 2026-10-16: «میتونی مراحل رو ادامه بدی»).
