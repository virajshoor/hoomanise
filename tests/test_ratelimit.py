import asyncio
import time

import pytest

import app.redis_client as redis_client
from app import ratelimit
from app.config import settings


@pytest.mark.asyncio
async def test_rate_limit_user_minute(fake_redis):
    allowed_seen = []
    result = None
    for i in range(12):
        result = await ratelimit.check_limits(
            user_id="u1", ip="1.2.3.4",
            user_per_min=10, user_per_day=1000, ip_per_min=100, global_per_min=1000,
        )
        allowed_seen.append(result["allowed"])
    assert allowed_seen[:10] == [True] * 10
    assert allowed_seen[10] is False
    assert result["scope"] == "user_minute"
    assert result["retry_after"] == 60


@pytest.mark.asyncio
async def test_rate_limit_ip(fake_redis):
    for _ in range(30):
        await ratelimit.check_limits(None, "9.9.9.9", 1000, 1000, 30, 1000)
    result = await ratelimit.check_limits(None, "9.9.9.9", 1000, 1000, 30, 1000)
    assert result["allowed"] is False
    assert result["scope"] == "ip_minute"


@pytest.mark.asyncio
async def test_rate_limit_global(fake_redis):
    last = None
    for i in range(241):
        last = await ratelimit.check_limits(f"u{i}", "1.1.1.1", 1000, 1000, 1000, 240)
    assert last["allowed"] is False
    assert last["scope"] == "global"


@pytest.mark.asyncio
async def test_rate_limit_user_day(fake_redis):
    for _ in range(200):
        await ratelimit.check_limits("uD", "8.8.8.8", 1000, 200, 1000, 1000)
    result = await ratelimit.check_limits("uD", "8.8.8.8", 1000, 200, 1000, 1000)
    assert result["allowed"] is False
    assert result["scope"] == "user_day"


@pytest.mark.asyncio
async def test_rate_limit_isolated_users(fake_redis):
    for _ in range(10):
        await ratelimit.check_limits("uA", "1.1.1.1", 10, 1000, 1000, 1000)
    result = await ratelimit.check_limits("uB", "1.1.1.1", 10, 1000, 1000, 1000)
    assert result["allowed"] is True


@pytest.mark.asyncio
async def test_rate_limit_headers(fake_redis):
    result = await ratelimit.check_limits("uH", "1.1.1.1", 10, 1000, 1000, 1000)
    assert result["headers"]["X-RateLimit-Limit"] == "10"
    assert result["headers"]["X-RateLimit-Remaining"] == "9"


@pytest.mark.asyncio
async def test_queue_enqueue_and_slots(fake_redis):
    from app.queue import SlotManager, enqueue, get_job_state

    await enqueue("job-1")
    state = await get_job_state("job-1")
    assert state["state"] == "queued"

    mgr = SlotManager()
    assert await mgr.acquire(time.time() + 1, "job-1")
    assert await fake_redis.zcard("hoomanise:slots") == 1
    await mgr.release("job-1")
    assert await fake_redis.zcard("hoomanise:slots") == 0


@pytest.mark.asyncio
async def test_slot_limit_enforced(fake_redis):
    from app.queue import SlotManager

    assert settings.job_concurrency == 3
    acquired = []
    for i in range(5):
        mgr = SlotManager()
        got = await mgr.acquire(time.time() + 0.5, f"j{i}")
        if got:
            acquired.append(mgr)
    assert len(acquired) == 3


@pytest.mark.asyncio
async def test_stale_slots_reaped(fake_redis, monkeypatch):
    from app.queue import SlotManager

    settings.job_concurrency = 2
    settings.job_slot_ttl_seconds = 1
    try:
        m1 = SlotManager()
        assert await m1.acquire(time.time() + 1, "a")
        m2 = SlotManager()
        assert await m2.acquire(time.time() + 1, "b")
        m3 = SlotManager()
        assert not await m3.acquire(time.time() + 0.6, "c")
        await asyncio.sleep(1.2)
        m4 = SlotManager()
        assert await m4.acquire(time.time() + 1, "d")
    finally:
        settings.job_concurrency = 3
        settings.job_slot_ttl_seconds = 120


@pytest.mark.asyncio
async def test_orphan_recovery(fake_redis):
    from app.queue import PROCESSING_KEY, QUEUE_KEY, recover_orphans

    await fake_redis.lpush(PROCESSING_KEY, "job-stuck")
    n = await recover_orphans()
    assert n == 1
    assert await fake_redis.lrange(QUEUE_KEY, 0, -1) == ["job-stuck"]
