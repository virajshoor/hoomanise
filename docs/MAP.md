# Humanise API — Codebase Map

This directory is the canonical map of the codebase for humans and AI agents.
Start here, then follow links. Every file below is written to be read independently.

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | System overview, request flow, component responsibilities |
| [endpoints.md](endpoints.md) | Every URL, method, auth requirement, request/response shapes, status codes |
| [auth.md](auth.md) | Clerk JWT verification, API-key system, user mapping, code walk-through |
| [engine.md](engine.md) | The humanizer: detection methods, transforms pipeline, scoring math, data tables |
| [database.md](database.md) | Neon/Postgres schema, indexes, migration system, how to add a migration |
| [redis.md](redis.md) | Every Redis key, rate limiting, job queue, concurrency slots, worker lifecycle |
| [configuration.md](configuration.md) | All environment variables, defaults, where each is read |
| [modifying.md](modifying.md) | How to change anything safely: add endpoints, transforms, presets, limits |
| [testing.md](testing.md) | Test suite layout, what each test proves, how to run/extend |
| [deployment.md](deployment.md) | Railway/Neon/Clerk topology, deploy procedure, go-live checklist, custom-domain migration |

## Repository layout

```
hoomanise/
├── app/
│   ├── main.py            # FastAPI app, middleware, lifespan, /health, /readyz
│   ├── config.py          # Settings: every env var, defaults
│   ├── auth.py            # Clerk JWT + API key authentication
│   ├── api_keys.py        # Key generation, hashing, constant-time compare
│   ├── credits.py         # Atomic credit reserve/settle/refund (row-locked)
│   ├── db.py              # psycopg pool, query/execute helpers, migration runner
│   ├── migrations/        # SQL migration files (001_init.sql, …)
│   ├── migrate.py         # CLI: python -m app.migrate
│   ├── redis_client.py    # Async Redis singleton
│   ├── ratelimit.py       # Fixed-window rate limiter (fails open for reads, closed for costly ops)
│   ├── queue.py           # Job queue, SlotManager, Worker (in-process)
│   ├── routes_v1.py       # All /v1/* endpoint handlers
│   └── engine/            # The humanizer (pure Python, no ML deps)
│       ├── resources.py   # Data tables: AI words/phrases, synonyms, contractions
│       ├── transforms.py  # Every rewrite transform
│       ├── pipeline.py    # Orchestration, presets, tiers, candidate ranking
│       ├── analysis.py    # All detection-method scorers (0–1 per method)
│       ├── semantics.py   # Meaning-preservation gate (claims, numbers, negations)
│       ├── grammar.py     # Regex grammar gate
│       ├── structure.py   # Structural-distance + lexical-diversity metrics
│       ├── pos_grammar.py # Optional spaCy POS/dependency checks
│       └── data/wordfreq.txt  # Top-10k English words (frequency ranks)
├── tests/                 # pytest suite (71 tests, fakeredis + mock JWKS)
├── static/                # SEO site: index.html, robots.txt, sitemap.xml
├── Procfile               # Railway start command
├── railpack.json          # Railpack start command (primary)
├── railway.json           # Railway start command + healthcheck
├── requirements.txt       # exact-pinned (==) for reproducible builds
├── README.md              # Human-facing docs
└── .env.example           # Environment variable reference
```

## The 30-second version

A FastAPI service on Railway. Clients authenticate with a Clerk JWT or an `hm_` API key.
`POST /v1/humanize` validates input, writes a job row to Neon Postgres, pushes the job ID
onto a Redis queue, and returns 202. An in-process asyncio worker pops jobs, acquires one
of 3 Redis-backed concurrency slots, runs the pure-Python humanizer engine on a thread,
stores the result in Postgres, and records usage. Clients poll `GET /v1/jobs/{id}`.
Rate limiting is a Redis fixed-window counter with 4 configurable scopes. New accounts get
600 free credits (1 credit = 5 words), after which the API points to support@example.com.
The SEO homepage + dashboard are served by the same service (`/`, `/dashboard`).

## Ground rules for agents modifying this repo

1. All runtime config comes from `app/config.py` env vars — never hardcode limits/URLs.
2. Schema changes go in a new `app/migrations/NNN_*.sql` file — never edit applied ones.
3. No secrets in code or git; secrets live in Railway variables (see `.env.example`).
4. The engine (`app/engine/`) must stay dependency-free (pure Python + stdlib) — it runs
   in a 512 MB container (spaCy runs with `JOB_CONCURRENCY=1` for headroom).
5. Run `pytest` after any change; 71 tests must pass.
6. Tests use fakeredis and a mock JWKS server — no live services needed for unit tests.
