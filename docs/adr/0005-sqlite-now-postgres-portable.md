# ADR-0005 — SQLite (WAL) now, PostgreSQL-portable

**Status:** accepted (v4.1)

**Context.** Single-node bot on WSL2; SQLite is enough, but concurrent workers, heartbeats and the bot all write. Upgrading immediately to a dedicated PostgreSQL daemon introduces 200–400 MB of permanent RAM overhead on a machine constrained to 16 GB where Chromium browser slots already compete for memory. However, SQLite concurrency under high worker/chat write volume risks `SQLITE_BUSY` contention if not carefully controlled.

**Decision.** Keep SQLite with `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`; short single-purpose transactions; `BEGIN IMMEDIATE` for claims; Alembic batch mode. Use only portable SQLAlchemy types and no SQLite-specific JSON functions.

To eliminate `SQLITE_BUSY` lock contention under concurrent chat and worker load:
1. **Zero I/O inside transactions:** Network calls (HTTP/Telegram/Anthropic), sleeps, and CPU-intensive file/image operations are strictly forbidden inside open `session_scope` transactions.
2. **In-Memory UI State & Heartbeat Cache:** High-frequency, ephemeral UI coordinates (`PanelMemory` message IDs, active panel version nonces, per-user debounce locks) are maintained in-memory and only flushed to `settings` when durability across bot restarts is required.
3. **Heartbeat Jitter & Dynamic Throttling:** Workers apply $\pm 20\%$ randomized jitter to `heartbeat_seconds` to prevent lockstep DB write contention across workers. Redundant heartbeat writes are skipped if remaining lease TTL exceeds safety margins and no job progress counters changed.
4. **Exponential Backoff Busy-Retry Wrapper:** All write transactions wrap execution in an async retry interceptor (`tenacity` or custom decorator) handling `sqlite3.OperationalError` (`database is locked` / `SQLITE_BUSY`) with up to 5 attempts and randomized exponential backoff (50ms base).
5. **Periodic Passive Checkpoint & Auto-Vacuum:** On connection, `PRAGMA wal_autocheckpoint=1000` is enforced, complemented by an idle background task executing `PRAGMA wal_checkpoint(PASSIVE)` to bound WAL file growth during heavy concurrent writes.
6. **Turn-End Counter Batching:** Chat token usage and message counters accumulate in-memory per session and are flushed in a single transaction at turn completion rather than per-chunk or per-message.
7. **Database Migration Threshold:** Migrate to PostgreSQL only when heartbeat and claim contention consistently exceed latency budgets in `/status` metrics, or when horizontal scaling across multiple physical nodes is required.

**Consequences.** Simple, zero-overhead operations with maximum available RAM for browser slots; moving to PostgreSQL later remains a zero-refactor transition (changing `DATABASE_URL` and swapping the claim query for `FOR UPDATE SKIP LOCKED`).