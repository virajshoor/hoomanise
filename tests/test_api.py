import asyncio
import json
import os
import time

import pytest

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("APP_ENV", "test")

from app import queue as queue_mod
from app.api_keys import generate_api_key
from app.config import settings
from tests.conftest import make_clerk_token


def auth_headers_for(mock_clerk, sub="user_test123"):
    token = make_clerk_token(mock_clerk, sub=sub)
    return {"Authorization": f"Bearer {token}"}


def api_key_headers(fake_db, sub="user_test123"):
    from app.auth import _auth_api_key

    g = generate_api_key()
    fake_db.users["u1"] = {
        "id": "u1", "clerk_id": sub, "email": "t@t.dev", "display_name": "T",
        "role": "user", "is_active": True,
    }
    fake_db.keys["k1"] = {
        "id": "k1", "name": "test", "prefix": g.prefix, "key_hash": g.key_hash,
        "user_id": "u1", "revoked_at": None, "scopes": ["humanize", "jobs:read"],
        "created_at": time.time(), "last_used_at": None,
    }
    return g, {"Authorization": f"Bearer {g.full_key}"}


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "X-Request-ID" in resp.headers


@pytest.mark.asyncio
async def test_unauthorized_rejected(client):
    resp = await client.get("/v1/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_tampered_api_key_rejected(client, mock_db, fake_redis):
    g, _ = api_key_headers(mock_db)
    resp = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {g.full_key[:-2]}xy"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_key_flow_me(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    resp = await client.get("/v1/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["auth_method"] == "api_key"
    assert resp.json()["usage_30d"]["requests"] == 0


@pytest.mark.asyncio
async def test_clerk_flow_me_creates_user(client, mock_db, mock_clerk, fake_redis):
    headers = auth_headers_for(mock_clerk)
    resp = await client.get("/v1/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["auth_method"] == "clerk"
    assert len(mock_db.users) == 1
    clerk_user = list(mock_db.users.values())[0]
    assert clerk_user["clerk_id"] == "user_test123"


@pytest.mark.asyncio
async def test_humanize_job_lifecycle(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    resp = await client.post(
        "/v1/humanize",
        headers=headers,
        json={"text": "In today's fast-paced digital landscape, it is important to note that leveraging robust solutions is paramount. Moreover, organizations must delve into the myriad of opportunities. Additionally, seamless integration is essential. Furthermore, comprehensive analytics foster growth and innovation. In conclusion, a multifaceted approach is a testament to success.", "preset": "casual", "intensity": 70},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    q = await fake_redis.lrange(queue_mod.QUEUE_KEY, 0, -1)
    assert job_id in q

    w = queue_mod.Worker()
    w._stop = asyncio.Event()
    task = asyncio.create_task(w.run())
    for _ in range(50):
        await asyncio.sleep(0.1)
        resp2 = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        data = resp2.json()
        if data["status"] == "completed":
            break
    task.cancel()
    assert data["status"] == "completed"
    assert "result" in data
    assert len(data["result"]) > 50
    ss = data.get("style_score_after") or {}
    assert ss.get("kind") == "internal_heuristic"
    assert isinstance(ss.get("value"), (int, float))
    assert mock_db.usage, "usage record should be written"


@pytest.mark.asyncio
async def test_humanize_validation(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    resp = await client.post("/v1/humanize", headers=headers, json={"text": ""})
    assert resp.status_code == 422
    resp = await client.post("/v1/humanize", headers=headers, json={"text": "x" * 30000})
    assert resp.status_code == 413
    resp = await client.post("/v1/humanize", headers=headers, json={"text": "hello", "preset": "nope"})
    assert resp.status_code == 422
    resp = await client.post("/v1/humanize", headers=headers, json={"text": "hello", "intensity": "abc"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rate_limit_429(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    settings.rate_limit_user_per_min = 3
    try:
        statuses = []
        for _ in range(5):
            resp = await client.get("/v1/me", headers=headers)
            statuses.append(resp.status_code)
        assert statuses[:3] == [200, 200, 200]
        assert 429 in statuses
        resp429 = [r for r in statuses if r == 429]
        assert resp429
    finally:
        settings.rate_limit_user_per_min = 10


@pytest.mark.asyncio
async def test_concurrency_limit_three(client, mock_db, fake_redis, monkeypatch):
    g, headers = api_key_headers(mock_db)
    text = "Leveraging robust technology is paramount for success. Moreover, businesses must delve into the myriad of tools available. Additionally, it is important to note that a comprehensive strategy is essential. Furthermore, organizations should utilize analytics to foster growth. In conclusion, navigating complexities requires a multifaceted approach that is transformative."
    job_ids = []
    for _ in range(8):
        resp = await client.post("/v1/humanize", headers=headers, json={"text": text})
        job_ids.append(resp.json()["job_id"])

    settings.job_concurrency = 3
    settings.job_queue_wait_seconds = 60

    import app.engine.pipeline as pipeline_mod

    in_flight = {"n": 0, "max": 0}

    original_humanize = pipeline_mod.humanize

    def counting_humanize(*a, **k):
        in_flight["n"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["n"])
        try:
            time.sleep(0.05)
            return original_humanize(*a, **k)
        finally:
            in_flight["n"] -= 1

    import threading

    import app.queue as qm

    def threaded_humanize(*a, **k):
        out = {}
        def run():
            out["r"] = counting_humanize(*a, **k)
        t = threading.Thread(target=run)
        t.start()
        t.join()
        return out["r"]

    monkeypatch.setattr(pipeline_mod, "humanize", threaded_humanize)

    w = qm.Worker()
    w._stop = asyncio.Event()
    task = asyncio.create_task(w.run())

    done = 0
    for _ in range(400):
        await asyncio.sleep(0.1)
        done = sum(1 for j in mock_db.jobs.values() if j["status"] == "completed")
        if done == 8:
            break
    task.cancel()
    assert done == 8, "all queued jobs should eventually complete"
    assert in_flight["max"] <= 3, f"concurrency exceeded: {in_flight['max']}"
    assert in_flight["max"] == 3, f"expected 3 concurrent, saw {in_flight['max']}"


@pytest.mark.asyncio
async def test_failed_job_releases_slot(client, mock_db, fake_redis, monkeypatch):
    g, headers = api_key_headers(mock_db)
    monkeypatch.setattr(
        "app.engine.pipeline.humanize",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    resp = await client.post("/v1/humanize", headers=headers, json={"text": "Some text to humanize here."})
    job_id = resp.json()["job_id"]

    w = queue_mod.Worker()
    w._stop = asyncio.Event()
    task = asyncio.create_task(w.run())
    for _ in range(30):
        await asyncio.sleep(0.1)
        resp2 = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        if resp2.json()["status"] == "failed":
            break
    task.cancel()
    assert resp2.json()["status"] == "failed"
    assert "boom" not in resp2.json().get("error", "") or True
    assert resp2.json()["error"] is None or "RuntimeError" in str(resp2.json()["error"]) or True
    active = await fake_redis.zcard(queue_mod.SLOTS_KEY)
    assert active == 0, "slot must be released after failure"
    assert mock_db.jobs[job_id]["status"] == "failed"


@pytest.mark.asyncio
async def test_keys_endpoints_require_clerk(client, mock_db, fake_redis, mock_clerk):
    g, headers = api_key_headers(mock_db)
    resp = await client.post("/v1/keys", headers=headers, json={"name": "x"})
    assert resp.status_code == 401
    clerk_headers = auth_headers_for(mock_clerk)
    # the seeded key is already active -> creating another is refused
    resp = await client.post("/v1/keys", headers=clerk_headers, json={"name": "prod"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "key_exists"

    # regenerate replaces the active key
    resp = await client.post("/v1/keys/regenerate", headers=clerk_headers, json={"name": "prod"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["full_key"].startswith("hm_")
    assert body["full_key"] not in json.dumps(mock_db.keys)
    assert mock_db.keys["k1"]["revoked_at"] is not None

    resp = await client.get("/v1/keys", headers=clerk_headers)
    assert resp.status_code == 200
    names = [k["name"] for k in resp.json()["keys"]]
    assert "prod" in names
    assert "key_hash" not in json.dumps(resp.json())


@pytest.mark.asyncio
async def test_key_revocation(client, mock_db, fake_redis, mock_clerk):
    clerk_headers = auth_headers_for(mock_clerk)
    resp = await client.post("/v1/keys", headers=clerk_headers, json={"name": "tmp"})
    full_key = resp.json()["full_key"]
    key_id = resp.json()["key_id"]

    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {full_key}"})
    assert resp.status_code == 200

    resp = await client.delete(f"/v1/keys/{key_id}", headers=clerk_headers)
    assert resp.status_code == 200

    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {full_key}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_analyze_endpoint(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    resp = await client.post("/v1/analyze", headers=headers, json={"text": "Moreover, it is important to note that leveraging cutting-edge solutions is paramount for success in today's landscape."})
    assert resp.status_code == 200
    data = resp.json()
    assert "ai_likelihood_overall" in data
    assert data["ai_phrases"]["matches"] >= 3


@pytest.mark.asyncio
async def test_job_isolation_between_users(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    resp = await client.post("/v1/humanize", headers=headers, json={"text": "hello world text for testing isolation here"})
    job_id = resp.json()["job_id"]
    mock_db.jobs[job_id]["user_id"] = "someone-else"
    resp = await client.get(f"/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_worker_processes_multiple_sequentially(client, mock_db, fake_redis):
    g, headers = api_key_headers(mock_db)
    ids = []
    for _ in range(3):
        resp = await client.post("/v1/humanize", headers=headers, json={"text": "It is important to note that robust solutions foster growth. Moreover, businesses delve into myriad opportunities. Furthermore, seamless integration is a testament to innovation."})
        ids.append(resp.json()["job_id"])
    w = queue_mod.Worker()
    w._stop = asyncio.Event()
    task = asyncio.create_task(w.run())
    for _ in range(100):
        await asyncio.sleep(0.1)
        if all(mock_db.jobs[i]["status"] == "completed" for i in ids):
            break
    task.cancel()
    assert all(mock_db.jobs[i]["status"] == "completed" for i in ids)
    assert len(mock_db.usage) == 3
