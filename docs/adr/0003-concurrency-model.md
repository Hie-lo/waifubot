# ADR-0003 — Concurrency model: single-job workers, slot pools, `min()` limits

**Status:** accepted (v4.1)

**Context.** Jobs were not truly concurrent, and the v4.0 ceilings (20 jobs, 100 workers) said nothing about browser cost: each Chromium instance costs hundreds of MB. Furthermore, long-running Chromium browser contexts accumulate script, DOM, and cache memory leaks over multi-day runtimes on dynamic generation sites.

**Decision.**
- A worker runs one job at a time; concurrency comes from running several workers (`WorkerPool`, TaskGroup, live resize).
- Provider resources are acquired as exclusive **slots** (`ProviderSlotPool`). Browser providers use one Chromium with one isolated `BrowserContext` per slot; warm contexts persist storage state so sessions are reused across jobs but never shared concurrently.
- **Context recycling:** To prevent progressive memory bloat, each slot tracks completed jobs. After `slot_max_jobs` (default 25), the slot automatically exports its `storage_state`, completely disposes of the old `BrowserContext`, and instantiates a clean context restoring the saved session state.
- **Challenge rendering via WSLg:** Worker slots run headless. When a challenge occurs, the `ChallengeGate` serializes execution (one window at a time), exports the triggering slot's `storage_state` into a headed tab rendered directly to Windows via WSLg display sockets (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`), and syncs the solved session state back to the slot.
- Effective concurrency = `min(worker_count, max_concurrent_jobs, max_browser_contexts, provider_concurrency)`. Ceilings stay 100 / 20 / 20; defaults are small and measured.
- A soft `MemoryGuard` delays opening new slots when free RAM is low; it never lowers the configured ceilings.
- Fair claiming: oldest first, round-robin across admins.

**Alternatives.** One browser per job — rejected: RAM. Shared page with parallel generations — rejected: gallery cross-contamination (R10). Unbounded context lifespans — rejected: gradual OOM crashes.

**Consequences.** Real parallelism with strict process isolation and bounded RAM over multi-day runtimes.