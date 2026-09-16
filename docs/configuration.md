# Configuration

Every knob is an env var, read once at import by `app/config.py::Settings` (singleton
`settings`). Review defaults before deployment; `.env.example` is the reference. Never
hardcode config elsewhere.

## Core

| Var | Default | Used by | Notes |
|---|---|---|---|
| `APP_ENV` | `production` | logging/docs flavor | `development` locally |
| `LOG_LEVEL` | `INFO` | main.py basicConfig | `DEBUG` very chatty |
| `PUBLIC_BASE_URL` | (empty) | main.py OpenAPI `servers` | set to the public URL so Swagger examples hit prod |
| `WORKER_ENABLED` | `true` | main.py lifespan | set `false` if you split the worker into its own service |

## Connections

| Var | Default | Used by | Notes |
|---|---|---|---|
| `DATABASE_URL` | — (required) | db.py | Neon **pooled** URL, `sslmode=require` |
| `REDIS_URL` | — (required) | redis_client.py | `${{Redis.REDIS_URL}}` reference on Railway |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 1 / 3 | db.py | memory-constrained |
| `REDIS_POOL_MAX` | 32 | redis_client.py | lazy connections |

## Clerk

| Var | Default | Used by |
|---|---|---|
| `CLERK_SECRET_KEY` | — | auth.py Backend API user fetch (email on user creation) |
| `CLERK_ISSUER` | — (optional) | auth.py JWT `iss` check; unset = not verified |
| `CLERK_JWKS_URL` | — (required for Clerk path) | auth.py PyJWKClient (timeout 10s) |
| `CLERK_AUTHORIZED_PARTIES` | — (comma-separated, optional) | auth.py `azp` check |

## API keys

| Var | Default | Notes |
|---|---|---|
| `API_KEY_PEPPER` | "" | Server-side secret salted into key hashes. Rotating it invalidates all keys. |
| `API_KEY_PREFIX` | `hm_` | Changing it orphans stored keys (prefix slices derive from it) |

## Limits

| Var | Default | Notes |
|---|---|---|
| `MAX_INPUT_CHARS` | 20000 | humanize/analyze text limit → 413 |
| `MAX_BODY_BYTES` | 1000000 | middleware body cap → 413 |
| `RATE_LIMIT_USER_PER_MIN` | 10 | |
| `RATE_LIMIT_USER_PER_DAY` | 200 | |
| `RATE_LIMIT_IP_PER_MIN` | 30 | |
| `RATE_LIMIT_GLOBAL_PER_MIN` | 240 | |

## Queue / worker

| Var | Default | Notes |
|---|---|---|
| `HUMAN_SCORE_THRESHOLD` | 35 | text below this internal style score is treated as already-human (safe low-tier pass only) |
| `JOB_CONCURRENCY` | 1 (prod) / 3 (default) | max simultaneous humanize jobs; prod uses 1 for spaCy memory headroom |
| `JOB_QUEUE_WAIT_SECONDS` | 600 | slot-acquisition deadline; exceeded → job fails "queue timeout" |
| `JOB_SLOT_TTL_SECONDS` | 120 | stale slot reaping window (crash recovery) |
| `JOB_RESULT_TTL_HOURS` | 24 | Redis job-state TTL (results persist in Postgres regardless) |
| `JOB_LEASE_SECONDS` | 300 | reserved for future lease renewals (unused) |
| `JOB_INPUT_STORE_MAX_CHARS` | 100000 | reserved cap for stored inputs (unused; `MAX_INPUT_CHARS` governs) |

## Platform

| Var | Default | Notes |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` for structured logs |
| `LOG_SAMPLE` | 1.0 | fraction of request lines logged |
| `FREE_CREDITS_DEFAULT` | 600 | free credits granted per account |
| `WORDS_PER_CREDIT` | 5 | 1 credit = 5 words (rounded up per request) |
| `SALES_EMAIL` | support@example.com | shown when credits run out |
| `PUBLIC_SITE_URL` | https://example.com | canonical/OG/sitemap base |
| `USE_SPACY_GRAMMAR` | true (prod) | POS grammar gate; needs ~150 MB RAM |
| `RATE_LIMIT_BATCH_MAX` | 10 | max texts per batch |
| `ANALYZE_CONCURRENCY` | 2 | simultaneous `/v1/analyze` allowed |
| `MAX_OUTSTANDING_JOBS_PER_USER` | 10 | queued+processing jobs per user |
| `MAX_QUEUE_DEPTH` | 500 | global queue cap → 503 |
| `JOB_RETENTION_DAYS` | 30 | purge raw input/result text older than this (0 disables) |
| `WEBHOOK_SIGNING_KEY` | (falls back to `API_KEY_PEPPER`) | master key for webhook signatures; production refuses to start without one of them |
| `MIGRATION_DATABASE_URL` | (falls back to `DATABASE_URL`) | privileged role used only for migrations |
| `RESPONSE_CACHE_ENABLED` / `_TTL_HOURS` | true / 168 | identical-input cache |
| `WEBHOOK_DELIVERY_RETRIES` / `_TIMEOUT_SECONDS` / `WEBHOOK_MAX_PER_USER` | 3 / 10 / 5 | webhook delivery |
| `USE_SPACY_GRAMMAR` | false | POS/dependency grammar (needs ~150MB+ RAM; off on 512MB) |

## CORS

`ALLOWED_ORIGINS` — comma-separated; default `*`. `allow_credentials=False` (bearer-token
auth, no cookies). When you get a real domain: set to `https://example.com` and the
Clerk app origin.

## Where to set them

- **Production:** `railway variable set --service api KEY=value` (they inject at runtime)
- **Local:** export in shell or a git-ignored `.env` (loaded only if you wire dotenv;
  the app itself reads `os.environ` directly)
- Secrets present in this setup (pepper, Clerk keys, Neon password) are **not** in git —
  provide them through your deployment secret store or a git-ignored local `.env`.
