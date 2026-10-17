# ADR-0001 — Adapters live outside `core/`

**Status:** accepted (v4.1)

**Context.** v4.0 claimed `core` imports no frameworks, yet `core/providers/captcha` (Playwright), `core/http.py` (HTTP client) and `core/ai_enhance.py` (Anthropic SDK) lived inside it. The rule was true only by convention.

**Decision.** `core` holds models, pure logic and **ports** (`core/ports.py`). Every implementation that touches a library or the network lives in `adapters/`. `import-linter` contracts enforce this in CI. `bot` reaches adapters only through the composition root in `bot/app.py`.

**Consequences.** Providers, enhancer and storage are swappable with zero core change; core tests need no mocks of third-party libraries. Cost: one-time move of modules (Stage 2) and import updates.
