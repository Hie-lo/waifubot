# AGENT.md — working rules for agents and humans

*Merge note: this file collects the process rules that used to sit inside the spec (ids unchanged) plus engineering standards. If a project-specific `AGENT.md` already exists, merge instead of overwriting.*

Product behavior is in `SPEC.md`; code layout is in `ARCHITECTURE.md`.

---

## 1. Process rules (ids are stable)

- **R29 — User word supersedes files.** If a live instruction contradicts a file, follow the instruction and report the conflict in the delivery report.
- **R30 — One sprint/stage at a time.** Deliver a short plain-Persian report with a real-test command, then **STOP** until approval. Large work is split into checklists (see `docs/MIGRATION-v4.0-to-v4.1.md`). Minimal error probability over speed.
- **R31 — Review pass after every stage:** dead code, unused locale keys, unrouted callbacks (registry test), count semantics (R9), secret scan, import-linter, pyright, ruff, tests green.
- **R32 — No live provider runs** in tests/CI (only via explicit `--confirm-live`); Playwright async everywhere.
- **R33 — Locale parity:** Persian-first, complete English; `REQUIRED_KEYS` enforced for both files, plus identical placeholder sets per key.

## 2. Engineering standards

**Environment & tooling**
- `uv` + `uv.lock`; Python version pinned in `.python-version`.
- `ruff check` and `ruff format`; `pyright` strict on `core/` and `services/`; `import-linter` contracts (ARCHITECTURE §3); `pre-commit` with `gitleaks`.
- CI runs lint → types → import-linter → pytest. No live providers.

**Code style**
- `Protocol` for ports, `StrEnum` for states, pydantic v2 at boundaries, `SecretStr` for secrets.
- `asyncio.TaskGroup` for background work; no fire-and-forget tasks. `async with` for every browser/slot lifecycle.
- Short DB transactions; one `session_scope` per unit of work; never hold a transaction across a provider call.
- Every user-facing string through `t(key, **fmt)`; every callback through a typed `CallbackData`.

**Testing**
- Every feature lands with tests; fakes for every port; golden file for prompt determinism.
- Property tests (`hypothesis`) for the exact-count gate and pipeline accounting.
- Concurrency tests for claiming, leases, resizing and shutdown.
- Never hard-code counts ("N tests", "N callbacks") in documents.

**Authorization, limits & privacy**
- Role, block status, plan and limits are derived once into `RequestContext`; never re-derive them in a handler or service. The superadmin's "no limits" lives only in `effective_limits`.
- Every quota-bound action goes through `check_and_reserve`; no handler counts on its own.
- Chat message text, persona text and memory never appear in logs, audit rows or admin screens.

**Secrets**
- Only in `.env`, never committed. On a leak: rotate first; scrubbing history is optional.

## 3. Definition of done (per stage)

1. Code + tests merged on a branch; full suite green.
2. Review pass (R31) clean.
3. `CHANGELOG.md` updated; `SPEC.md` / `ARCHITECTURE.md` updated if behavior or layout changed; ADR written if a design decision was made.
4. Plain-Persian report with the real-test command, then **STOP** (R30).

## 4. Delivery report template

```
مرحله: <نام>
چه چیزی تغییر کرد: <چند خط ساده>
تست واقعی: <دستور>
تعارض با دستور شما (اگر بود): <شرح>
منتظر تأیید شما هستم.
```
