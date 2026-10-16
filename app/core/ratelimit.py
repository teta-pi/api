"""Shared Redis-backed rate limiting (S-10, docs/security.md).

Every limiter in the API used to be a per-process `dict` of timestamps —
correct only under `uvicorn --workers 1` (docs/architecture.md). With N
workers each holds its own dict, so the real ceiling is `configured limit x N`
and a worker restart resets a client's count to zero. This module replaces
all of them with one counter, shared over Redis, via a single atomic
INCR+EXPIRE (Lua) call: two workers racing on the same key can never both
land inside a window that's already full.

Fixed-window, not sliding: a key counts hits since it was first touched and
expires `window_seconds` later, so a client can in principle burst up to
`2x limit` across a window boundary. The original per-process limiters were
exact sliding windows, but that precision isn't worth the extra Redis round
trips (ZADD/ZREMRANGEBYSCORE/ZCARD) here — fixed-window is the same trade-off
`/auth/email-code`'s cooldown already made before this module existed.

Failure mode is per-call (`fail_open`), decided per endpoint group and
documented in docs/api.md's "Rate limiting" section:
  * `fail_open=True` — Redis unreachable lets the request through unlimited.
    Right for public/cosmetic endpoints (badge, tag-ping, claim, verify-endpoint)
    where a cache outage must never turn into a user-facing outage.
  * `fail_open=False` — Redis unreachable raises 503. Right for the brute-force
    counters on `/auth/*` and the business-email-control codes: silently
    going unlimited there would turn a Redis blip into a credential-guessing
    window, which is the one thing these limiters exist to prevent.
Either way, a Redis outage never bubbles up as an unhandled 500.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# INCR then, only on the first hit of a new window, EXPIRE — both in one
# round trip so a crash between a separate INCR and EXPIRE call can never
# leave a key with no TTL (which would count forever instead of resetting).
_INCR_EXPIRE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def client_ip(request: Request) -> str:
    default = request.client.host if request.client else "unknown"
    ip = request.headers.get("x-forwarded-for", default)
    return ip.split(",")[0].strip()


async def increment(key: str, *, limit: int, window_seconds: int, fail_open: bool) -> bool:
    """Touch `key`'s counter and report whether the caller is still within
    `limit` hits per `window_seconds`. Does not raise on a normal over-limit
    result — callers that need extra side effects on the "blocked" branch
    (e.g. auth.py deleting a pending code) check the return value themselves;
    callers that don't should use `enforce`/`enforce_ip` instead.
    """
    redis = await get_redis()
    try:
        count = await redis.eval(_INCR_EXPIRE_LUA, 1, key, window_seconds)
    except RedisError:
        logger.warning("ratelimit: redis unreachable for key=%s fail_open=%s", key, fail_open)
        if fail_open:
            return True
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiter temporarily unavailable, try again shortly",
        ) from None
    return count <= limit


async def enforce(key: str, *, limit: int, window_seconds: int, fail_open: bool) -> None:
    """Like `increment`, but raises 429 itself when the limit is exceeded."""
    allowed = await increment(key, limit=limit, window_seconds=window_seconds, fail_open=fail_open)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
        )


async def enforce_ip(
    request: Request, *, bucket: str, limit: int, window_seconds: int, fail_open: bool
) -> None:
    """`enforce` keyed by the caller's IP, namespaced under `bucket` (e.g.
    "claim", "badge") — the drop-in replacement for the old per-route
    `_rate_limit(request)` in-memory helpers."""
    key = f"ratelimit:{bucket}:{client_ip(request)}"
    await enforce(key, limit=limit, window_seconds=window_seconds, fail_open=fail_open)
