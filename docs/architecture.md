# Architecture

## Topology

```
                       ┌─────────────────────────────┐
 Client (Postman/app) ─▶│ FastAPI on Railway (api)   │
                       │  - middleware stack         │
                       │  - /v1 routes               │
                       │  - in-process asyncio worker│
                       └───────┬──────────────┬──────┘
                               │              │
                 ┌─────────────▼──┐      ┌────▼─────────────────┐
                 │ Railway Redis  │      │ Neon PostgreSQL      │
                 │ (free template)│      │ (free, pooled conn)  │
                 │ queue/slots/RL │      │ users/jobs/usage     │
                 └────────────────┘      └──────────────────────┘
```

One Railway service runs both the API and the worker (free-tier constraint, 512 MB RAM).
All state that must survive goes to Postgres; Redis is volatile coordination only.

## Request flow (POST /v1/humanize)

1. **Middleware stack** (`app/main.py`, applied bottom-up):
   - `SecurityHeadersMiddleware` — X-Content-Type-Options, X-Frame-Options, Referrer-Policy
   - `BodyLimitMiddleware` — rejects Content-Length > `MAX_BODY_BYTES` with 413
   - `RequestIDMiddleware` — assigns/propagates `X-Request-ID`, logs every request
   - FastAPI `CORSMiddleware` — origins from `ALLOWED_ORIGINS`
2. **Auth** (`app/auth.py::authenticate`) — Bearer token; `hm_` prefix → API-key path,
   otherwise Clerk JWT path. Returns `AuthContext(user_id, method, api_key_id, role…)`.
3. **Rate limit** (`app/ratelimit.py::check_limits` via `routes_v1.py::guard`) — 4 Redis
   INCR counters (global, IP, user-min, user-day). On Redis failure: fail-open + warning log.
   On breach: HTTP 429 with `Retry-After` + `X-RateLimit-*` headers.
4. **Validation** (`routes_v1.py::create_humanize_job`) — text non-empty, ≤ `MAX_INPUT_CHARS`
   (413 if over), preset in {casual, professional, academic}, intensity int 0–100.
5. **Persist + enqueue** — job row INSERT into Postgres (input text stored for the worker),
   then `queue.enqueue(job_id)` (Redis HSET job state + LPUSH queue). Audit log row written.
6. **Respond 202** — `{job_id, status: "queued", poll_url}`.

## Worker flow (`app/queue.py::Worker.run`)

Started in the FastAPI lifespan when Postgres+Redis are reachable and `WORKER_ENABLED=true`.

```
loop:
  if in_flight >= JOB_CONCURRENCY: sleep(0.2); continue
  job_id = BRPOPLPUSH hoomanise:queue → hoomanise:processing (2s timeout)
  spawn _safe_process(job_id) as task
```

`_safe_process`:
1. Load job row from Postgres (drop if missing; skip if terminal status).
2. `SlotManager.acquire` — ZSET-based global semaphore:
   - reap slots older than `JOB_SLOT_TTL_SECONDS` (crash safety)
   - if `ZCARD < JOB_CONCURRENCY`: ZADD self with now-timestamp
   - retry every 0.5s until `JOB_QUEUE_WAIT_SECONDS` deadline → job fails "queue timeout"
3. Mark `processing` in Postgres + Redis.
4. Run `pipeline.humanize` + `analysis.overall_score` via `asyncio.to_thread`
   (seed = low 32 bits of the job UUID → deterministic per job).
5. Write result: UPDATE job row, INSERT usage_record, Redis hash → completed.
6. `finally`: release slot (ZREM), LREM from processing list. Errors → `_fail`
   (status=failed + error message) and slot still released.

**Crash safety:** jobs stuck in `hoomanise:processing` are re-queued on startup
(`recover_orphans`). Slots expire by TTL, so a killed worker frees its slots within
`JOB_SLOT_TTL_SECONDS`.

## Shutdown (graceful)

Lifespan teardown: `worker.stop()` sets a stop event, drains in-flight jobs (10s budget),
then closes Redis and DB pools. `Procfile` runs under uvicorn which forwards SIGTERM.

## Error handling

- All HTTPException → `{"error": {code, message, request_id}}` (`_status_code_name` map in main.py)
- Validation errors → 422 with per-field details
- Unhandled exceptions → 500 with generic message; full traceback only in logs
- Every response carries `X-Request-ID`

## Security & limits

- **Auth**: Clerk JWT (RS256 via JWKS, timeouts, optional `azp`) or `hm_` API keys
  (SHA-256 + pepper, constant-time compare, prefix lookup, one active key per user).
- **Scopes**: `humanize`, `jobs:read`, `keys:manage`, `webhooks:manage`; API keys are
  denied by default, Clerk sessions bypass. Key/webhook admin needs the dashboard.
- **Credits**: atomic reserve (user row locked) → settle on completion → refund on
  failure; cache hits are charged; grant comes from `users.free_credits`.
- **Backpressure**: `MAX_OUTSTANDING_JOBS_PER_USER` (429), `MAX_QUEUE_DEPTH` (503),
  `ANALYZE_CONCURRENCY`, streaming body cap, `REQUEST_TIMEOUT_SECONDS`.
- **Rate limiting**: fixed windows; fails **closed** (503) for costly endpoints if Redis
  is down, open for cheap reads.
- **Webhooks**: SSRF-guarded (https only, no redirects, private/loopback/link-local
  blocked); signing secret derived from the server key (nothing secret stored).
- **Transport & privacy**: HSTS, Clerk-compatible CSP, `Cache-Control: no-store, private`
  on authenticated responses, raw job text purged after `JOB_RETENTION_DAYS`, and
  `DELETE /v1/jobs/{id}` for immediate deletion.

## Credits

New accounts get `FREE_CREDITS_DEFAULT` (600) credits. Each request costs
`max(1, ceil(words / WORDS_PER_CREDIT))`. When exhausted the API returns `429
credits_exhausted` pointing at `SALES_EMAIL`. Balances are derived from
`usage_records.credits_used`, so there is no separate ledger to keep in sync.

## Static site

`static/index.html` + `robots.txt` + `sitemap.xml` are served at `/`, `/robots.txt`,
`/sitemap.xml` by `main.py`, with canonical URLs from `PUBLIC_SITE_URL`.

## Memory budget (512 MB)

- uvicorn 1 worker, no access log
- psycopg pool 1–3 connections (`DB_POOL_*`)
- Redis pool 32 lazy connections (`REDIS_POOL_MAX`)
- Engine data: ~10k-word frequency file (~100 KB), plain dicts — loaded lazily via
  `resources.word_ranks()` (lru_cache)
- spaCy (`en_core_web_sm`) is loaded lazily on first job and kept resident; the worker
  runs `JOB_CONCURRENCY=1` so only one job (and one parse) is in flight at a time
- Parses are capped at 4,000 characters (`pos_grammar._iter_sents`) to bound latency
