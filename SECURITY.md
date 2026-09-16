# Security notes

## Release review

The public release removes local credential files and retired deployment references.
The review covers secret scanning, dependencies, authentication, ownership checks,
SQL parameterization, request limits, and webhook delivery. It is not a penetration
test or a guarantee that all vulnerabilities have been found.

Fixed during review:

- Disabled Clerk users were not consistently rejected by the user mapping and
  Clerk-only management routes. Both now use the shared active-user checks.
- Missing Clerk issuer configuration could omit issuer validation. Authentication
  now fails closed and requires the issuer claim.
- Removed unused hard-coded fallback key material and a retired provisioning script
  that wrote database credentials into local files.

## Verification for this release

- 73 tests passed on Python 3.12 after authentication fixes and dependency upgrades.
- Gitleaks scanned all 37 pre-release commits: two findings were the literal
  documentation placeholder `hm_your_key`, not working credentials.
- Gitleaks found no secrets in the cleaned publication snapshot.
- `pip-audit` reported no known vulnerabilities in audited installed packages after
  upgrading FastAPI/Starlette, Pydantic, pytest, and python-dotenv.
- The spaCy `en-core-web-sm` 3.8.0 model is distributed through GitHub and was skipped
  by the PyPI advisory audit. No vulnerability conclusion is made for that artifact.
- Public Git history starts from the cleaned snapshot; earlier deployment history
  is not published.

## Known deployment risks

- **Webhook SSRF:** destination validation rejects private/reserved addresses and
  redirects, but DNS is resolved again when connecting. DNS rebinding remains
  possible. Block private, loopback, metadata, and internal destinations at the
  network layer, or disable webhook routes until delivery uses a pinned validated
  address. URL validation alone is insufficient.
- **Proxy trust:** IP limiting reads the final `X-Forwarded-For` value, matching the
  original hosting setup. Only expose the API through a proxy that overwrites or
  safely appends this header; direct access permits spoofing of IP rate counters.
  User/global limits still apply.
- **Administrative throttling:** key and webhook creation do not use the shared
  rate-limit guard. Apply gateway limits before accepting untrusted traffic.
- **Availability and concurrency:** database calls in some async routes block the
  event loop. Worker recovery, queue admission, and key replacement need real
  database/concurrency testing before multi-replica deployment.
- **Data retention:** PostgreSQL stores input/output text; Redis also caches results.
  Job deletion does not necessarily purge cached copies immediately. Configure
  cache TTLs and retention to match your privacy requirements.

Use TLS, explicit CORS and Clerk authorized parties, a strong private pepper,
separate migration/runtime DB roles, private Redis, and fresh provider credentials.
Do not deploy the example environment unchanged.

## Reporting

Report vulnerabilities privately through GitHub's private vulnerability reporting
feature if enabled. Never include live tokens, customer data, or credentials in a
public issue. If a credential is exposed, revoke it at its provider; deleting a
file or Git commit alone does not revoke access.
