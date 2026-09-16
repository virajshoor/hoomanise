import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
import pytest_asyncio
from fakeredis import aioredis as fakeredis_aioredis

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_KEY_PEPPER", "test-pepper")

import app.redis_client as redis_client
from app.config import settings


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "_redis", fake)
    return fake


class _JWKSServer:
    def __init__(self, jwks: dict):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(outer.jwks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.jwks = jwks
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def mock_clerk(monkeypatch):
    import cryptography.hazmat.primitives.serialization as ser
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "test-key-1"
    n = key.public_key().public_numbers().n
    e = key.public_key().public_numbers().e

    def _int_to_b64(i):
        b = i.to_bytes((i.bit_length() + 7) // 8, "big")
        import base64 as b64

        return b64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": _int_to_b64(n), "e": _int_to_b64(e)}
        ]
    }
    srv = _JWKSServer(jwks)
    monkeypatch.setattr(
        settings, "clerk_jwks_url", f"http://127.0.0.1:{srv.port}/.well-known/jwks.json", raising=False
    )
    monkeypatch.setattr(settings, "clerk_issuer", "https://test.clerk.accounts.dev", raising=False)
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_jwk_client", None)
    yield {"key": key, "kid": kid}
    srv.stop()


def make_clerk_token(mock_clerk, sub="user_test123", issuer="https://test.clerk.accounts.dev", exp=None):
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": issuer,
        "exp": exp if exp is not None else now + 300,
        "iat": now,
        "azp": "http://localhost:3000",
    }
    return jwt.encode(payload, mock_clerk["key"], algorithm="RS256", headers={"kid": mock_clerk["kid"]})


class FakeDB:
    def __init__(self):
        self.jobs = {}
        self.users = {}
        self.keys = {}
        self.hooks = {}
        self.usage = []
        self.audit = []

    def query(self, sql, params=None, one=False):
        sql = " ".join(sql.split())
        if "SELECT COUNT" in sql and "api_keys" in sql:
            rows = [{"c": len([k for k in self.keys.values() if not k["revoked_at"]]), "active_keys": 0}]
        elif "FROM api_keys WHERE prefix" in sql:
            rows = [
                k for k in self.keys.values()
                if k["prefix"] == (params[0] if params else None) and k["revoked_at"] is None
            ]
        elif "FROM api_keys WHERE user_id" in sql:
            rows = [k for k in self.keys.values() if str(k["user_id"]) == str((params[0] if params else None))]
        elif "FROM users WHERE id" in sql:
            row = self.users.get(str((params[0] if params else None)))
            rows = [row] if row else []
        elif "FROM users WHERE clerk_id" in sql:
            rows = [u for u in self.users.values() if u.get("clerk_id") == (params[0] if params else None)]
        elif "INSERT INTO users" in sql:
            uid = str(uuid.uuid4())
            row = {
                "id": uid, "clerk_id": params[0], "email": params[1],
                "display_name": params[2], "role": "user", "is_active": True,
            }
            self.users[uid] = row
            rows = [row]
        elif "INSERT INTO api_keys" in sql:
            kid = str(uuid.uuid4())
            row = {
                "id": kid, "name": params[1], "prefix": params[2],
                "key_hash": params[3], "user_id": params[0], "revoked_at": None,
                "scopes": ["humanize", "jobs:read"], "created_at": time.time(), "last_used_at": None,
            }
            self.keys[kid] = row
            rows = [row]
        elif "UPDATE api_keys SET revoked_at" in sql:
            row = self.keys.get(str(params[0]))
            if row and str(row["user_id"]) == str(params[1]) and row["revoked_at"] is None:
                row["revoked_at"] = time.time()
                rows = [{"id": row["id"]}]
            else:
                rows = []
        elif "FROM jobs WHERE id" in sql:
            row = self.jobs.get(str((params[0] if params else None)))
            if row and "user_id = %s" in sql and str(row.get("user_id")) != str((params[1] if params and len(params) > 1 else None)):
                row = None
            rows = [row] if row else []
        elif "status IN ('queued','processing')" in sql:
            uid = str((params[0] if params else None))
            rows = [{"c": len([j for j in self.jobs.values()
                               if str(j.get("user_id")) == uid and j.get("status") in ("queued", "processing")])}]
        elif "FROM jobs WHERE user_id" in sql:
            rows = [j for j in self.jobs.values() if str(j.get("user_id")) == str((params[0] if params else None))]
        elif "FROM webhook_endpoints WHERE id" in sql and "UPDATE" not in sql:
            row = self.hooks.get(str((params[0] if params else None)))
            if row and "disabled_at IS NULL" in sql and row["disabled_at"] is not None:
                rows = []
            elif row:
                rows = [row]
            else:
                rows = []
        elif "FROM webhook_endpoints WHERE user_id" in sql and "COUNT" in sql:
            active = [w for w in self.hooks.values() if str(w["user_id"]) == str((params[0] if params else None)) and w["disabled_at"] is None]
            rows = [{"c": len(active)}]
        elif "FROM webhook_endpoints WHERE user_id" in sql:
            rows = [
                {k: w[k] for k in ("id", "url", "disabled_at", "last_delivery_at", "last_status", "created_at")}
                for w in self.hooks.values()
                if str(w["user_id"]) == str((params[0] if params else None))
            ]
        elif "INSERT INTO webhook_endpoints" in sql:
            wid = str(uuid.uuid4())
            row = {"id": wid, "user_id": params[0], "url": params[1],
                   "disabled_at": None, "last_delivery_at": None, "last_status": None, "created_at": time.time()}
            self.hooks[wid] = row
            rows = [row]
        elif "UPDATE webhook_endpoints SET disabled_at" in sql:
            row = self.hooks.get(str((params[0] if params else None)))
            if row and str(row["user_id"]) == str((params[1] if params else None)) and row["disabled_at"] is None:
                row["disabled_at"] = time.time()
                rows = [{"id": row["id"]}]
            else:
                rows = []
        elif "UPDATE webhook_endpoints SET last_delivery" in sql:
            rows = []
        elif "COALESCE(SUM(usage_records.credits_used)" in sql:
            uid = str((params[0] if params else None))
            user = self.users.get(uid, {})
            rows = [{"used": len([u for u in self.usage if str(u[0]) == uid]) if self.usage else 0,
                     "granted": user.get("free_credits", 600)}]
        elif "SELECT COALESCE(SUM(credits_used)" in sql:
            rows = [{"used": len(self.usage)}]
        elif "SELECT free_credits FROM users" in sql:
            user = self.users.get(str((params[0] if params else None)), {})
            rows = [{"free_credits": user.get("free_credits", 600)}]
        elif "SELECT COUNT" in sql and "usage_records" in sql:
            rows = [{"total_requests": len(self.usage), "credits": len(self.usage)}]
        else:
            rows = []
        if one:
            return rows[0] if rows else None
        return rows

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if "UPDATE api_keys SET revoked_at = now() WHERE user_id" in sql:
            for k in self.keys.values():
                if str(k["user_id"]) == str(params[0]) and k["revoked_at"] is None:
                    k["revoked_at"] = time.time()
            return
        if "INSERT INTO jobs" in sql and "'completed'" in sql:
            self.jobs[str(params[0])] = {
                "id": params[0], "user_id": params[1], "api_key_id": params[2],
                "status": "completed", "preset": params[3], "intensity": params[4],
                "input_text": params[7], "input_sha256": params[5],
                "created_at": time.time(), "completed_at": time.time(), "error": None,
                "result_text": params[9], "input_chars": params[6], "started_at": None,
                "preserve_formatting": params[8], "metadata": params[10] if len(params) > 10 else None,
            }
        elif "INSERT INTO jobs" in sql:
            self.jobs[str(params[0])] = {
                "id": params[0], "user_id": params[1], "api_key_id": params[2],
                "status": "queued", "preset": params[3], "intensity": params[4],
                "input_text": params[7], "input_sha256": params[5],
                "created_at": time.time(), "completed_at": None, "error": None,
                "result_text": None, "input_chars": params[6], "started_at": None,
            }
        elif "UPDATE jobs SET status='processing'" in sql:
            self.jobs[str(params[0])]["status"] = "processing"
        elif "UPDATE jobs SET status='completed'" in sql:
            self.jobs[str(params[2])]["status"] = "completed"
            self.jobs[str(params[2])]["result_text"] = params[0]
            self.jobs[str(params[2])]["metadata"] = params[1]
        elif "UPDATE jobs SET status='failed'" in sql:
            self.jobs[str(params[1])]["status"] = "failed"
            self.jobs[str(params[1])]["error"] = params[0]
        elif "INSERT INTO usage_records" in sql:
            self.usage.append(params)
        elif "UPDATE api_keys SET last_used_at" in sql:
            pass
        elif "INSERT INTO audit_log" in sql:
            self.audit.append(params)


@pytest.fixture
def mock_db(monkeypatch):
    fake = FakeDB()
    import app.db as db_mod
    import app.queue as queue_mod
    import app.credits as credits_mod

    monkeypatch.setattr(db_mod, "query", fake.query)
    monkeypatch.setattr(db_mod, "execute", fake.execute)
    monkeypatch.setattr(queue_mod.db, "query", fake.query)
    monkeypatch.setattr(queue_mod.db, "execute", fake.execute)

    def fake_charge(user_id, api_key_id, job_id, endpoint, credits, status="reserved", input_words=None):
        user = fake.users.get(str(user_id), {})
        granted = user.get("free_credits", 600)
        used = sum(u.get("credits_used", 0) for u in fake.usage
                   if isinstance(u, dict) and str(u.get("user_id")) == str(user_id))
        if used + credits > granted:
            raise credits_mod.InsufficientCredits(granted, used, credits)
        already = bool(job_id) and any(isinstance(u, dict) and str(u.get("job_id")) == str(job_id) for u in fake.usage)
        if not already:
            fake.usage.append({"user_id": user_id, "api_key_id": api_key_id, "job_id": job_id,
                               "endpoint": endpoint, "status": status, "credits_used": credits,
                               "input_words": input_words})
        return {"granted": granted, "used_before": used, "charged": not already}

    def fake_settle(job_id, *, status, input_words=None, output_words=None, score_before=None, score_after=None):
        for u in fake.usage:
            if isinstance(u, dict) and str(u.get("job_id")) == str(job_id):
                u["status"] = status
                u["input_words"] = input_words
                u["output_words"] = output_words

    def fake_refund(job_id):
        for u in fake.usage:
            if isinstance(u, dict) and str(u.get("job_id")) == str(job_id):
                u["credits_used"] = 0
                u["status"] = "refunded"

    monkeypatch.setattr(credits_mod, "charge", fake_charge)
    monkeypatch.setattr(credits_mod, "settle", fake_settle)
    monkeypatch.setattr(credits_mod, "refund", fake_refund)
    monkeypatch.setattr(queue_mod, "snapshot", getattr(queue_mod, "snapshot", None), raising=False)
    return fake


@pytest.fixture
def no_worker(monkeypatch):
    import app.queue as queue_mod

    monkeypatch.setattr(queue_mod, "start_worker", lambda: None)
    return None


@pytest.fixture
def real_worker(monkeypatch, mock_db):
    import app.queue as queue_mod



@pytest_asyncio.fixture
async def client(monkeypatch, mock_db, fake_redis, no_worker):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
