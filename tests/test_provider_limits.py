import asyncio

import pytest

from cognitrix.providers.limits import (
    ConcurrencyLimiter,
    LimitBackendUnavailable,
    RedisLeaseBackend,
)


@pytest.mark.asyncio
async def test_provider_and_actor_limits_are_independent():
    limiter = ConcurrencyLimiter(provider_limit=2, actor_limit=1)
    first_entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with limiter.slot("openai", "m", "jwt:a"):
            first_entered.set()
            await release.wait()

    holder = asyncio.create_task(first())
    await first_entered.wait()

    # Same provider still has capacity, but the same actor does not.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            limiter.slot("openai", "m", "jwt:a").__aenter__(),
            timeout=0.02,
        )

    # A different actor can use the second provider/model slot.
    async with asyncio.timeout(0.2):
        async with limiter.slot("openai", "m", "jwt:b"):
            pass

    release.set()
    await holder


@pytest.mark.asyncio
async def test_actor_waiter_does_not_reserve_provider_capacity():
    limiter = ConcurrencyLimiter(provider_limit=2, actor_limit=1)
    holder_entered = asyncio.Event()
    waiter_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def hold_actor():
        async with limiter.slot("openai", "m", "jwt:a"):
            holder_entered.set()
            await release_holder.wait()

    async def wait_for_same_actor():
        waiter_started.set()
        async with limiter.slot("openai", "m", "jwt:a"):
            pass

    holder = asyncio.create_task(hold_actor())
    waiter = None
    try:
        await holder_entered.wait()
        waiter = asyncio.create_task(wait_for_same_actor())
        await waiter_started.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        async with asyncio.timeout(0.2):
            async with limiter.slot("openai", "m", "jwt:b"):
                pass
    finally:
        if waiter is not None:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        release_holder.set()
        await holder


class RecordingBackend:
    def __init__(self, fail=False):
        self.fail = fail
        self.acquired = []
        self.renewed = []
        self.released = []
        self._counter = 0

    async def acquire(self, key, limit, ttl_seconds):
        if self.fail:
            raise OSError("redis unavailable")
        self._counter += 1
        token = f"token-{self._counter}"
        self.acquired.append((key, limit, ttl_seconds, token))
        return token

    async def renew(self, key, token, ttl_seconds):
        self.renewed.append((key, token, ttl_seconds))
        return True

    async def release(self, key, token):
        self.released.append((key, token))


@pytest.mark.asyncio
async def test_streaming_slot_renews_and_releases_both_leases():
    backend = RecordingBackend()
    limiter = ConcurrencyLimiter(
        provider_limit=2,
        actor_limit=2,
        backend=backend,
        environment="production",
        lease_ttl_seconds=0.05,
        renew_interval_seconds=0.01,
    )
    async with limiter.slot("openai", "m", "jwt:a"):
        await asyncio.sleep(0.035)

    assert len(backend.acquired) == 2
    assert len(backend.renewed) >= 2
    assert len(backend.released) == 2


@pytest.mark.asyncio
async def test_backend_failure_fails_closed_in_production():
    limiter = ConcurrencyLimiter(backend=RecordingBackend(fail=True), environment="production")
    with pytest.raises(LimitBackendUnavailable):
        async with limiter.slot("openai", "m", "jwt:a"):
            pass


@pytest.mark.asyncio
async def test_backend_failure_falls_back_locally_only_in_development():
    limiter = ConcurrencyLimiter(backend=RecordingBackend(fail=True), environment="development")
    async with limiter.slot("openai", "m", "jwt:a"):
        pass


@pytest.mark.asyncio
async def test_production_without_distributed_backend_fails_closed():
    limiter = ConcurrencyLimiter(environment="production")

    with pytest.raises(LimitBackendUnavailable, match="required in production"):
        async with limiter.slot("openai", "m", "jwt:a"):
            pass


class ScriptRedis:
    def __init__(self):
        self.calls = []
        self.results = [1, 1, 1]

    async def eval(self, *args):
        self.calls.append(args)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_redis_backend_acquires_renews_and_releases_namespaced_lease():
    client = ScriptRedis()
    backend = RedisLeaseBackend(client, namespace="test:limits")

    token = await backend.acquire("provider:openai:m", 2, 10)
    assert token
    acquire_call = client.calls[0]
    assert acquire_call[2] == "test:limits:provider:openai:m"
    assert "redis.call('TIME')" in acquire_call[0]
    assert len(acquire_call) == 6
    assert acquire_call[3] == 2
    assert acquire_call[5] == 10_000
    assert await backend.renew("provider:openai:m", token, 10) is True
    renew_call = client.calls[1]
    assert "redis.call('TIME')" in renew_call[0]
    assert len(renew_call) == 5
    assert renew_call[4] == 10_000
    await backend.release("provider:openai:m", token)
    assert client.calls[-1][2] == "test:limits:provider:openai:m"


class SlowRedis:
    async def eval(self, *_args):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_redis_eval_is_bounded_by_operation_timeout():
    backend = RedisLeaseBackend(SlowRedis(), operation_timeout_seconds=0.01)

    with pytest.raises(LimitBackendUnavailable, match="timed out"):
        await asyncio.wait_for(
            backend.acquire("provider:openai:m", 2, 10),
            timeout=0.2,
        )


@pytest.mark.asyncio
async def test_lost_production_renewal_interrupts_owner_with_control_error():
    backend = RecordingBackend()

    async def lose_lease(*_args):
        return False

    backend.renew = lose_lease
    limiter = ConcurrencyLimiter(
        backend=backend,
        environment="production",
        lease_ttl_seconds=0.05,
        renew_interval_seconds=0.01,
    )

    with pytest.raises(LimitBackendUnavailable, match="lease lost"):
        async with limiter.slot("openai", "m", "jwt:a"):
            await asyncio.sleep(1)


def test_openai_sdk_hidden_retries_are_disabled(monkeypatch):
    import cognitrix.providers.base as provider

    captured = []
    provider._client_cache.clear()
    monkeypatch.setattr(
        provider,
        "AsyncOpenAI",
        lambda **kwargs: captured.append(kwargs) or object(),
    )

    provider._get_or_create_client("https://provider.test/v1", "secret")

    assert captured[0]["max_retries"] == 0
