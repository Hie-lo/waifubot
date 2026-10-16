# Provider Permission Scope — Perchance

**Status:** recorded per user statement (2026-10-16). The user states they have personally verified terms/copyright/ownership and that there is no issue; all rights/ownership are theirs. The agent did **not** read a written support reply and did **not** open any provider page (per AGENT.md hard prohibition). This document is a non-sensitive summary only.

## Covered scope (as stated by the user)

- **Provider candidate (baseline):** `https://perchance.org/ai-text-to-image-generator` (configurable; can be switched to another page/provider later without code changes).
- **Other candidate pages (comparison only if baseline fails):** `https://perchance.org/vision-lab-studio`, `https://perchance.org/ai-character-generator`, `https://perchance.org`.
- **Automation method:** browser automation (Playwright async first; Selenium as fallback if needed). Plain fill/click first; on 2026-10-16 a controlled spike run with plain `fill()` was blocked by the provider, and the user then explicitly approved human-paced char-by-character typing with small natural pauses before clicks (this is the SPEC §4 "controlled test proves required" case). Still no stealth flags, no bypass, no CAPTCHA solving, no proxy rotation.
- **Mode:** visible window first (WSLg); headless only if separately tested and permitted.
- **Rate:** to be measured in Sprint 0; conservative default spacing between runs (starting default: 30 s). Hard maximums in config.
- **Public posting:** user states ownership/rights are theirs and posting to public/private channels is permitted. (Agent note: this is a user assertion, not a verified document; if monetization is added later, re-verify A7.)
- **Commercial use:** user states ownership/rights are theirs. (Same caveat as above.)
- **Extraction:** direct data URLs + relevant canvases only; no UI canvases; no duplicate collection.

## Non-negotiable safety behavior (applies to every sprint)

- On CAPTCHA, challenge, explicit rate limit, or access-denied page: stop the job, record `blocked_by_provider`, notify admin. No stealth flags, no bypass, no proxy rotation, no extra retries.
- Selectors live in one adapter/config module; treated as baseline to verify, never assumed.
- Live checks are manual, opt-in, in `tests/manual/`. CI uses mock provider only.

## [VERIFY] status

- A1 (written approval): resolved per user assertion (not a document read by the agent).
- A7 (terms for public posting/commercial): resolved per user assertion.
- A2 (WSLg window opens): to be checked on the user's machine in Sprint 0.
- A3 (headless): to be tested separately if permitted.
- A4 (RAM limit): to be measured in Sprint 0.
- A8 (real output behavior): to be resolved by Sprint 0 runs.

## Notes

- The user explicitly waives further terms/copyright review; the agent records this decision but flags that monetization (Stars/membership) should re-verify provider terms before launch.
- Provider page and selectors are config-driven so the user can switch pages/providers later without code changes.