# ADR-0002 — Atomic claim with lease and heartbeat

**Status:** accepted (v4.1)

**Context.** v4.0 recovered interrupted jobs only at startup. A worker stuck or crashed mid-run left its job `running` until the next restart, and two workers could in theory claim the same job.

**Decision.** Claiming is one atomic statement inside `BEGIN IMMEDIATE` (`UPDATE … RETURNING`). A running job holds `lease_owner` + `lease_expires_at`, renewed by a heartbeat every `heartbeat_seconds` (lease TTL starting default 60 s). A reaper loop requeues expired leases, counts one attempt and announces it (R13). Graceful shutdown releases leases without consuming attempts.

**Alternatives.** External queue (Redis/Celery) — rejected: extra service on WSL2 for a single-node bot. Startup-only recovery — rejected: leaves stuck jobs.

**Consequences.** Self-healing queue; PostgreSQL portable via `FOR UPDATE SKIP LOCKED`. Needs SQLite WAL + `busy_timeout` (ADR-0005).
