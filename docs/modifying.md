# Modifying the codebase

Patterns for the most likely changes. After any change: `python3 -m pytest` (31 pass),
then `railway up --service api --detach` to ship. Commit with a clear message.

## Add or change an endpoint

1. Handler in `app/routes_v1.py` (router has `prefix="/v1"`):
   ```python
   @router.post("/thing")
   async def thing(request: Request, body: dict):
       ctx = await guard(request)          # auth + rate limit
       # validate body → 422 via HTTPException(status_code=422, detail={"code": ..., "message": ...})
       # persist via db (wrap sync calls: await asyncio.get_running_loop().run_in_executor(None, db.query, sql, params))
   ```
2. Follow existing conventions: pydantic-free dict bodies (validated by hand), structured
   errors, `_iso()` for timestamps, owner-scoped queries (`AND user_id = %s`).
3. Update `docs/endpoints.md` and README endpoint table.
4. If CPU-heavy: make it a job (copy `/v1/humanize` flow: insert row → `enqueue` → poll).

## Add a credit rule / change pricing

Credits live in `routes_v1.credits_for(text)` (default `ceil(words/5)`) and are charged in
`guard(..., cost=...)` via `_quota_check`. The grant is `settings.free_credits_default`
(mirrored by `users.free_credits` default in migration 004). To make paid tiers later:
add a column per user, read it in `_quota_check` instead of the global default, and write
a payments webhook that raises it. Nothing else needs to change.

## Add a grammar rule

Two layers: `grammar.py` (regex, always on) and `pos_grammar.py` (spaCy, when
`USE_SPACY_GRAMMAR=true`). Both return a list of problem strings; the pipeline treats any
problem as a hard reject for the candidate. Keep rules precise — a false positive silently
rejects good rewrites.

## Add a detection method (scorer)

1. `app/engine/analysis.py`: function returning `{"…metrics…", "score": 0..1}`.
2. Add to `full_analysis()` dict.
3. If it should affect `overall_score`/`classifier_proxy`: add a weight (keep sum ≈ 1.0).
4. Mark length-gated scorers with `"active": False` when they lack signal (see
   `stylometry`, `function_word_ratio`, `word_length_profile`, `ngram_repetition`) so
   `overall_score` renormalizes instead of averaging in a neutral placeholder.
5. Test with a human sample and an AI sample; check separation before/after.

## Add/extend a transform

1. Function in `app/engine/transforms.py`: `(text, rng, prob, …) -> text`. Use
   `_apply_random(text, pattern, [templates], rng, prob)` for regex+template swaps;
   templates interpolate `{0}`, `{1}` from capture groups (groups must be capturing).
2. Insert into `pipeline.humanize` **in the right order** — order matters:
   - phrase scrubbing happens before contractions AND again after (step 10) because
     contraction injection can re-create AI phrases
   - burstiness before punctuation cleanup (dashes produced by merges get cleaned)
   - `tidy` always last
3. Protect natural collocations: add to `PROTECT_BIGRAMS` in resources.py
   (`_bigram_protected` consults the next word — it's how "right now" survives the
   "right"→"correct" swap).
4. If it introduces artifacts, scan seeds: run the pipeline over seeds 1–30 and grep
   output. Most past bugs (double connectives, ", ." artifacts) were found this way.

## Tune the scorer

- Thresholds live in `_norm(...)` calls in `analysis.py` (calibrated: raw AI 58–76,
  humanized 22–46, human ~20).
- Keep the human sample scoring <25 — if a recalibration pushes humans up, you've broken
  the false-positive story.
- Common gotcha: `_norm(v, lo, hi)` with `hi < lo` inverts (that's intended for
  "lower-is-worse" metrics). `_norm` returns 0.0 only when `hi == lo`.

## Add a preset (e.g. "newsletter")

1. `pipeline.PRESETS` list + per-transform factors in `pipeline.humanize`
   (`c_prob` contraction factor, `rare_word_spice` factor, discourse-marker factor).
2. Validate it in `routes_v1.create_humanize_job` (preset check) and in the OpenAPI
   description.

## Add AI vocabulary to scrub

- Single words → `resources.AI_WORDS` (word → [alternatives]; capitalize-aware)
- Phrases → `resources.AI_PHRASES` list of `(regex, [replacement templates])`
- Verify replacements are grammatical in-context: the templates are plain string
  interpolations, so trailing commas/spaces matter.

## Change the schema

1. New file `app/migrations/002_<desc>.sql` (plain SQL, runs in a transaction with the
   version insert).
2. Test locally: `python -m app.migrate` against a scratch DATABASE_URL.
3. Deploy: migrations run automatically in the Railway start command before uvicorn.
4. Never edit `001_init.sql` (already applied — the runner would skip it silently).

## Change limits / concurrency

All in env vars (see configuration.md). `JOB_CONCURRENCY` is read live per acquire —
no code change needed, just `railway variable set --service api JOB_CONCURRENCY=5`
(restarts the service).

## Split the worker into its own service (if you outgrow 512 MB)

(This module does not exist yet — it is a recipe for when you outgrow the single service.)

1. New Railway service from the same repo; start command:
   `python -m app.migrate && WORKER_ENABLED=… python -m app.worker_only`
2. Add `app/worker_only.py`: minimal asyncio runner that calls
   `redis ping → recover_orphans → Worker().run()` under a signal-handler stop.
3. Set `WORKER_ENABLED=false` on the API service. Queue/slots already coordinate
   across instances via Redis — no other change needed.

## Add auth scopes / roles

- `users.role` exists (`user|admin`), `api_keys.scopes` is a TEXT[] (default `{humanize}`).
- Enforcement point: `AuthContext` in `app/auth.py` + per-endpoint checks in routes.
- Pattern to copy: `_require_clerk` in routes_v1.py (raises before doing work).

## Point at a custom domain (example.com)

1. Railway: add `api.example.com` custom domain to the api service; set DNS CNAME.
2. `railway variable set --service api PUBLIC_BASE_URL=https://api.example.com
   ALLOWED_ORIGINS=https://example.com`
3. Clerk: move to a production instance (domain verification), update
   `CLERK_ISSUER` / `CLERK_JWKS_URL` / optionally `CLERK_AUTHORIZED_PARTIES`.
4. Update README/docs URLs (they reference the Railway domain today).

## Meaning-preservation invariants (do not regress)

- Any new transform must keep `semantic_check` passing: never drop content words outside
  `DROPPABLE`, never invent outside `ALLOWLIST`, never alter numbers/negations/proper nouns.
- If a transform legitimately replaces a word (synonym swap), add it to the right table —
  swap keys/outputs are auto-added to DROPPABLE/ALLOWLIST at import; phrase-pattern
  literals are extracted by `_pattern_words` (handles `[Ww]` classes, `\b`, hyphens).
- Templates must not create fragments or double conjunctions; `repair_fragments` runs
  before `rewrite_burstiness`, and splits require a finite verb in the first half.
- `difflib.SequenceMatcher` needs `autojunk=False` (junking common chars produced
  nonsense similarity values on long strings).

## Things that have bitten before (do not regress)

- `db.query/execute` are **blocking** — always off-thread from async code.
- `PyJWKClient` without `timeout=10` can hang the event loop (was a real prod bug).
- Redis `MaxConnectionsError`/timeouts under bursts: pool is non-blocking with retries;
  the limiter fails open — don't wrap guard() so failures 500 again.
- FakeDB in `tests/conftest.py` mirrors SQL string patterns; if you change SQL strings
  in routes/queue, update the FakeDB matchers or tests will silently pass/fail oddly.
- Sentence-split regex (`SENT_SPLIT_RE`) requires `[A-Z"']` after `.` — lowercase
  sentence starts are handled by `_recapitalize` in transforms, not the splitter.
