import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import generate_request_id, settings
from .db import close_pool, get_pool
from .redis_client import close_redis, get_redis

class JsonFormatter(logging.Formatter):
    def format(self, record):
        import json as _json

        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for k, v in getattr(record, "__dict__", {}).items():
            if k in ("args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
                     "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
                     "name", "pathname", "process", "processName", "relativeCreated",
                     "stack_info", "thread", "threadName", "taskName"):
                continue
            try:
                _json.dumps(v)
                entry[k] = v
            except (TypeError, ValueError):
                entry[k] = str(v)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return _json.dumps(entry)


_handler = logging.StreamHandler()
if settings.log_format == "json":
    _handler.setFormatter(JsonFormatter())
else:
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logging.basicConfig(level=settings.log_level, handlers=[_handler], force=True)
log = logging.getLogger("hoomanise")

START_TIME = time.time()
_request_id_ctxvar = None


import random as _random


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(settings.request_id_header) or generate_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[settings.request_id_header] = request_id
        if settings.log_sample >= 1.0 or _random.random() < settings.log_sample:
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                },
            )
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_body_bytes:
                return _too_large()
            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > settings.max_body_bytes:
                    return _too_large()
            request._body = body
        return await call_next(request)


def _too_large():
    return JSONResponse(
        status_code=413,
        content={"error": {"code": "payload_too_large", "message": f"maximum body size is {settings.max_body_bytes} bytes"}},
    )


class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.endswith("/stream") or request.url.path in ("/dashboard", "/"):
            return await call_next(request)
        try:
            return await asyncio.wait_for(call_next(request), timeout=settings.request_timeout_seconds)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"error": {"code": "timeout", "message": f"request exceeded {settings.request_timeout_seconds}s"}},
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.headers.get("authorization") or request.url.path.startswith(("/v1/keys", "/v1/webhooks")):
            response.headers["Cache-Control"] = "no-store, private"
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
                "https://*.clerk.accounts.dev https://*.clerk.com; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self' https://api.clerk.com https://*.clerk.accounts.dev "
                "https://*.clerk.com; frame-src https://*.clerk.accounts.dev https://*.clerk.com; "
                "base-uri 'self'; form-action 'self'"
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.env == "production" and not settings.api_key_pepper:
        raise RuntimeError("API_KEY_PEPPER must be set in production (API keys and webhook signatures depend on it)")
    ready = {"db": False, "redis": False, "worker": False}
    app.state.ready = ready
    try:
        get_pool().check()
        ready["db"] = True
    except Exception as e:
        log.error("database unavailable at startup", extra={"error": str(e)})
    try:
        await get_redis().ping()
        ready["redis"] = True
    except Exception as e:
        log.error("redis unavailable at startup", extra={"error": str(e)})

    if ready["db"] and ready["redis"] and settings.worker_enabled:
        from .queue import start_worker

        start_worker()
        ready["worker"] = True

    log.info("startup complete", extra=ready)
    yield

    if ready["worker"]:
        from .queue import stop_worker

        await stop_worker()
    await asyncio.sleep(0.1)
    await close_redis()
    close_pool()
    log.info("shutdown complete")


app = FastAPI(
    title="Humanise API",
    version=settings.version,
    description=(
        "AI-text humanizer API. Detects and defeats the major AI-detection signals: "
        "perplexity, burstiness, stylometry, AI-phrase density, DetectGPT-style curvature, "
        "supervised classifiers, formatting fingerprints, generative watermarks, and vendor accents."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

base_url = os.environ.get("PUBLIC_BASE_URL", "")
if base_url:
    app.servers = [{"url": base_url.rstrip("/")}]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(TimeoutMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", None) or generate_request_id()
    detail = exc.detail
    if isinstance(detail, str):
        body = {"code": _status_code_name(exc.status_code), "message": detail}
    elif isinstance(detail, dict):
        body = detail
    else:
        body = {"code": "error", "message": "request failed"}
    if exc.status_code >= 500:
        log.error("http error", extra={"request_id": request_id, "status": exc.status_code})
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {**body, "request_id": request_id}},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", None) or generate_request_id()
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_input",
                "message": "request validation failed",
                "details": [
                    {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
                    for e in exc.errors()
                ],
                "request_id": request_id,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or generate_request_id()
    log.exception("unhandled error", extra={"request_id": request_id})
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "an internal error occurred",
                "request_id": request_id,
            }
        },
    )


def _status_code_name(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "invalid_input",
        429: "rate_limited",
        500: "internal_error",
        503: "service_unavailable",
    }.get(status, "error")


@app.get("/health", tags=["system"])
def health():
    return {
        "ok": True,
        "service": settings.app_name,
        "version": settings.version,
        "uptime_seconds": round(time.time() - START_TIME, 1),
    }


@app.get("/readyz", tags=["system"])
async def readyz():
    checks = {}
    try:
        get_pool().check()
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"fail: {type(e).__name__}"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"fail: {type(e).__name__}"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks},
    )


from fastapi.responses import HTMLResponse

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Humanise Dashboard</title>
<style>
body{font-family:-apple-system,Segoe UI,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#222}
h1{font-size:1.4rem} section{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}
button{padding:.4rem .8rem;margin:.2rem .2rem;cursor:pointer}
input,textarea{width:100%;padding:.4rem;margin:.3rem 0;box-sizing:border-box}
pre{background:#f4f4f4;padding:.5rem;overflow-x:auto;border-radius:4px;font-size:.85rem}
.muted{color:#777;font-size:.85rem}
</style>
<script>
window.__PK__ = "__PK__"; window.__BASE__ = "__BASE__";
</script>
<script async crossorigin="anonymous" data-clerk-publishable-key="__PK__" src="https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js"></script>
</head>
<body>
<h1>Humanise API Dashboard</h1>
<p class="muted">Sign in with Clerk, then manage API keys and jobs. All calls hit this API directly.</p>
<section>
<h2>Your API key</h2>
<div id="keybox" class="muted">Loading…</div>
<button id="regen">Regenerate key</button>
<span class="muted" id="keynote"></span>
</section>
<section>
<h2>Humanize</h2><section>
<h2>Humanize</h2>
<textarea id="text" rows="6" placeholder="Paste AI text here…"></textarea>
<button id="go">Submit</button> <select id="preset"><option>casual</option><option>professional</option><option>academic</option></select>
<select id="intensity"><option>20</option><option>50</option><option selected>70</option><option>90</option></select>
<div id="job"></div>
</section>
<script>
let clerkReady = new Promise(res => window.addEventListener('clerk-loaded', () => window.Clerk.load({publishableKey: window.__PK__}).then(res)));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function authed(path, opts={}) {
  await clerkReady;
  const token = await window.Clerk.session?.getToken();
  if (!token) { await window.Clerk.redirectToSignIn(); return null; }
  const r = await fetch(path, {...opts, headers: {...(opts.headers||{}), 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}});
  if (r.status === 401) { await window.Clerk.redirectToSignIn(); return null; }
  return r;
}
async function loadKeys() {
  const r = await authed('/v1/keys'); if (!r) return;
  const d = await r.json();
  const active = (d.keys || []).find(k => !k.revoked);
  const box = document.getElementById('keybox');
  if (!active) { box.innerHTML = '<em>No key yet — click Regenerate key to create one.</em>'; return; }
  const revoked = (d.keys || []).filter(k => k.revoked).length;
  box.innerHTML = 'Active key: <code>' + esc(active.prefix) + '…</code> · created ' + esc((active.created_at||'').slice(0,10)) +
    (active.last_used_at ? ' · last used ' + esc(active.last_used_at.slice(0,10)) : ' · never used') +
    (revoked ? ' · ' + revoked + ' revoked' : '');
}
document.getElementById('regen').onclick = async () => {
  const note = document.getElementById('keynote');
  const r = await authed('/v1/keys/regenerate', {method:'POST', body: JSON.stringify({name:'dashboard'})});
  if (!r) return;
  const d = await r.json();
  if (!r.ok) { note.textContent = ' ' + (d.error ? d.error.message : 'failed'); return; }
  note.innerHTML = ' <strong>Copy now — shown once.</strong> Previous key revoked.';
  const box = document.getElementById('keybox');
  box.innerHTML = '<div>New key (copy it now):</div><code id="newkeytext"></code>';
  document.getElementById('newkeytext').textContent = d.full_key;
  loadKeys();
};
document.getElementById('go').onclick = async function(){
  const text = document.getElementById('text').value;
  const r = await authed('/v1/humanize', {method:'POST', body: JSON.stringify({text, preset: document.getElementById('preset').value, intensity: +document.getElementById('intensity').value})});
  if (!r) return;
  const d = await r.json();
  if (!r.ok) { document.getElementById('job').textContent = (d.error ? d.error.message : JSON.stringify(d)); return; }
  document.getElementById('job').textContent = 'queued: ' + d.job_id;
  const poll = setInterval(async () => {
    const rr = await authed('/v1/jobs/' + d.job_id); if (!rr) { clearInterval(poll); return; }
    const jj = await rr.json();
    if (jj.status === 'completed') { clearInterval(poll); document.getElementById('job').textContent = jj.result || ''; }
    else if (jj.status === 'failed') { clearInterval(poll); document.getElementById('job').textContent = 'failed: ' + (jj.error||''); }
    else document.getElementById('job').textContent = 'status: ' + jj.status;
  }, 1500);
};
clerkReady.then(() => { loadKeys(); });
</script>
</body></html>"""


SITE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _site_url() -> str:
    return os.environ.get("PUBLIC_SITE_URL", "https://example.com").rstrip("/")


def _render(name: str, content_type: str):
    from fastapi.responses import PlainTextResponse

    path = os.path.join(SITE_DIR, name)
    if not os.path.exists(path):
        return PlainTextResponse("not found", status_code=404)
    body = open(path, encoding="utf-8").read().replace("__SITE__", _site_url())
    if name.endswith(".html"):
        return HTMLResponse(body)
    return PlainTextResponse(body, media_type=content_type)


@app.get("/", include_in_schema=False)
def homepage():
    return _render("index.html", "text/html")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return _render("robots.txt", "text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return _render("sitemap.xml", "application/xml")


@app.get("/dashboard", tags=["system"], include_in_schema=False)
def dashboard():
    html = (
        DASHBOARD_HTML
        .replace("__PK__", os.environ.get("CLERK_PUBLISHABLE_KEY", ""))
        .replace("__BASE__", base_url or "")
    )
    return HTMLResponse(html)


from .routes_v1 import router as v1_router

app.include_router(v1_router)
