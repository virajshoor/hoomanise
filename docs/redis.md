# Redis (Railway template, redis:8.2)

Client: `app/redis_client.py` — async `redis-py` singleton.
`decode_responses=True`, `socket_timeout=10`, `retry_on_timeout` + `Retry(ExponentialBackoff, 3)`,
`max_connections=REDIS_POOL_MAX` (default 32, lazy). Connects to `${{Redis.REDIS_URL}}`
(Railway private network — not publicly reachable).
Local dev quirk: `redis://localhost…` is rewritten to `redis://127.0.0.1…` to avoid an
IPv6/IPv4 pool mismatch seen on macOS.

## Every key the app touches

| Key | Type | Purpose | TTL |
|---|---|---|---|
| `hoomanise:queue` | LIST | FIFO job queue (LPUSH enqueue, BRPOPLPUSH consume) | — |
| `hoomanise:processing` | LIST | In-flight jobs (reliability parking list); re-queued on startup | — |
| `hoomanise:slots` | ZSET | Concurrency semaphore; member=slot uuid, score=acquire timestamp | entries reaped when older than `JOB_SLOT_TTL_SECONDS` (120s) |
| `hoomanise:job:{id}` | HASH | Job state: `state`, `enqueued_ts`, `started_ts`, `finished_ts`, `error`, `score_after` | `JOB_RESULT_TTL_HOURS` (24h) |
| `rl:global:min:{minute}` | STRING | Global/minute counter | 120s |
| `rl:ip:min:{ip}:{minute}` | STRING | Per-IP/minute counter | 120s |
| `rl:u:min:{user_id}:{minute}` | STRING | Per-user/minute counter | 120s |
| `rl:u:day:{user_id}:{day}` | STRING | Per-user/day counter | 90000s |
| `hoomanise:webhooks` | LIST | Webhook delivery queue (payloads + attempt counts) | — |
| `hoomanise:cache:{sha}:{preset}:{intensity}:{preserve}` | HASH | Response cache: `result`, `style_score` | `RESPONSE_CACHE_TTL_HOURS` (168h) |

Minute bucket = `int(unix_time // 60)`; day = `// 86400`. Fixed windows — a burst that
spans a minute boundary is split across two buckets by design.

Redis is **never** the source of truth: losing it drops queued jobs and resets rate
counters, but job records/results live in Postgres. Queue depth is capped at
`MAX_QUEUE_DEPTH` (503 beyond it) and expiry of the whole instance is survivable
(jobs are then marked failed/refunded).

## Rate limiter (`app/ratelimit.py`)

`check_limits(user_id, ip, …)` → per-request, in order: global, user-min, user-day,
ip-min. Each `_hit` = `INCR key:{bucket}` (EXPIRE on first increment). Returns
`{allowed, scope, limit, remaining, retry_after, headers}`.

- All four must allow; first violated scope names the 429.
- Called via `routes_v1.guard`; wrapped in try/except there — **Redis failure fails
  open** (request proceeds, warning log). Deliberate: availability > strict limiting
  on a free API. Change this only with intent.

## Queue + concurrency (`app/queue.py`)

- `enqueue(job_id)`: HSET job state queued (24h TTL) + LPUSH.
- `Worker.run`: while `len(inflight) < JOB_CONCURRENCY`, `BRPOPLPUSH(queue, processing, 2)`;
  each popped job becomes an asyncio task (`_safe_process`).
- `SlotManager.acquire(deadline)`: pipeline `[ZREMRANGEBYSCORE stale, ZCARD]`; if
  `count < JOB_CONCURRENCY` → ZADD, then re-check ZCARD ≤ limit (race-safe against
  other instances); else sleep 0.5s and retry until deadline. This is the cross-instance
  semaphore; the local `inflight` set prevents one instance from popping more than it
  can run.
- Slot TTL reaping means a worker killed mid-job frees its slot within 120s.
- `recover_orphans` (on startup): everything in `processing` is RPUSHed back to the queue.
- `_fail(job_id, error)`: Postgres `status='failed'` + Redis hash update. Slot release is
  in `finally` — failures never leak slots.

## Verifying Redis state manually (prod is private-network only)

From the api service context (e.g. temporarily add a debug route or run
`railway run`):
```
LRANGE hoomanise:queue 0 -1
ZRANGE hoomanise:slots 0 -1 WITHSCORES
HGETALL hoomanise:job:<id>
```
Locally: `redis-cli` on 127.0.0.1:6379 (Homebrew redis, started with
`/opt/homebrew/opt/redis/bin/redis-server --daemonize yes`).
