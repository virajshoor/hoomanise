# Database (Neon PostgreSQL)

Use your own PostgreSQL database. For Neon, use a pooled connection URL with
`sslmode=require`. Set credentials through `DATABASE_URL`.
Pool settings: `DB_POOL_MIN=1`, `DB_POOL_MAX=3` (memory constraint).

Access pattern (`app/db.py`):
- `ConnectionPool` singleton (`get_pool`), dict-row factory
- `query(sql, params, one=False)` / `execute(sql, params)` — synchronous psycopg;
  **always call from async code via `asyncio.to_thread` or `run_in_executor`**
  (the worker and routes do this — keep the pattern)
- `migrate()` — migration runner; `close_pool()` for shutdown

## Migrations

- Files: `app/migrations/NNN_name.sql`, applied in lexicographic order
- Tracking: `schema_migrations (version TEXT PK, applied_at)`
- Runner: `python -m app.migrate` (idempotent — skips applied versions)
- Runs automatically before server start (Procfile: `python -m app.migrate && uvicorn …`)
- Rule: never edit an applied migration; add `002_*.sql` for changes

## Schema (001_init.sql)

### users
```
id UUID PK (gen_random_uuid)
clerk_id TEXT UNIQUE          -- Clerk `sub` claim; NULL = no Clerk link yet
email TEXT, display_name TEXT
role TEXT DEFAULT 'user' CHECK (role IN ('user','admin'))
is_active BOOLEAN DEFAULT TRUE
created_at, updated_at TIMESTAMPTZ
```

### api_keys
```
id UUID PK
user_id UUID → users ON DELETE CASCADE
name TEXT DEFAULT 'default'
prefix TEXT NOT NULL          -- first 9 chars incl 'hm_' (lookup index)
key_hash TEXT UNIQUE          -- sha256(pepper + full_key), never plaintext
scopes TEXT[] DEFAULT '{humanize}'
revoked_at TIMESTAMPTZ        -- soft delete
last_used_at TIMESTAMPTZ      -- throttled update (≥60s apart)
created_at
```
Indexes: `idx_api_keys_user(user_id)`, `idx_api_keys_prefix(prefix)`

### jobs
```
id UUID PK
user_id UUID → users CASCADE
api_key_id UUID → api_keys SET NULL
status TEXT CHECK queued|processing|completed|failed
preset TEXT, intensity INT
input_sha256 TEXT, input_chars INT, input_text TEXT   -- input kept for the worker
result_text TEXT, error TEXT, retries INT DEFAULT 0
created_at, started_at, completed_at
```
Indexes: `idx_jobs_user_created(user_id, created_at DESC)`,
partial `idx_jobs_status(status) WHERE status IN ('queued','processing')`

### usage_records
```
id BIGSERIAL PK
user_id UUID → users CASCADE, api_key_id UUID SET NULL, job_id UUID SET NULL
endpoint TEXT, status TEXT
input_words INT, output_words INT
ai_score_before NUMERIC(5,2), ai_score_after NUMERIC(5,2)
credits_used INT DEFAULT 1        -- ceil(input_words / WORDS_PER_CREDIT)
created_at
```
Index: `idx_usage_user_created(user_id, created_at DESC)` — powers `/v1/me` 30-day usage.

### audit_log
```
id BIGSERIAL PK, user_id UUID, api_key_id UUID
action TEXT                   -- user_created, job_created, api_key_created, api_key_revoked
detail JSONB, created_at
```
Index: `idx_audit_action_created(action, created_at DESC)`

### schema_migrations
`version TEXT PK, applied_at TIMESTAMPTZ`

### webhook_endpoints (003)
```
id UUID PK, user_id UUID → users CASCADE
url TEXT                         -- no secret column: signing keys are derived
events TEXT[] default {job.completed, job.failed}
disabled_at, last_delivery_at, last_status, created_at
```
Index: partial `idx_webhook_user_active(user_id) WHERE disabled_at IS NULL`

### Runtime vs migration roles

Use `DATABASE_URL` for a runtime role with SELECT/INSERT/UPDATE/DELETE on application
tables and USAGE/SELECT on required sequences. Use a separate owner connection through
`MIGRATION_DATABASE_URL` for schema changes. Provision these roles using your database
administration tools; never store owner credentials in this repository.

### Retention

The worker purges `jobs.input_text`/`result_text` older than `JOB_RETENTION_DAYS`
(default 30) every ~10 minutes. `usage_records` and `audit_log` are retained for
accounting; `DELETE /v1/jobs/{id}` removes a job immediately on request.

### users.free_credits (004)
`free_credits INTEGER NOT NULL DEFAULT 600` — the free grant. Remaining balance is
`free_credits - SUM(usage_records.credits_used)`; nothing else to keep in sync.

### Migration list
| Version | Contents |
|---|---|
| 001_init | users, api_keys, jobs, usage_records, audit_log, schema_migrations |
| 002_job_options | jobs.preserve_formatting, jobs.metadata (JSONB) |
| 003_webhooks | webhook_endpoints |
| 004_user_credits | users.free_credits |
| 005_single_active_key | one active key per user; drops webhook secret column |
| 006_credits_and_scopes | unique usage_records.job_id; key scopes default `{humanize,jobs:read}` |
| 007_usage_job_unique | plain unique index on `usage_records(job_id)` (ON CONFLICT target) |

## What lives where (Postgres vs Redis)

- **Postgres (permanent):** users, keys, jobs (input + result), usage, audit
- **Redis (temporary):** queue order, concurrency slots, job state hash (24h TTL),
  rate-limit counters (2 min / 1 day TTL)
- Job *results* survive in Postgres forever; Redis job state expiring only loses the
  `style_score_after` hint on the poll response, never the result. (The DB column keeps
  its historical name `ai_score_after`; the API exposes it as `style_score_after` /
  `credits_used` as credits.)

## Ops notes

- Neon free tier scales to zero: first connection after idle can take ~1s (readiness
  probe may briefly show degraded after long idle — self-heals)
- Connection math: pool max 3 × 1 instance = 3 Postgres connections (free-tier friendly)
- Inspect: `neonctl api "/projects/YOUR_NEON_PROJECT_ID/branches" --org <org-id>`
  or `neonctl connection-string main --pooled --project-id YOUR_NEON_PROJECT_ID`
