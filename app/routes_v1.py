import asyncio
import hashlib
import json
import logging
import uuid
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from . import db
from .api_keys import generate_api_key
from .auth import AuthContext, authenticate
from .config import settings
from .engine import analysis
from .queue import enqueue, get_job_state
from .redis_client import get_redis
from .ratelimit import check_limits
from .redis_client import get_redis

log = logging.getLogger("hoomanise.v1")
router = APIRouter(prefix="/v1", tags=["v1"])

import asyncio as _asyncio

_ANALYZE_SEM = _asyncio.Semaphore(max(1, settings.analyze_concurrency))


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return value.isoformat()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # Railway's edge appends the real peer last; the head of the list is client-controlled.
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def credits_for(text: str) -> int:
    words = len(analysis.words(text))
    return max(1, -(-words // max(1, settings.words_per_credit)))


def _usage_and_grant(user_id: str):
    return db.query(
        "SELECT COALESCE(SUM(usage_records.credits_used), 0) AS used,"
        " MAX(users.free_credits) AS granted"
        " FROM users LEFT JOIN usage_records ON usage_records.user_id = users.id"
        " WHERE users.id = %s GROUP BY users.id",
        (user_id,),
        one=True,
    )


async def _charge_or_429(user_id, api_key_id, job_id, endpoint, cost, status="reserved", input_words=None):
    from .credits import InsufficientCredits, charge

    try:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: charge(user_id, api_key_id, job_id, endpoint, cost, status=status, input_words=input_words)
        )
    except InsufficientCredits as e:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "credits_exhausted",
                "message": f"Free credits exhausted. Contact {settings.sales_email} to continue.",
                "free_credits": e.granted,
                "used": e.used,
                "remaining": max(0, e.granted - e.used),
                "sales_email": settings.sales_email,
            },
        )


def require_scope(ctx: AuthContext, scope: str) -> None:
    if ctx.method == "clerk":
        return
    scopes = ctx.api_key_scopes or []
    if scope not in scopes:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "insufficient_scope",
                "message": f"this API key lacks the required scope '{scope}'",
                "required_scope": scope,
            },
        )


async def _charge_after_insert(ctx, job_id, endpoint, cost, status="reserved", input_words=None, delete_on_fail: bool = True):
    import asyncio

    try:
        await _charge_or_429(ctx.user_id, ctx.api_key_id, job_id, endpoint, cost, status=status, input_words=input_words)
    except HTTPException:
        if delete_on_fail:
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: db.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
                )
            except Exception:
                pass
        raise


async def _ensure_queue_capacity(ctx: AuthContext, n: int) -> None:
    import asyncio

    def _outstanding():
        return db.query(
            "SELECT COUNT(*) AS c FROM jobs WHERE user_id = %s AND status IN ('queued','processing')",
            (ctx.user_id,),
            one=True,
        )

    row = await asyncio.get_running_loop().run_in_executor(None, _outstanding)
    outstanding = row["c"] if row else 0
    if outstanding + n > settings.max_outstanding_jobs_per_user:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "too_many_jobs",
                "message": f"you have {outstanding} jobs in flight; the limit is {settings.max_outstanding_jobs_per_user}",
                "outstanding": outstanding,
                "limit": settings.max_outstanding_jobs_per_user,
            },
            headers={"Retry-After": "30"},
        )
    from .queue import QUEUE_KEY

    depth = await get_redis().llen(QUEUE_KEY)
    if depth + n > settings.max_queue_depth:
        raise HTTPException(
            status_code=503,
            detail={"code": "queue_full", "message": "the processing queue is full; try again shortly"},
            headers={"Retry-After": "60"},
        )


async def guard(request: Request, ctx: AuthContext | None = None, cost: int = 0, costly: bool = False) -> AuthContext:
    ctx = ctx or await authenticate(request)
    try:
        result = await check_limits(
            user_id=ctx.user_id if ctx.method in ("api_key", "clerk") else None,
            ip=client_ip(request),
            user_per_min=settings.rate_limit_user_per_min,
            user_per_day=settings.rate_limit_user_per_day,
            ip_per_min=settings.rate_limit_ip_per_min,
            global_per_min=settings.rate_limit_global_per_min,
        )
    except Exception as e:
        if costly:
            raise HTTPException(
                status_code=503,
                detail={"code": "service_unavailable", "message": "rate limiter unavailable; retry shortly"},
                headers={"Retry-After": "30"},
            )
        log.warning("rate limiter unavailable, failing open", extra={"error": type(e).__name__})
        return ctx
    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": f"rate limit exceeded ({result['scope']})",
                "scope": result["scope"],
                "retry_after_seconds": result["retry_after"],
            },
            headers={
                "Retry-After": str(result["retry_after"]),
                **result["headers"],
            },
        )
    return ctx


@router.post("/humanize", status_code=202)
async def create_humanize_job(request: Request, body: dict):
    ctx = await authenticate(request)
    require_scope(ctx, "humanize")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "'text' must be a non-empty string"})
    if len(text) > settings.max_input_chars:
        raise HTTPException(status_code=413, detail={"code": "input_too_large", "message": f"maximum {settings.max_input_chars} characters"})
    preset = body.get("preset", "casual")
    intensity = body.get("intensity", 70)
    if preset not in ("casual", "professional", "academic"):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "preset must be casual|professional|academic"})
    try:
        intensity = max(0, min(100, int(intensity)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "intensity must be an integer 0-100"})

    preserve_formatting = body.get("preserve_formatting", True)
    if not isinstance(preserve_formatting, bool):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "preserve_formatting must be a boolean"})

    input_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cache_key = f"hoomanise:cache:{ctx.user_id}:{input_sha}:{preset}:{intensity}:{preserve_formatting}"
    cached = None
    if settings.response_cache_enabled:
        try:
            cached = await get_redis().hgetall(cache_key) or None
        except Exception:
            cached = None

    await guard(request, ctx=ctx, costly=True)
    await _ensure_queue_capacity(ctx, 1)
    words = len(analysis.words(text))
    cost = credits_for(text)
    job_id = str(uuid.uuid4())

    if cached and cached.get("result"):
        def _insert_cached():
            db.execute(
                "INSERT INTO jobs (id, user_id, api_key_id, status, preset, intensity, input_sha256, input_chars, input_text, preserve_formatting, result_text, metadata, completed_at)"
                " VALUES (%s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, %s, now())",
                (job_id, ctx.user_id, ctx.api_key_id, preset, intensity, input_sha, len(text), text, preserve_formatting,
                 cached.get("result"), cached.get("metadata")),
            )

        await asyncio.get_running_loop().run_in_executor(None, _insert_cached)
        await _charge_after_insert(ctx, job_id, "/v1/humanize", cost, status="completed", input_words=words)
        db.execute(
            "INSERT INTO audit_log (user_id, api_key_id, action, detail) VALUES (%s, %s, 'job_created', %s)",
            (ctx.user_id, ctx.api_key_id, json.dumps({"job_id": job_id, "cached": True, "chars": len(text)})),
        )
        return {"job_id": job_id, "status": "completed", "cached": True, "poll_url": f"/v1/jobs/{job_id}"}

    def _insert():
        db.execute(
            "INSERT INTO jobs (id, user_id, api_key_id, status, preset, intensity, input_sha256, input_chars, input_text, preserve_formatting)"
            " VALUES (%s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s)",
            (job_id, ctx.user_id, ctx.api_key_id, preset, intensity, input_sha, len(text), text, preserve_formatting),
        )

    await asyncio.get_running_loop().run_in_executor(None, _insert)
    await _charge_after_insert(ctx, job_id, "/v1/humanize", cost, input_words=words)
    await enqueue(job_id)
    db.execute(
        "INSERT INTO audit_log (user_id, api_key_id, action, detail) VALUES (%s, %s, 'job_created', %s)",
        (ctx.user_id, ctx.api_key_id, json.dumps({"job_id": job_id, "chars": len(text)})),
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "poll_url": f"/v1/jobs/{job_id}",
    }


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str):
    ctx = await guard(request)
    require_scope(ctx, "jobs:read")
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "invalid job id"})
    state = await get_job_state(job_id)
    row = await asyncio.get_running_loop().run_in_executor(
        None,
        db.query,
        "SELECT id, status, preset, intensity, input_chars, result_text, error, created_at, started_at, completed_at"
        " FROM jobs WHERE id = %s AND user_id = %s",
        (job_id, ctx.user_id),
        True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "job not found"})
    state = state or {}
    status = row["status"] if row else state.get("state", "queued")
    resp = {
        "job_id": job_id,
        "status": status,
        "preset": row["preset"] if row else None,
        "intensity": row["intensity"] if row else None,
        "created_at": _iso(row["created_at"]) if row else None,
        "completed_at": _iso(row["completed_at"]) if row else None,
        "error": row.get("error") if row else state.get("error"),
    }
    if row and row.get("result_text"):
        resp["result"] = row["result_text"]
        score_val = None
        if state.get("score_after"):
            try:
                score_val = float(state["score_after"])
            except (TypeError, ValueError):
                score_val = None
        resp["style_score_after"] = {
            "value": score_val,
            "scale": "0-100",
            "kind": "internal_heuristic",
            "note": "internal style heuristic, not a validated AI detector; treat as advisory",
        }
        if row.get("metadata"):
            resp["metadata"] = row["metadata"]
        elif state.get("metadata"):
            import json as _json
            try:
                resp["metadata"] = _json.loads(state["metadata"])
            except (ValueError, TypeError):
                pass
    return resp


@router.delete("/jobs/{job_id}")
async def delete_job(request: Request, job_id: str):
    ctx = await guard(request)
    require_scope(ctx, "jobs:read")
    import uuid

    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "invalid job id"})
    row = db.query(
        "DELETE FROM jobs WHERE id = %s AND user_id = %s RETURNING id",
        (job_id, ctx.user_id),
        one=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "job not found"})
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'job_deleted', %s)",
        (ctx.user_id, json.dumps({"job_id": job_id})),
    )
    return {"job_id": job_id, "deleted": True}


@router.get("/jobs")
async def list_jobs(request: Request, limit: int = 20, status: str | None = None):
    ctx = await guard(request)
    require_scope(ctx, "jobs:read")
    limit = max(1, min(100, limit))
    if status and status not in ("queued", "processing", "completed", "failed"):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "invalid status filter"})
    rows = db.query(
        "SELECT id, status, preset, created_at, completed_at FROM jobs WHERE user_id = %s"
        + (" AND status = %s" if status else "")
        + " ORDER BY created_at DESC LIMIT %s",
        (ctx.user_id, status, limit) if status else (ctx.user_id, limit),
    )
    return {
        "jobs": [
            {
                "job_id": str(r["id"]),
                "status": r["status"],
                "preset": r["preset"],
                "created_at": _iso(r["created_at"]),
                "completed_at": _iso(r.get("completed_at")),
            }
            for r in rows
        ]
    }


@router.post("/analyze")
async def analyze(request: Request, body: dict):
    ctx = await authenticate(request)
    require_scope(ctx, "humanize")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "'text' must be a non-empty string"})
    if len(text) > settings.max_input_chars:
        raise HTTPException(status_code=413, detail={"code": "input_too_large", "message": f"maximum {settings.max_input_chars} characters"})
    await guard(request, ctx=ctx, costly=True)
    await _charge_or_429(
        ctx.user_id, ctx.api_key_id, None, "/v1/analyze", credits_for(text),
        status="completed", input_words=len(analysis.words(text)),
    )
    async with _ANALYZE_SEM:
        result = await asyncio.get_running_loop().run_in_executor(None, analysis.full_analysis, text)
    return result


@router.get("/me")
async def me(request: Request):
    ctx = await guard(request)
    require_scope(ctx, "jobs:read")
    usage = db.query(
        "SELECT COUNT(*) AS total_requests, COALESCE(SUM(credits_used),0) AS credits"
        " FROM usage_records WHERE user_id = %s AND created_at > now() - interval '30 days'",
        (ctx.user_id,),
        one=True,
    )
    keys = db.query(
        "SELECT COUNT(*) AS active_keys FROM api_keys WHERE user_id = %s AND revoked_at IS NULL",
        (ctx.user_id,),
        one=True,
    )
    used = usage["credits"] if usage else 0
    grant_row = db.query("SELECT free_credits FROM users WHERE id = %s", (ctx.user_id,), one=True)
    granted = (grant_row or {}).get("free_credits") or settings.free_credits_default
    return {
        "user_id": ctx.user_id,
        "auth_method": ctx.method,
        "email": ctx.email,
        "display_name": ctx.display_name,
        "role": ctx.role,
        "usage_30d": {
            "requests": usage["total_requests"] if usage else 0,
            "credits": used,
        },
        "credits": {
            "granted": granted,
            "used": used,
            "remaining": max(0, granted - used),
            "words_per_credit": settings.words_per_credit,
            "words_remaining": max(0, granted - used) * settings.words_per_credit,
            "sales_email": settings.sales_email,
        },
        "active_api_keys": keys["active_keys"] if keys and "active_keys" in keys else (keys["c"] if keys and "c" in keys else 0),
    }


@router.post("/keys", status_code=201)
async def create_key(request: Request, body: dict):
    claims = await _require_clerk(request)
    name = body.get("name") or "default"
    if not isinstance(name, str) or len(name) > 64:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "'name' must be a string <= 64 chars"})
    user_row = db.query(
        "SELECT * FROM users WHERE clerk_id = %s AND is_active",
        (claims["sub"],),
        one=True,
    )
    if not user_row:
        user_row = await authenticate(request)
        user_row = db.query("SELECT * FROM users WHERE id = %s", (user_row.user_id,), one=True)

    existing = db.query(
        "SELECT COUNT(*) AS c FROM api_keys WHERE user_id = %s AND revoked_at IS NULL",
        (user_row["id"],),
        one=True,
    )
    if existing and existing["c"] >= 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "key_exists",
                "message": "you already have an active API key; use POST /v1/keys/regenerate to replace it",
            },
        )

    generated = generate_api_key()
    row = db.query(
        "INSERT INTO api_keys (user_id, name, prefix, key_hash) VALUES (%s, %s, %s, %s) RETURNING id, name, prefix, created_at",
        (user_row["id"], name, generated.prefix, generated.key_hash),
        one=True,
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'api_key_created', %s)",
        (user_row["id"], json.dumps({"key_id": str(row["id"]), "name": name})),
    )
    log.info("api key created", extra={"user_id": str(user_row["id"]), "key_id": str(row["id"])})
    return {
        "key_id": str(row["id"]),
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": _iso(row["created_at"]),
        "full_key": generated.full_key,
        "warning": "Store this key now. It will never be shown again.",
    }


@router.post("/keys/regenerate", status_code=201)
async def regenerate_key(request: Request, body: dict | None = None):
    claims = await _require_clerk(request)
    user_row = db.query(
        "SELECT id FROM users WHERE clerk_id = %s AND is_active",
        (claims["sub"],),
        one=True,
    )
    if not user_row:
        ctx = await authenticate(request)
        user_row = db.query("SELECT id FROM users WHERE id = %s", (ctx.user_id,), one=True)

    db.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
        (user_row["id"],),
    )
    generated = generate_api_key()
    name = (body or {}).get("name") or "default"
    if not isinstance(name, str) or len(name) > 64:
        name = "default"
    row = db.query(
        "INSERT INTO api_keys (user_id, name, prefix, key_hash) VALUES (%s, %s, %s, %s) RETURNING id, name, prefix, created_at",
        (user_row["id"], name, generated.prefix, generated.key_hash),
        one=True,
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'api_key_regenerated', %s)",
        (user_row["id"], json.dumps({"key_id": str(row["id"]), "name": name})),
    )
    log.info("api key regenerated", extra={"user_id": str(user_row["id"]), "key_id": str(row["id"])})
    return {
        "key_id": str(row["id"]),
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": _iso(row["created_at"]),
        "full_key": generated.full_key,
        "warning": "Your previous key has been revoked. Store this new key now; it will never be shown again.",
    }


@router.get("/keys")
async def list_keys(request: Request):
    ctx = await guard(request)
    require_scope(ctx, "keys:manage")
    rows = db.query(
        "SELECT id, name, prefix, revoked_at, last_used_at, created_at FROM api_keys"
        " WHERE user_id = %s ORDER BY created_at DESC",
        (ctx.user_id,),
    )
    return {
        "keys": [
            {
                "key_id": str(r["id"]),
                "name": r["name"],
                "prefix": r["prefix"],
                "created_at": _iso(r["created_at"]),
                "last_used_at": _iso(r.get("last_used_at")),
                "revoked": r["revoked_at"] is not None,
            }
            for r in rows
        ]
    }


@router.delete("/keys/{key_id}", status_code=200)
async def revoke_key(request: Request, key_id: str):
    ctx = await guard(request)
    require_scope(ctx, "keys:manage")
    try:
        uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "invalid key id"})
    row = db.query(
        "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND user_id = %s AND revoked_at IS NULL RETURNING id",
        (key_id, ctx.user_id),
        one=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "key not found or already revoked"})
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'api_key_revoked', %s)",
        (ctx.user_id, json.dumps({"key_id": key_id})),
    )
    return {"key_id": key_id, "revoked": True}


async def _require_clerk(request: Request) -> dict:
    from .api_keys import extract_bearer_token

    token = extract_bearer_token(request.headers.get("Authorization"))
    if not token or token.startswith(settings.api_key_prefix):
        raise HTTPException(status_code=401, detail={"code": "clerk_auth_required", "message": "this endpoint requires Clerk dashboard authentication, not an API key"})
    ctx = await authenticate(request)
    return {"sub": ctx.clerk_id}




@router.post("/humanize/batch", status_code=200)
async def create_humanize_batch(request: Request, body: dict):
    ctx = await guard(request, costly=True)
    require_scope(ctx, "humanize")
    texts = body.get("texts")
    if not isinstance(texts, list) or not texts:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "'texts' must be a non-empty list"})
    if len(texts) > settings.rate_limit_batch_max:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": f"maximum {settings.rate_limit_batch_max} texts per batch"})
    preset = body.get("preset", "casual")
    intensity = body.get("intensity", 70)
    preserve = body.get("preserve_formatting", True)
    if preset not in ("casual", "professional", "academic"):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "preset must be casual|professional|academic"})
    try:
        intensity = max(0, min(100, int(intensity)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "intensity must be an integer 0-100"})
    if not isinstance(preserve, bool):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "preserve_formatting must be a boolean"})

    import hashlib

    await _ensure_queue_capacity(ctx, len(texts))
    jobs = []
    for t in texts:
        if not isinstance(t, str) or not t.strip():
            raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "every entry in 'texts' must be a non-empty string"})
        if len(t) > settings.max_input_chars:
            raise HTTPException(status_code=413, detail={"code": "input_too_large", "message": f"each text is limited to {settings.max_input_chars} characters"})

    for t in texts:
        input_sha = hashlib.sha256(t.encode("utf-8")).hexdigest()
        job_id = str(uuid.uuid4())

        def _insert(job_id=job_id, t=t, input_sha=input_sha):
            db.execute(
                "INSERT INTO jobs (id, user_id, api_key_id, status, preset, intensity, input_sha256, input_chars, input_text, preserve_formatting)"
                " VALUES (%s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s)",
                (job_id, ctx.user_id, ctx.api_key_id, preset, intensity, input_sha, len(t), t, preserve),
            )

        await asyncio.get_running_loop().run_in_executor(None, _insert)
        await _charge_after_insert(ctx, job_id, "/v1/humanize/batch", credits_for(t),
                                   input_words=len(analysis.words(t)))
        await enqueue(job_id)
        jobs.append({"job_id": job_id, "status": "queued", "poll_url": f"/v1/jobs/{job_id}"})
    db.execute(
        "INSERT INTO audit_log (user_id, api_key_id, action, detail) VALUES (%s, %s, 'batch_created', %s)",
        (ctx.user_id, ctx.api_key_id, json.dumps({"count": len(jobs)})),
    )
    return {"batch_size": len(jobs), "jobs": jobs}


@router.get("/jobs/{job_id}/stream")
async def stream_job(request: Request, job_id: str):
    ctx = await guard(request)
    require_scope(ctx, "jobs:read")
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "invalid job id"})
    row = await asyncio.get_running_loop().run_in_executor(
        None,
        db.query,
        "SELECT id, user_id FROM jobs WHERE id = %s AND user_id = %s",
        (job_id, ctx.user_id),
        True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "job not found"})

    from fastapi.responses import StreamingResponse

    async def event_stream():
        deadline = asyncio.get_event_loop().time() + 120
        last = None
        while asyncio.get_event_loop().time() < deadline:
            state = await get_job_state(job_id) or {}
            status = state.get("state", "queued")
            if status != last:
                yield f"event: state\ndata: {json.dumps({'job_id': job_id, 'status': status})}\n\n"
                last = status
            if status in ("completed", "failed"):
                break
            await asyncio.sleep(1)
        yield f"event: done\ndata: {json.dumps({'job_id': job_id, 'status': last})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/webhooks", status_code=201)
async def create_webhook(request: Request, body: dict):
    claims = await _require_clerk(request)
    url = body.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "'url' must be an https URL"})
    if len(url) > 500:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "url too long"})
    user_row = db.query(
        "SELECT id FROM users WHERE clerk_id = %s AND is_active",
        (claims["sub"],),
        one=True,
    )
    if not user_row:
        user_row = await authenticate(request)
        user_row = db.query("SELECT id FROM users WHERE id = %s", (user_row.user_id,), one=True)
    existing = db.query(
        "SELECT COUNT(*) AS c FROM webhook_endpoints WHERE user_id = %s AND disabled_at IS NULL",
        (user_row["id"],),
        one=True,
    )
    if existing and existing["c"] >= settings.webhook_max_per_user:
        raise HTTPException(status_code=409, detail={"code": "webhook_limit", "message": f"maximum {settings.webhook_max_per_user} active webhooks"})
    from .queue import webhook_secret
    from .queue import safe_webhook_url

    if not safe_webhook_url(url):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_input", "message": "url must be a public https endpoint (private/internal hosts are rejected)"},
        )

    row = db.query(
        "INSERT INTO webhook_endpoints (user_id, url) VALUES (%s, %s) RETURNING id, url, created_at",
        (user_row["id"], url),
        one=True,
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'webhook_created', %s)",
        (user_row["id"], json.dumps({"webhook_id": str(row["id"]), "url": url})),
    )
    log.info("webhook created", extra={"user_id": str(user_row["id"]), "webhook_id": str(row["id"])})
    return {
        "webhook_id": str(row["id"]),
        "url": row["url"],
        "signing_secret": webhook_secret(str(row["id"])),
        "warning": "Store this secret now. It will never be shown again. Deliveries are signed with X-Hoomanise-Signature: sha256=hex(HMAC-SHA256(secret, body)).",
    }


@router.get("/webhooks")
async def list_webhooks(request: Request):
    ctx = await guard(request)
    require_scope(ctx, "webhooks:manage")
    rows = db.query(
        "SELECT id, url, disabled_at, last_delivery_at, last_status, created_at FROM webhook_endpoints"
        " WHERE user_id = %s ORDER BY created_at DESC",
        (ctx.user_id,),
    )
    return {
        "webhooks": [
            {
                "webhook_id": str(w["id"]),
                "url": w["url"],
                "created_at": _iso(w["created_at"]),
                "last_delivery_at": _iso(w.get("last_delivery_at")),
                "last_status": w.get("last_status"),
                "disabled": w["disabled_at"] is not None,
            }
            for w in rows
        ]
    }


@router.delete("/webhooks/{webhook_id}")
async def disable_webhook(request: Request, webhook_id: str):
    ctx = await guard(request)
    require_scope(ctx, "webhooks:manage")
    try:
        uuid.UUID(webhook_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_input", "message": "invalid webhook id"})
    row = db.query(
        "UPDATE webhook_endpoints SET disabled_at = now() WHERE id = %s AND user_id = %s AND disabled_at IS NULL RETURNING id",
        (webhook_id, ctx.user_id),
        one=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "webhook not found or already disabled"})
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'webhook_disabled', %s)",
        (ctx.user_id, json.dumps({"webhook_id": webhook_id})),
    )
    return {"webhook_id": webhook_id, "disabled": True}
