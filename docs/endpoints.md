# Endpoints

Local development base URL: `http://localhost:8000`
All responses include `X-Request-ID`. Errors are always:

```json
{"error": {"code": "<machine_code>", "message": "<human message>", "request_id": "..."}}
```

Error codes: `unauthorized`, `insufficient_scope`, `invalid_input`, `payload_too_large`,
`input_too_large`, `rate_limited`, `credits_exhausted`, `too_many_jobs`, `queue_full`,
`key_exists`, `webhook_limit`, `not_found`, `clerk_auth_required`, `service_unavailable`,
`timeout`, `internal_error`.

## Static site

| Method | Path | Description |
|---|---|---|
| GET | `/` | Marketing homepage (SEO: title/meta/OG/Twitter/JSON-LD, canonical from `PUBLIC_SITE_URL`) |
| GET | `/robots.txt` | Crawler rules + sitemap reference |
| GET | `/sitemap.xml` | Homepage, `/docs`, `/dashboard` |
| GET | `/dashboard` | Clerk-powered browser dashboard (keys + live humanize preview) |

## System

### GET /health
No auth. Liveness. `{"ok": true, "service": "hoomanise-api", "version": "1.0.0", "uptime_seconds": …}`

### GET /readyz
No auth. Readiness. Checks Postgres (`SELECT 1` via pool check) and Redis PING.
`200 {"ready": true, "checks": {"postgres": "ok", "redis": "ok"}}` or `503` with fail details.

## Jobs

### POST /v1/humanize — submit humanization job
Auth: API key or Clerk JWT. Rate limited (1 request = 1 rate-limit hit).

Request body:
```json
{
  "text": "<required, 1..20000 chars>",
  "preset": "casual | professional | academic (default casual)",
  "intensity": 70,             // 0–100, default 70; tiered: 0-33 rhythm, 34-66 restructure, 67-100 reconstruct
  "preserve_formatting": true  // default: markdown/lists/code/URLs/citations survive intact
}
```

Responses:
- `202` `{"job_id": "<uuid>", "status": "queued", "poll_url": "/v1/jobs/<uuid>"}`
- `413` if text > `MAX_INPUT_CHARS` (code `input_too_large`)
- `422` invalid text/preset/intensity
- `429` rate limited (includes `Retry-After`, `X-RateLimit-*` headers)

Side effects: `jobs` row (queued), audit_log row, Redis queue push.

### GET /v1/jobs/{job_id}
Auth: API key or Clerk JWT (owner-only — jobs of other users 404).

```json
{
  "job_id": "…", "status": "queued|processing|completed|failed",
  "preset": "casual", "intensity": 70,
  "created_at": "ISO", "completed_at": "ISO|null",
  "error": null,
  "result": "<humanized text>",       // only when completed
  "style_score_after": {              // only when completed
    "value": 36.0, "scale": "0-100", "kind": "internal_heuristic",
    "note": "internal style heuristic, not a validated AI detector; treat as advisory"
  },
  "metadata": {"semantic_check": "passed", "attempts": 1, "similarity": 0.87, "already_human": false, "tier": "high"}
}
```

Ownership check is against the Postgres row (`WHERE id = %s AND user_id = %s`).
Redis job state is advisory only — authorization never trusts it.

### GET /v1/jobs?limit=20&status=completed
Auth: API key or Clerk JWT. Lists the caller's jobs, newest first. `limit` clamped 1–100.
`{"jobs": [{"job_id", "status", "preset", "created_at", "completed_at"}]}`

### DELETE /v1/jobs/{job_id}
Auth: API key (`jobs:read`) or Clerk. Owner-only. `200 {"job_id", "deleted": true}` or `404`.
Nulls the stored input/result immediately (usage/audit rows are retained for accounting).

### POST /v1/analyze — synchronous detection analysis
Auth: API key or Clerk JWT. Runs the full detector suite on the input (no queueing).

```json
{
  "ai_likelihood_overall": 58.0,
  "perplexity": {"rare_token_share": 0.12, "bigram_repetition_rate": 0.07, "score": 0.9},
  "burstiness": {"cv": 0.26, "mean_sentence_words": 12.5, "score": 0.97},
  "stylometry": {"per_1000_words": {...}, "score": 0.39},
  "ai_phrases": {"matches": 14, "per_1000_words": 79.5, "vendor_fingerprint_tally": {...}, "score": 1.0},
  "detectgpt_proxy": {"sentence_length_spread": 0.88, "trigram_collision_rate": 0.012, "score": 0.37},
  "classifier_proxy": {"weights": {...}, "score": 0.78},
  "formatting": {"markdown_tokens": 0, "emoji": 0, …, "score": 0.0},
  "watermark_resistance": {"paraphrase_eligible_token_rate": 0.57, …, "score": 0.57}
}
```

Each `score` is 0–1 AI-likelihood for that method; `ai_likelihood_overall` is 0–100 blended.
Writes a usage_record row. Same input limits as /v1/humanize.

## Account

### GET /v1/me
Auth: API key or Clerk JWT.
`{"user_id", "auth_method": "api_key|clerk", "email", "display_name", "role", "usage_30d": {"requests", "credits"}, "active_api_keys"}`

## Scopes (API keys)

API keys carry scopes; Clerk dashboard sessions bypass all scope checks.

| Scope | Grants |
|---|---|
| `humanize` | `POST /v1/humanize`, `/v1/humanize/batch`, `/v1/analyze` |
| `jobs:read` | `GET /v1/jobs`, `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/stream`, `DELETE /v1/jobs/{id}`, `GET /v1/me` |
| `keys:manage` | `GET /v1/keys`, `DELETE /v1/keys/{id}` (create/regenerate are Clerk-only) |
| `webhooks:manage` | `GET /v1/webhooks`, `DELETE /v1/webhooks/{id}` (create is Clerk-only) |

Missing scope → `403 insufficient_scope` (deny by default). New keys default to
`["humanize", "jobs:read"]`; key/webhook administration therefore requires the dashboard.

## API keys

### POST /v1/keys/regenerate — **Clerk JWT only**
Revokes every active key for the account, then creates a new one. Returns the same shape as
`POST /v1/keys`, with a warning that the previous key is already dead. The dashboard's
"Regenerate key" button calls this.

### POST /v1/keys — **Clerk JWT only** (API keys cannot mint keys)
Body: `{"name": "<string ≤64, default 'default'>"}`.
`201` → `{"key_id", "name", "prefix", "created_at", "full_key": "hm_…", "warning": "Store this key now. It will never be shown again."}`
**One active key per account** (enforced by a unique partial index). Only the SHA-256 hash is stored.

### GET /v1/keys
Auth: API key or Clerk JWT. Metadata only (never hashes):
`{"keys": [{"key_id", "name", "prefix", "created_at", "last_used_at", "revoked"}]}`

### DELETE /v1/keys/{key_id}
Auth: API key or Clerk JWT (owner-only). Revokes (soft — sets `revoked_at`).
`200 {"key_id", "revoked": true}` or `404`.

## Backpressure & limits

| Limit | Env | Behaviour |
|---|---|---|
| Outstanding jobs per user | `MAX_OUTSTANDING_JOBS_PER_USER` (10) | `429 too_many_jobs` + Retry-After |
| Global queue depth | `MAX_QUEUE_DEPTH` (500) | `503 queue_full` + Retry-After |
| Concurrent `/v1/analyze` | `ANALYZE_CONCURRENCY` (2) | queued in-process |
| Request body | `MAX_BODY_BYTES` (1 MB) | streamed cap → `413 payload_too_large` |
| Request duration | `REQUEST_TIMEOUT_SECONDS` (30) | `504 timeout` (SSE excluded) |

Rate limiting fails **closed** (503) for costly endpoints when Redis is unavailable,
and open for cheap read-only ones. Credits are reserved atomically before a job is
queued and refunded if processing fails.

## Credits

New accounts get `users.free_credits` (default 600). 1 credit = 5 words, charged per
request (batch sums it), including cache hits. Exhausted → `429 credits_exhausted` with
`sales_email`. Balances come from `usage_records.credits_used`; the grant is per-user.

## Rate limiting (applies to all /v1/*)

Fixed windows in Redis. Scopes and defaults (`RATE_LIMIT_*` env vars):

| Scope | Key | Default |
|---|---|---|
| Global/minute | `rl:global:min:{minute}` | 240 |
| IP/minute | `rl:ip:min:{ip}:{minute}` | 30 |
| User/minute | `rl:u:min:{user_id}:{minute}` | 10 |
| User/day | `rl:u:day:{user_id}:{day}` | 200 |

429 response:
```
Retry-After: 60
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
{"error": {"code": "rate_limited", "message": "rate limit exceeded (user_minute)",
           "scope": "user_minute", "retry_after_seconds": 60, "request_id": "…"}}
```
If Redis errors, the limiter fails OPEN (request proceeds; warning logged).
