# ADR-0006 — Plans, quota reserve/settle, single superadmin short-circuit

**Status:** accepted (v4.1)

**Context.** Plain users now use the bot (R51) within limits set by admins (R52), and the superadmin must have no limits (R53). Limits touch generation, chat, characters and channels; counting in each handler would drift and race.

**Decision.**
- Limits are a pure value object (`core/limits.py`, `None` = unlimited). `effective_limits(role, plan, overrides)` is the **only** place that looks at the role for limit purposes: it returns `Limits.unlimited()` for the superadmin.
- `RequestContext` is built once per update in `bot/auth.py`.
- All entry points call `check_and_reserve(ctx, kind, amount)`, executed in the same DB transaction as the guarded action. Generation **reserves** at `create_job` and **settles** at the exact-count gate (refunding the difference), so users are charged only for delivered images; failures and cancellations refund fully.
- Plans are data (`plans` table, built-in `free` and `staff`), assignable with expiry and per-user overrides; lowering a limit never deletes existing assets.

**Alternatives.** Charging on request (unfair with R9 top-ups), charging on completion only (allows overspending while jobs queue) — both rejected.

**Consequences.** One place to audit limits; property tests can prove `settled + refunded == reserved`. Payments are explicitly deferred.
