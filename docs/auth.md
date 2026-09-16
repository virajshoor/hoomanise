# Authentication

Two independent paths, cleanly separated. Code lives in `app/auth.py` + `app/api_keys.py`.

## Path selection

`authenticate(request)` reads the `Authorization: Bearer <token>` header:
- token starts with `hm_` (the `API_KEY_PREFIX`) → **API-key path**
- anything else → **Clerk JWT path**
- missing/malformed header → 401 `missing bearer token`

## Clerk JWT path (`_auth_clerk`)

1. `verify_clerk_token(token)` — runs in a thread executor (never blocks the loop):
   - `jwt.PyJWKClient(CLERK_JWKS_URL, cache_keys=True, timeout=10, max_cached_keys=16)`
     fetches and caches Clerk's JWKS (RS256 keys). Timeout is explicit — a hanging JWKS
     fetch once froze the whole event loop; do not remove it.
   - `jwt.decode` with `algorithms=["RS256"]`, `issuer=CLERK_ISSUER`, `require exp+sub`.
   - If `CLERK_AUTHORIZED_PARTIES` is set, the `azp` claim must be in the list.
2. User mapping (`_ensure_clerk_user`): `SELECT users WHERE clerk_id = %s` —
   if absent, INSERT (email/display_name fetched from Clerk Backend API via
   `CLERK_SECRET_KEY` using plain urllib; failures are non-fatal), plus an
   `audit_log` `user_created` row.
3. Returns `AuthContext(method="clerk")`.

There is no local password system. Sessions, sign-in UI, and user management are Clerk's job.
Configure your own Clerk application and instance; no hosted instance is included.

The browser dashboard at `/dashboard` uses the Clerk JS SDK with `CLERK_PUBLISHABLE_KEY`
(injected into the page) and calls the same `/v1` endpoints with the session token.

## API-key path (`_auth_api_key`)

Key format: `hm_` + 24 chars of `secrets.token_urlsafe` (~32 chars total).

1. **Generation** (`api_keys.generate_api_key`): full key returned to caller exactly once.
   Stored: `prefix` = first `len("hm_")+6` = 9 chars (lookup index), `key_hash` =
   SHA-256 of `API_KEY_PEPPER + full_key` (64 hex chars, unique index).
2. **Lookup**: `SELECT * FROM api_keys WHERE prefix = %s AND revoked_at IS NULL`
   (prefix length is `api_keys.PREFIX_LEN` — single source of truth; lookup slices the
   presented token to the same length).
3. **Compare**: `constant_time_equal` — `hmac.compare_digest` of recomputed hash vs stored
   hash. Plaintext keys are never stored or logged.
4. **User**: load owning `users` row; disabled users (`is_active=false`) → 401.
5. **Last-used throttling**: `UPDATE api_keys SET last_used_at = now() WHERE …
   (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')` — avoids a
   write per request.
6. Scopes are enforced per endpoint; new keys default to `humanize` and `jobs:read`.

## Which endpoints accept which credentials

| Endpoint | API key | Clerk JWT |
|---|---|---|
| /v1/humanize, /v1/jobs*, /v1/analyze, /v1/me, /v1/keys (GET), /v1/keys/{id} (DELETE) | yes | yes |
| /v1/keys (POST — create key) | **no** (401 `clerk_auth_required`) | yes |

Enforcement: `routes_v1._require_clerk` rejects `hm_` tokens before verification
(verification itself runs in an executor).

## Scopes

API keys carry mandatory scopes checked per endpoint (deny by default); Clerk dashboard
sessions bypass scope checks:

| Scope | Endpoints |
|---|---|
| `humanize` | humanize, batch, analyze |
| `jobs:read` | jobs list/get/stream/delete, `/v1/me` |
| `keys:manage` | list/revoke keys |
| `webhooks:manage` | list/disable webhooks |

New keys default to `["humanize", "jobs:read"]` (migration 006), so programmatic clients
cannot administer keys or webhooks without a dashboard session.

## One key per account

Each account has exactly one active API key, enforced with a unique partial index
(`uniq_active_api_key_per_user ON api_keys(user_id) WHERE revoked_at IS NULL`). Creating a
second key returns `409 key_exists`; use `POST /v1/keys/regenerate`, which revokes the old
key and issues a new one in one step. The dashboard exposes this as "Regenerate key".

## Revocation

`DELETE /v1/keys/{key_id}` sets `revoked_at = now()` (soft delete). Lookup filters
`revoked_at IS NULL`, so revoked keys 401 immediately. Owner-only via `user_id` match.

## Testing auth

- Unit tests (`tests/test_infra.py`, `tests/test_api.py`) spin up a local HTTP server
  serving a mock JWKS with a throwaway RSA key (`tests/conftest.py::mock_clerk`) and mint
  tokens with `make_clerk_token`. Proves signature/issuer/exp/azp enforcement end-to-end
  through the ASGI app.
