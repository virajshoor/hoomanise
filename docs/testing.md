# Testing

Run: `python3 -m pytest -q` → 71 tests, ~12s, **no live services required**.
Config: `pytest.ini` (`asyncio_mode = auto`).

## Test infrastructure (`tests/conftest.py`)

- `fake_redis` — monkeypatches `app.redis_client._redis` with `fakeredis.aioredis.FakeRedis`
- `mock_clerk` — spins up a local HTTP JWKS server (`_JWKSServer`) with a throwaway RSA
  key, points `settings.clerk_jwks_url`/`clerk_issuer` at it, resets the PyJWKClient cache.
  `make_clerk_token(mock_clerk, sub, issuer, exp)` mints RS256 tokens signed by that key.
- `FakeDB` — dict-backed stand-in for `app/db.py`, matched by SQL-substring sniffing.
  **Fragility warning:** it matches literal SQL fragments (`"FROM api_keys WHERE prefix"`,
  `"UPDATE jobs SET status='failed'"`, etc.). If you reword SQL in routes/queue, update
  FakeDB or tests will fail or pass vacuously. It must mirror:
  - param ORDER of INSERT/UPDATE statements (e.g. completed-jobs UPDATE is
    `(result_text, job_id)` — FakeDB reads `params[1]` for the id)
  - `revoked_at IS NULL` filtering on key lookup
  - owner filtering on job get (`user_id = %s` in SQL → check `params[1]`)
- `client` fixture — httpx `AsyncClient` over `ASGITransport(app)` — **lifespan does not
  run** (so no real DB/Redis/worker startup), which is why `no_worker` patches
  `start_worker` to a no-op for route tests.

## Suites

### tests/test_api.py — endpoint + integration behavior
- `test_health` — liveness, X-Request-ID present
- `test_unauthorized_rejected` — 401 with structured error
- `test_tampered_api_key_rejected` — modified `hm_` key → 401 (hash compare)
- `test_api_key_flow_me` — full API-key auth → `/v1/me` shape
- `test_clerk_flow_me_creates_user` — Clerk JWT auto-creates local user with `clerk_id`
- `test_humanize_job_lifecycle` — submit → queue contains job → run `Worker.run` task →
  poll → completed, result present, `style_score_after` present, usage record written
- `test_humanize_validation` — empty text 422, >MAX chars 413, bad preset 422, bad intensity 422
- `test_rate_limit_429` — limit=3 → first 3 pass, then 429
- `test_concurrency_limit_three` — 8 jobs, instruments `pipeline.humanize` with a
  counting wrapper (threaded) → asserts **max in-flight == 3** and all 8 complete
- `test_failed_job_releases_slot` — humanize raises → job `failed`, `ZCARD slots == 0`
- `test_keys_endpoints_require_clerk` — API key can't mint keys (401), Clerk can,
  hash never in responses
- `test_key_revocation` — key works → revoked → 401
- `test_analyze_endpoint` — sync analysis output shape + phrase matches
- `test_job_isolation_between_users` — foreign job → 404
- `test_worker_processes_multiple_sequentially` — 3 jobs → 3 completed + 3 usage records

### tests/test_ratelimit.py — limiter + queue mechanics (fakeredis direct)
- user-min limit (429 at limit+1, scope `user_minute`, retry_after 60)
- ip-min, global (241st of 240 fails), user-day
- user isolation (uA full doesn't block uB)
- headers `X-RateLimit-Limit/Remaining`
- queue enqueue/state, SlotManager acquire/release, **slot cap == 3**,
  **stale slot reaping** (TTL=1s frees slots), orphan recovery (processing → queue)

### tests/test_benchmark.py — fixed corpus
Five AI genres (essay, email, casual, technical, marketing) + three human samples:
semantic preservation, improvement, grammar cleanliness, structural-distance floor,
similarity window at intensity 70, tier monotonicity, and the regression set
(`major to`, `job displacement. And`, `, enhance efficiency.`, `Also AI-`,
duplicate connectives) across multiple seeds.

### tests/test_human_lift.py — false-positive alarm
50 human-authored samples (`tests/data/human_samples.json`): mean internal score < 30,
≤2 flagged above 45, and every sample must be gated as "already human" with
similarity > 0.75.

### tests/test_platform.py — product surface & security
Batch submit + limits, credits exhaustion (`credits_exhausted` + sales email), credit
conversion math, response-cache hit, webhook CRUD, derived webhook secrets, and the
SSRF guard (http/localhost/private-IP/metadata rejected).

### tests/test_semantics.py — hard guarantees
Meaning preservation, numbers/dates/names survival, negation stability,
already-human minimal changes, double-pass stability, formatting preservation,
register leakage, tier scaling, and the known-artifact regression list.

### tests/test_infra.py — Clerk JWT security
- valid token accepted (`sub` returned)
- wrong issuer rejected, expired rejected, garbage rejected
- `azp` not in allowlist → rejected; in allowlist → accepted

## Testing against live services (manual)

Provision your own PostgreSQL, Redis, and Clerk instance. Follow the README to load
`.env`, run migrations, and start the API. Create a fresh API key with your Clerk
session, then verify `/health`, `/readyz`, authentication, and submit/poll behavior.
Never use production user data in tests or commit live keys.

## Adding tests

Follow existing fixtures. For DB-touching route tests, extend `FakeDB` matchers for any
new SQL. For engine changes, prefer seed-scan loops (docs/modifying.md) plus a couple of
fixed-seed assertions.
