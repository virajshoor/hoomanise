import json
import os
import time

import pytest

os.environ.setdefault("APP_ENV", "test")

from tests.conftest import make_clerk_token
from tests.test_api import api_key_headers, auth_headers_for


@pytest.mark.asyncio
async def test_batch_endpoint(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    resp = await client.post(
        "/v1/humanize/batch",
        headers=headers,
        json={"texts": ["First text to humanize here with leverage.", "Second text to humanize here with delve."]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_size"] == 2
    assert len(body["jobs"]) == 2


@pytest.mark.asyncio
async def test_batch_limits(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    texts = ["text " * 5 for _ in range(12)]
    resp = await client.post("/v1/humanize/batch", headers=headers, json={"texts": texts})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_credits_exhausted(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    from app.config import settings

    original = settings.free_credits_default
    settings.free_credits_default = 3
    for u in mock_db.users.values():
        u["free_credits"] = 3
    try:
        for _ in range(4):
            await client.post("/v1/analyze", headers=headers, json={"text": "Some text to analyze here for credits."})
        r = await client.post("/v1/humanize", headers=headers, json={"text": "Text after exhausting the free credits."})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "credits_exhausted"
        assert "support@example.com" in r.json()["error"]["message"]
    finally:
        settings.free_credits_default = original


def test_credit_conversion():
    from app.config import settings
    from app.routes_v1 import credits_for

    assert settings.words_per_credit == 5
    assert credits_for("one two three four five") == 1
    assert credits_for("one two three four five six") == 2
    assert credits_for("x") == 1


@pytest.mark.asyncio
async def test_response_cache_hit(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    text = "Leveraging robust solutions is paramount. Moreover, it is important to note that caching works."
    import hashlib

    sha = hashlib.sha256(text.encode()).hexdigest()
    await fake_redis.hset(
        f"hoomanise:cache:{g.prefix and 'u1'}:{sha}:casual:70:True",
        mapping={"result": "CACHED RESULT", "style_score": "10"},
    )
    resp = await client.post("/v1/humanize", headers=headers, json={"text": text})
    assert resp.status_code == 202
    body = resp.json()
    assert body.get("cached") is True
    assert body["status"] == "completed"
    resp2 = await client.get(f"/v1/jobs/{body['job_id']}", headers=headers)
    assert resp2.json()["result"] == "CACHED RESULT"


@pytest.mark.asyncio
async def test_webhook_crud(client, mock_db, mock_clerk, fake_redis):
    clerk_headers = auth_headers_for(mock_clerk)
    resp = await client.post("/v1/webhooks", headers=clerk_headers, json={"url": "https://example.com/hook"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["signing_secret"].startswith("whsec_")
    hook_id = body["webhook_id"]
    stored = list(mock_db.hooks.values())[0]
    assert "whsec_" not in json.dumps(mock_db.hooks)  # nothing secret is stored
    assert body["signing_secret"] == __import__("app.queue", fromlist=["webhook_secret"]).webhook_secret(hook_id)

    resp = await client.get("/v1/webhooks", headers=clerk_headers)
    assert resp.status_code == 200
    assert resp.json()["webhooks"][0]["url"] == "https://example.com/hook"
    assert "secret" not in json.dumps(resp.json())

    resp = await client.delete(f"/v1/webhooks/{hook_id}", headers=clerk_headers)
    assert resp.status_code == 200

    g, headers = api_key_headers(mock_db)
    resp = await client.post("/v1/webhooks", headers=headers, json={"url": "https://x.com/"})
    assert resp.status_code == 401


def test_webhook_secret_derivation():
    from app.queue import webhook_secret

    s1, s2 = webhook_secret("hook-a"), webhook_secret("hook-b")
    assert s1.startswith("whsec_") and s1 != s2
    assert webhook_secret("hook-a") == s1  # deterministic, nothing secret stored


def test_webhook_ssrf_guard():
    from app.queue import safe_webhook_url

    assert safe_webhook_url("https://example.com/hook") is True
    for bad in ("http://example.com/h", "https://localhost/h", "https://127.0.0.1/h",
                "https://169.254.169.254/latest/meta-data/", "https://10.1.2.3/h", "ftp://x/"):
        assert safe_webhook_url(bad) is False, bad


def test_webhook_signature():
    import hashlib
    import hmac

    body = '{"event": "job.completed"}'
    secret = "whsec_abc"
    sig = "sha256=" + __import__("hmac").new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert sig.startswith("sha256=") and len(sig) == 71
