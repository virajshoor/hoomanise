# Hoomanise

A self-hosted Python API for rule-based text rewriting and style analysis. Built with FastAPI, PostgreSQL, Redis, and Clerk authentication.

This repository contains source code, not a hosted service. Previous deployments have been retired. Bring your own database, Redis instance, and Clerk application.

## What it does

- Rewrites text using casual, professional, or academic presets and adjustable intensity.
- Uses phrase replacement, sentence transforms, grammar checks, and heuristic meaning-preservation checks.
- Processes jobs asynchronously, with polling, batch submission, and server-sent status events.
- Supports scoped API keys, per-user credits, rate limits, caching, and signed webhooks.
- Includes a basic browser dashboard and an optional spaCy grammar model.

Style scores are internal heuristics, not probabilities that text was written by AI. The project does not guarantee detector evasion, watermark removal, factual preservation, or grammatical correctness. Review rewritten text before use.

## Requirements

- Python 3.12 recommended
- PostgreSQL and Redis
- A Clerk application for authenticated API use

The rewrite engine can also run on its own without those services.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your own connection URLs and Clerk settings. Generate an API-key pepper with `python -c 'import secrets; print(secrets.token_urlsafe(48))'` and save it as `API_KEY_PEPPER`. Keep it private and stable: changing it invalidates existing API keys and, unless configured separately, webhook signatures.

Required configuration:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection URL |
| `REDIS_URL` | Redis connection URL |
| `API_KEY_PEPPER` | Random private key material |
| `CLERK_ISSUER` | Exact issuer URL for your Clerk instance |
| `CLERK_JWKS_URL` | Your instance's JWKS endpoint |
| `CLERK_AUTHORIZED_PARTIES` | Comma-separated allowed frontend origins |
| `CLERK_PUBLISHABLE_KEY` | Public Clerk key used by the dashboard |
| `CLERK_SECRET_KEY` | Optional backend key for user profile lookup |

`.env` is not loaded automatically by the application. Export it before migrations and startup (source only a file you trust):

```bash
set -a
source .env
set +a
python -m app.migrate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `/docs` for the API schema, `/dashboard` for the browser client, `/health` for liveness, and `/readyz` for database/Redis readiness. Add your local frontend origin in Clerk as well.

## Use the engine directly

```python
from app.engine.pipeline import humanize

rewritten, metadata = humanize(
    "Moreover, it is important to note that clear writing helps readers.",
    preset="professional",
    intensity=50,
    seed=42,
)
print(rewritten)
```

## API example

Sign in through Clerk and create a key using `POST /v1/keys` with your session JWT. Store the returned key securely, then set `HOOMANISE_API_KEY` in your shell:

```bash
curl http://localhost:8000/v1/humanize \
  -H "Authorization: Bearer $HOOMANISE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Clear writing helps readers understand complex ideas.","preset":"professional","intensity":50}'

curl http://localhost:8000/v1/jobs/JOB_ID \
  -H "Authorization: Bearer $HOOMANISE_API_KEY"
```

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/humanize` | Submit a rewrite job |
| `POST /v1/humanize/batch` | Submit multiple texts |
| `GET /v1/jobs/{id}` | Read a job and result |
| `GET /v1/jobs/{id}/stream` | Stream status events |
| `GET /v1/jobs` | List your jobs |
| `DELETE /v1/jobs/{id}` | Delete your job |
| `POST /v1/analyze` | Analyze text style |
| `GET /v1/me` | Account and usage information |
| `/v1/keys` | Create, list, and revoke keys |
| `POST /v1/keys/regenerate` | Replace your active key |
| `/v1/webhooks` | Manage signed callbacks |

Key creation/regeneration and webhook creation require a Clerk session. Other routes enforce API-key scopes and ownership. Default grant: 600 credits, charged at one credit per five words, rounded up.

## Architecture

FastAPI authenticates requests and writes jobs to PostgreSQL. Redis holds the queue, transient job state, rate counters, and cached results. An in-process worker runs the rewrite engine and saves results in PostgreSQL. Use one application worker/replica until you validate multi-worker recovery and concurrency behavior.

## Tests

```bash
pytest -q
```

Tests use fake Redis, an in-memory database double, and a temporary local JWKS server. They do not prove real PostgreSQL transaction behavior or production reliability. Some webhook tests need DNS access.

## Security and deployment

Read [SECURITY.md](SECURITY.md) before exposing this service publicly. The repository has had a release-focused review, not a comprehensive penetration test. Known limitations include webhook DNS rebinding and deployment-specific proxy trust. Do not enable untrusted webhook destinations without outbound network restrictions.

Never commit `.env`, credentials, data exports, or logs. `.env.example` contains placeholders and local development defaults only. Use TLS, explicit frontend origins, fresh secrets, and a least-privilege database role for deployment.

Railway/Railpack startup templates are included, but contain no linked project or credentials. See [deployment](docs/deployment.md), [configuration](docs/configuration.md), [authentication](docs/auth.md), [database](docs/database.md), [endpoints](docs/endpoints.md), and [engine](docs/engine.md).
