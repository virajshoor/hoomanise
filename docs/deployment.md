# Self-hosted deployment

No managed deployment is included. Provision your own PostgreSQL, Redis, and Clerk application.

1. Install `requirements.txt` with Python 3.12.
2. Configure environment variables from `.env.example`; use fresh secrets, exact Clerk issuer/JWKS URLs, explicit `CLERK_AUTHORIZED_PARTIES` and `ALLOWED_ORIGINS`.
3. Set `APP_ENV=production`, a strong `API_KEY_PEPPER`, and your public site/API URLs.
4. Apply migrations using `MIGRATION_DATABASE_URL` with an owner role. Set runtime `DATABASE_URL` to a separate role with only required table and sequence permissions.
5. Run one Uvicorn worker and one replica initially. Included `Procfile`, `railpack.json`, and `railway.json` run migrations before startup.
6. Verify `/health`, `/readyz`, unauthorized access rejection, and an authenticated submit/poll flow.

Use TLS and keep PostgreSQL/Redis private. Read [security limitations](../SECURITY.md) before accepting untrusted users, especially webhook destinations. Configure a trusted reverse proxy and outbound network restrictions.

The API worker lives in the same process. Queue recovery and slots need further testing before multi-replica operation. Database migrations are append-only; review migrations separately before deploying a rollback.
