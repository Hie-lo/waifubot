# Provider interface — decisions vs. Sprint 0 spike findings

The interface (`src/waifubot/core/image_provider.py`) is frozen after the Sprint 0
report (`docs/spike-report.md`). How each decision maps to measured behavior:

| Spike finding | Interface decision |
|---|---|
| Images arrive progressively during a bounded polling window (successful runs took 95–125 s to collect 4 images) | `generate()` collects candidates until the target count or timeout and reports `ok` / `partial` / `timeout` — progressive-capable by design (SPEC §9.1) |
| Plain automation gets blocked; a challenge page is permanent | `blocked` status with error_code `blocked_by_provider` — a permanent outcome, never retried (SPEC §10). The job row records it and fails |
| Outputs are JPEG; formats must never be assumed | Candidates carry only `claimed_mime`; real format/dimensions/size come from Pillow validation in `core/image_pipeline.py` |
| No genuine seed was ever observed on Perchance | `ImageCandidate.seed` is optional and stored only when genuinely returned (the mock returns seeds; Perchance adapter will leave it `None` unless the page truly provides one) |
| 6 candidates → 4 valid (validation/dedupe filtered 2) | The pipeline, not the provider, owns validation, SHA-256 dedupe, and atomic saves; rejection reasons are categorized |
| Provider page must be switchable | All page URLs, selectors, style values, and the approved launch profile live in `config/providers/perchance.yaml`; the factory is config-driven (`name: mock` vs `name: perchance_playwright`) |

The Perchance adapter (`perchance_playwright`) is **implemented in Sprint 2**
(`core/providers/perchance.py`, async Playwright) with the user-approved launch
profile and human-paced typing (config-driven pacing in `config/providers/perchance.yaml`).
Live execution happens only through `python -m waifubot.cli live-smoke --confirm-live`.
The Selenium comparison probe (`tools/spike/selenium_compare.py`, throwaway) covers the
"test both tools" decision; production stays on async Playwright (already proven live).
The mock provider simulates every outcome observed or feared: `ok`, `partial`,
`malformed`, `blocked`, `timeout`, `error`, `empty`.
