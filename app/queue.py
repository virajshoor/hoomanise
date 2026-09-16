import asyncio
import hashlib
import hmac as _hmac
import json as _json
import logging
import time
import urllib.request
import urllib.error
import uuid

from . import db
from .config import settings as cfg
from .redis_client import get_redis

log = logging.getLogger("hoomanise.queue")

QUEUE_KEY = "hoomanise:queue"
PROCESSING_KEY = "hoomanise:processing"
SLOTS_KEY = "hoomanise:slots"
WEBHOOK_QUEUE_KEY = "hoomanise:webhooks"
CACHE_PREFIX = "hoomanise:cache:"

JOB_KEY = "hoomanise:job:{job_id}"


def job_key(job_id: str) -> str:
    return JOB_KEY.format(job_id=job_id)


def json_dumps(obj):
    return _json.dumps(obj, default=str)


def _webhook_master_key() -> bytes:
    key = cfg.webhook_signing_key or cfg.api_key_pepper
    if not key:
        raise RuntimeError("WEBHOOK_SIGNING_KEY (or API_KEY_PEPPER) must be set to sign webhooks")
    return key.encode()


def webhook_secret(webhook_id: str) -> str:
    return "whsec_" + _hmac.new(_webhook_master_key(), f"webhook:{webhook_id}".encode(), hashlib.sha256).hexdigest()


_PRIVATE_HOSTS = ("localhost", "127.", "10.", "192.168.", "169.254.", "0.", "[::1]", "::1")


def safe_webhook_url(url: str) -> bool:
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = parsed.hostname or ""
    if not host or any(host.startswith(p) or host == p.strip("[]") for p in _PRIVATE_HOSTS):
        return False
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _webhook_post(url: str, body: str, secret: str, timeout: int) -> int | None:
    if not safe_webhook_url(url):
        return None
    sig = _hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url,
        method="POST",
        data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "X-Hoomanise-Signature": f"sha256={sig}",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status
    except Exception:
        return None


async def enqueue(job_id: str) -> None:
    r = get_redis()
    await r.hset(
        job_key(job_id),
        mapping={"state": "queued", "enqueued_ts": str(time.time())},
    )
    await r.expire(job_key(job_id), cfg.job_result_ttl_hours * 3600)
    await r.lpush(QUEUE_KEY, job_id)
    log.info("job enqueued", extra={"job_id": job_id})


async def get_job_state(job_id: str) -> dict | None:
    r = get_redis()
    data = await r.hgetall(job_key(job_id))
    return data or None


async def recover_orphans() -> int:
    r = get_redis()
    orphans = await r.lrange(PROCESSING_KEY, 0, -1)
    if orphans:
        for job_id in orphans:
            await r.rpush(QUEUE_KEY, job_id)
            log.info("requeued orphan job", extra={"job_id": job_id})
        await r.delete(PROCESSING_KEY)
    return len(orphans)


class SlotManager:
    def __init__(self):
        self.slot_id = str(uuid.uuid4())

    async def acquire(self, deadline_ts: float, job_id: str | None = None) -> bool:
        r = get_redis()
        while time.time() < deadline_ts:
            pipe = r.pipeline()
            pipe.zremrangebyscore(SLOTS_KEY, "-inf", time.time() - cfg.job_slot_ttl_seconds)
            pipe.zcard(SLOTS_KEY)
            _, count = await pipe.execute()
            if count < cfg.job_concurrency:
                await r.zadd(SLOTS_KEY, {self.slot_id: time.time()})
                count_after = await r.zcard(SLOTS_KEY)
                if count_after <= cfg.job_concurrency:
                    log.info(
                        "concurrency slot acquired",
                        extra={"job_id": job_id, "active": count_after},
                    )
                    return True
                await r.zrem(SLOTS_KEY, self.slot_id)
            await asyncio.sleep(0.5)
        return False

    async def release(self, job_id: str | None = None) -> None:
        r = get_redis()
        await r.zrem(SLOTS_KEY, self.slot_id)
        if job_id:
            count = await r.zcard(SLOTS_KEY)
            log.info("concurrency slot released", extra={"job_id": job_id, "active": count})


class Worker:
    def __init__(self):
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self.run(), name="humanise-worker")

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def run(self):
        from .engine import analysis, pipeline

        r = get_redis()
        await recover_orphans()
        log.info("worker started", extra={"concurrency": cfg.job_concurrency})
        inflight: set[asyncio.Task] = set()

        def _done(task: asyncio.Task):
            inflight.discard(task)

        while not self._stop.is_set():
            if len(inflight) >= cfg.job_concurrency:
                await asyncio.sleep(0.2)
                continue
            try:
                item = await r.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("queue pop failed", extra={"error": str(e)})
                await asyncio.sleep(1)
                continue
            try:
                await self._drain_webhooks(r)
            except Exception as e:
                log.warning("webhook drain failed", extra={"error": str(e)})
            if time.time() - getattr(self, "_last_purge", 0) > 600:
                self._last_purge = time.time()
                try:
                    await asyncio.to_thread(self._purge_old_content)
                except Exception as e:
                    log.warning("retention purge failed", extra={"error": str(e)})
            if not item:
                continue
            job_id = item
            task = asyncio.create_task(self._safe_process(job_id, analysis, pipeline, r))
            inflight.add(task)
            task.add_done_callback(_done)

        if inflight:
            log.info("draining in-flight jobs", extra={"count": len(inflight)})
            await asyncio.gather(*inflight, return_exceptions=True)
        log.info("worker stopped")

    async def _safe_process(self, job_id: str, analysis, pipeline, r):
        try:
            await self.process(job_id, analysis, pipeline, r)
        except Exception as e:
            log.error(
                "job processing error",
                extra={"job_id": job_id, "error": f"{type(e).__name__}: {e}"},
            )
            await self._fail(r, job_id, "internal error")
        finally:
            try:
                await r.lrem(PROCESSING_KEY, 1, job_id)
            except Exception:
                pass

    async def process(self, job_id: str, analysis, pipeline, r):
        row = await asyncio.to_thread(
            db.query, "SELECT * FROM jobs WHERE id = %s", (job_id,), True
        )
        if row is None:
            log.warning("job not found in db, dropping", extra={"job_id": job_id})
            return
        if row["status"] in ("completed", "failed"):
            return

        slots = SlotManager()
        deadline = time.time() + cfg.job_queue_wait_seconds
        got = await slots.acquire(deadline, job_id)
        if not got:
            await self._fail(r, job_id, "queue timeout: waited too long for a slot")
            return

        started = time.time()
        try:
            await asyncio.to_thread(
                db.execute,
                "UPDATE jobs SET status='processing', started_at=now() WHERE id=%s AND status='queued'",
                (job_id,),
            )
            await r.hset(
                job_key(job_id),
                mapping={"state": "processing", "started_ts": str(started)},
            )
            await r.expire(job_key(job_id), cfg.job_result_ttl_hours * 3600)
            log.info("job started", extra={"job_id": job_id})

            seed = int(uuid.UUID(job_id).int % (2**32))
            humanized, meta = await asyncio.to_thread(
                pipeline.humanize,
                row["input_text"] or "",
                row["preset"],
                row["intensity"],
                seed,
                bool(row.get("preserve_formatting", True)),
                cfg.human_score_threshold,
            )
            stats = meta
            score_before = await asyncio.to_thread(analysis.overall_score, row["input_text"] or "")
            score_after = await asyncio.to_thread(analysis.overall_score, humanized)

            await asyncio.to_thread(
                db.execute,
                "UPDATE jobs SET status='completed', result_text=%s, metadata=%s, completed_at=now() WHERE id=%s",
                (humanized, json_dumps(meta), job_id),
            )
            from .credits import settle as _settle

            await asyncio.to_thread(
                _settle,
                job_id,
                status="completed",
                input_words=stats.get("before_words") if isinstance(stats, dict) else None,
                output_words=stats.get("after_words") if isinstance(stats, dict) else None,
                score_before=score_before,
                score_after=score_after,
            )
            await r.hset(
                job_key(job_id),
                mapping={
                    "state": "completed",
                    "finished_ts": str(time.time()),
                    "score_after": str(score_after),
                },
            )
            await r.expire(job_key(job_id), cfg.job_result_ttl_hours * 3600)
            import json as _json

            await r.hset(job_key(job_id), "metadata", _json.dumps(meta, default=str))
            if cfg.response_cache_enabled and row.get("input_sha256"):
                cache_key = (
                    f"{CACHE_PREFIX}{row['user_id']}:{row['input_sha256']}:{row['preset']}:"
                    f"{row['intensity']}:{bool(row.get('preserve_formatting', True))}"
                )
                await r.hset(
                    cache_key,
                    mapping={
                        "result": humanized,
                        "style_score": str(score_after),
                        "metadata": json_dumps(meta),
                    },
                )
                await r.expire(cache_key, cfg.response_cache_ttl_hours * 3600)
            log.info(
                "job completed",
                extra={"job_id": job_id, "duration_s": round(time.time() - started, 2)},
            )
        except Exception as e:
            await self._fail(r, job_id, f"{type(e).__name__}: {e}")
            raise
        finally:
            await slots.release(job_id)
        try:
            await self._queue_webhook(r, job_id, row, "job.completed")
        except Exception as e:
            log.warning("webhook enqueue failed", extra={"job_id": job_id, "error": str(e)})

    def _purge_old_content(self):
        if cfg.job_retention_days <= 0:
            return
        row = db.query(
            "WITH purged AS ("
            "  UPDATE jobs SET input_text = NULL, result_text = NULL"
            "  WHERE completed_at IS NOT NULL"
            "    AND completed_at < now() - make_interval(days => %s)"
            "    AND (input_text IS NOT NULL OR result_text IS NOT NULL)"
            "  RETURNING 1)"
            " SELECT COUNT(*) AS n FROM purged",
            (cfg.job_retention_days,),
            True,
        )
        if row and row.get("n"):
            log.info("retention purge", extra={"jobs": row["n"]})

    async def _queue_webhook(self, r, job_id: str, row, event: str):
        endpoint = await asyncio.to_thread(
            db.query,
            "SELECT id, url FROM webhook_endpoints WHERE user_id = %s AND disabled_at IS NULL LIMIT 1",
            (row["user_id"],),
            True,
        )
        if not endpoint:
            return
        payload = {
            "event": event,
            "job_id": job_id,
            "created_at": time.time(),
        }
        await r.rpush(
            WEBHOOK_QUEUE_KEY,
            json_dumps({
                "webhook_id": str(endpoint["id"]),
                "payload": payload,
                "attempts": 0,
            }),
        )
        log.info("webhook delivery queued", extra={"job_id": job_id, "webhook_id": str(endpoint["id"])})

    async def _drain_webhooks(self, r):
        while True:
            raw = await r.lpop(WEBHOOK_QUEUE_KEY)
            if not raw:
                return
            try:
                item = _json.loads(raw)
            except (ValueError, TypeError):
                continue
            attempts = int(item.get("attempts", 0))
            hook_row = await asyncio.to_thread(
                db.query,
                "SELECT id, url FROM webhook_endpoints WHERE id = %s AND disabled_at IS NULL",
                (item["webhook_id"],),
                True,
            )
            if hook_row is None:
                continue
            body = json_dumps(item["payload"])
            secret = webhook_secret(str(hook_row["id"]))
            status = await asyncio.to_thread(
                _webhook_post, item["url"], body, secret, cfg.webhook_timeout_seconds
            )
            await asyncio.to_thread(
                db.execute,
                "UPDATE webhook_endpoints SET last_delivery_at = now(), last_status = %s WHERE id = %s",
                (status, hook_row["id"]),
            )
            if status != 200 and attempts < cfg.webhook_delivery_retries:
                item["attempts"] = attempts + 1
                await r.rpush(WEBHOOK_QUEUE_KEY, json_dumps(item))
                log.warning(
                    "webhook delivery failed, requeued",
                    extra={"webhook_id": hook_row["id"], "attempts": attempts},
                )
                return
            log.info("webhook delivered", extra={"webhook_id": hook_row["id"], "status": status})

    async def _fail(self, r, job_id: str, error: str):
        from .credits import refund as _refund

        safe = error if error.startswith("queue timeout") else "internal_error"
        error_ref = uuid.uuid4().hex[:12]
        try:
            await asyncio.to_thread(
                db.execute,
                "UPDATE jobs SET status='failed', error=%s, completed_at=now() WHERE id=%s",
                (f"{safe} (ref {error_ref})" if safe == "internal_error" else safe, job_id),
            )
            log.error("job failed", extra={"job_id": job_id, "error_ref": error_ref, "detail": error[:500]})
            await asyncio.to_thread(_refund, job_id)
            await r.hset(job_key(job_id), mapping={"state": "failed", "error": safe, "error_ref": error_ref})
            await r.expire(job_key(job_id), cfg.job_result_ttl_hours * 3600)
        except Exception as e:
            log.error("failed to mark job failed", extra={"job_id": job_id, "error": str(e)})


worker: Worker | None = None


def start_worker() -> Worker:
    global worker
    if worker is None:
        worker = Worker()
        worker.start()
    return worker


async def stop_worker():
    global worker
    if worker is not None:
        await worker.stop()
        worker = None
