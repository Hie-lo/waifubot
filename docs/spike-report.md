# Sprint 0 — Spike Report (to be filled)

## 1. Environment
- Machine: WSL2 (user's PC)
- Python version:
- DISPLAY:
- WSLg window opens: yes/no
- Peak RAM (measured):
- .wslconfig limit set:

## 2. Permission scope (from user statement)
- Page:
- Automation method:
- Rate:
- Visible/headless:
- Public posting:
- Commercial use:
- Recorded in: docs/provider-permission.md

## 3. PoC weakness list (section 9.3 of SPEC) — confirmed/corrected
- [ ] hash(src[-200:]) not stable → SHA-256
- [ ] all saved as .jpg → derive extension
- [ ] blanket exception swallowing → categorized errors
- [ ] scanning every canvas → relevant only
- [ ] large Base64 polling → cheap poll + fetch new
- [ ] seed from title unreliable → store only if genuinely returned
- [ ] fixed sleeps/forced clicks/launch flags → not production
- [ ] hardcoded target count/timeout → config with hard max

## 4. Controlled runs (max permitted, spaced out)
| Run | elapsed_s | candidates | valid | errors | blocked |
|---|---|---|---|---|---|

## 5. Measurements
- Success rate:
- Median runtime:
- Max runtime:
- Valid image count (total):
- Validation errors:
- Peak RAM:
- Peak disk:

## 6. Output behavior (A8)
- Single final result or progressive:
- Formats observed:
- Seed returned: yes/no
- Negative prompts supported: yes/no

## 7. Headless test (only if permitted)
- Result:

## 8. Decision
- Go / No-go:
- If no-go: replacement shortlist (criteria: permission, terms for public posting & commercial, rate limits, anime quality, seed/ref support, cost):

## 9. Risks and open issues

## 10. Decisions needed from the user

Status: waiting for explicit approval to continue.