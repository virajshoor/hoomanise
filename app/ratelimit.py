import time

from .redis_client import get_redis

USER_MIN = "rl:u:min:{user_id}"
USER_DAY = "rl:u:day:{user_id}"
IP_MIN = "rl:ip:min:{ip}"
GLOBAL_MIN = "rl:global:min"

DAY_TTL = 90000


async def _hit(key: str, limit: int, ttl: int) -> tuple[bool, int, int]:
    r = get_redis()
    bucket = int(time.time() // 60) if ":min" in key else int(time.time() // 86400)
    namespaced = f"{key}:{bucket}"
    n = await r.incr(namespaced)
    if n == 1:
        await r.expire(namespaced, ttl if ":day" in key else 120)
    allowed = n <= limit
    remaining = max(0, limit - n)
    return allowed, remaining, limit


async def check_limits(
    user_id: str | None,
    ip: str,
    user_per_min: int,
    user_per_day: int,
    ip_per_min: int,
    global_per_min: int,
    day_ttl: int = DAY_TTL,
) -> dict:
    checks = []
    g_allowed, g_rem, g_lim = await _hit(GLOBAL_MIN, global_per_min, 120)
    checks.append(("global", g_allowed, g_rem, g_lim, 60))
    if user_id:
        u_allowed, u_rem, u_lim = await _hit(
            USER_MIN.format(user_id=user_id), user_per_min, 120
        )
        checks.append(("user_minute", u_allowed, u_rem, u_lim, 60))
        d_allowed, d_rem, d_lim = await _hit(
            USER_DAY.format(user_id=user_id), user_per_day, day_ttl
        )
        checks.append(("user_day", d_allowed, d_rem, d_lim, 86400))
    i_allowed, i_rem, i_lim = await _hit(IP_MIN.format(ip=ip), ip_per_min, 120)
    checks.append(("ip_minute", i_allowed, i_rem, i_lim, 60))

    ok = all(c[1] for c in checks)
    violated = next((c for c in checks if not c[1]), None)
    primary = next((c for c in checks if c[0].startswith("user")), None) or checks[-1]
    result = {
        "allowed": ok,
        "scope": violated[0] if violated else None,
        "limit": violated[3] if violated else primary[3],
        "remaining": violated[2] if violated else primary[2],
        "retry_after": violated[4] if violated else primary[4],
        "headers": {
            "X-RateLimit-Limit": str(primary[3]),
            "X-RateLimit-Remaining": str(primary[2]),
        },
    }
    return result
