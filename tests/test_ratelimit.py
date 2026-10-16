"""1.33 — Redis-backed rate limiting (S-10, docs/security.md).

Unit tests for `app.core.ratelimit`: the shared INCR+EXPIRE helper that
replaced the five per-process `dict` limiters (badge/claim/endpoint_verification/
tag/auth). A fake Redis client stands in for the real one — these tests are
about the helper's contract (shared budget across callers, window expiry,
documented failure mode), not about Lua syntax, which only a real Redis
exercises (covered by the probe's live `rate_limit[verify-endpoint]` check).
"""

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from app.core import ratelimit


class FakeRedis:
    """In-memory stand-in with a virtual clock (`now`), advanced manually so
    window-expiry tests don't need to sleep. One instance = one shared Redis;
    reusing it across multiple `increment`/`enforce` calls models multiple
    uvicorn workers hitting the same backing store."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[int, float]] = {}
        self.now = 0.0

    async def eval(self, script: str, numkeys: int, key: str, window_seconds: int) -> int:
        count, expire_at = self.store.get(key, (0, None))
        if expire_at is not None and self.now >= expire_at:
            count = 0
        count += 1
        if count == 1:
            expire_at = self.now + float(window_seconds)
        self.store[key] = (count, expire_at)
        return count

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.store.pop(k, None)


class BrokenRedis:
    async def eval(self, *a, **k):  # noqa: ANN002, ANN003
        raise RedisError("connection refused")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()

    async def _get_redis():
        return redis

    monkeypatch.setattr(ratelimit, "get_redis", _get_redis)
    return redis


@pytest.mark.asyncio
async def test_shared_budget_across_two_workers(fake_redis: FakeRedis) -> None:
    """Two limiter calls against the same key (= two uvicorn workers, one
    Redis) share one budget — the exact bug S-10 closes. Under the old
    per-process dicts, each "worker" would have its own counter and the
    5th+6th calls would both pass."""
    key = "ratelimit:test:1.2.3.4"
    for _ in range(5):  # limit=5, all within budget
        assert await ratelimit.increment(key, limit=5, window_seconds=60, fail_open=True)
    # 6th call, from either "worker", must be the one that trips — not a
    # fresh budget for a second caller.
    assert not await ratelimit.increment(key, limit=5, window_seconds=60, fail_open=True)


@pytest.mark.asyncio
async def test_window_expires(fake_redis: FakeRedis) -> None:
    key = "ratelimit:test:expiry"
    for _ in range(3):
        assert await ratelimit.increment(key, limit=3, window_seconds=60, fail_open=True)
    assert not await ratelimit.increment(key, limit=3, window_seconds=60, fail_open=True)

    fake_redis.now += 60.0  # window elapses
    assert await ratelimit.increment(key, limit=3, window_seconds=60, fail_open=True)


@pytest.mark.asyncio
async def test_enforce_raises_429_over_limit(fake_redis: FakeRedis) -> None:
    key = "ratelimit:test:enforce"
    for _ in range(2):
        await ratelimit.enforce(key, limit=2, window_seconds=60, fail_open=True)
    with pytest.raises(HTTPException) as exc:
        await ratelimit.enforce(key, limit=2, window_seconds=60, fail_open=True)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_redis_outage_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get_redis():
        return BrokenRedis()

    monkeypatch.setattr(ratelimit, "get_redis", _get_redis)
    # No exception, request proceeds unlimited — never a bare 500.
    assert await ratelimit.increment("k", limit=1, window_seconds=60, fail_open=True)


@pytest.mark.asyncio
async def test_redis_outage_fails_closed_as_503_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get_redis():
        return BrokenRedis()

    monkeypatch.setattr(ratelimit, "get_redis", _get_redis)
    with pytest.raises(HTTPException) as exc:
        await ratelimit.increment("k", limit=1, window_seconds=60, fail_open=False)
    assert exc.value.status_code == 503
