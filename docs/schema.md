# Database schema (Sprint 1a baseline)

Five tables (SPEC §5). UTC timestamps; foreign keys enabled (`PRAGMA foreign_keys=ON`
is set on every SQLite connection by `waifubot.db.session.make_engine`).
Alembic baseline: `alembic/versions/0001_initial_schema.py`.

| Table | Purpose | Key constraints |
|---|---|---|
| `characters` | Stored characters, versioned | `parent_id` self-FK (previous version); identity text immutable per row |
| `jobs` | Durable generation queue | `idempotency_key` UNIQUE; CHECK on `status`; index `(status, priority, created_at, id)` |
| `images` | Validated stored images | UNIQUE `(job_id, sha256)`; index on `sha256`; CHECK on `status`; `seed` only if genuinely returned |
| `posts` | Publication attempts | UNIQUE `(image_id, chat_id)`; CHECK on `status` |
| `settings` | Admin-tunable values | PK `key`; values can never override hard limits |

Status vocabularies (enforced by CHECK constraints):
- `jobs.status`: `pending`, `running`, `succeeded`, `failed`, `cancelled`
- `images.status`: `pending_review`, `approved`, `rejected`, `published`, `missing`
- `posts.status`: `pending`, `sent`, `failed`, `unknown`

## Commands

```bash
make migrate           # alembic upgrade head (DB_URL from env/.env)
make inspect-schema    # print DDL of every table
```

## Migrations

`alembic/env.py` takes the URL from `DB_URL` (never from `alembic.ini`). Create a new
migration after model changes with:

```bash
DB_URL="sqlite+aiosqlite:///./storage/waifubot.db" python3 -m alembic revision --autogenerate -m "describe change"
```

Tests cover the upgrade/downgrade cycle (`tests/unit/test_alembic.py`).
