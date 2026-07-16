"""Provider/model and actor concurrency limits with renewable leases."""

import asyncio
import time
import uuid
from collections import defaultdict
from contextlib import AbstractAsyncContextManager
from typing import Any

from cognitrix.errors import ExecutionControlError


class LimitBackendUnavailable(ExecutionControlError):
    """Distributed concurrency state is unavailable in a fail-closed runtime."""


class LimitExceeded(ExecutionControlError):
    """A distributed backend declined a concurrency lease."""


class RedisLeaseBackend:
    """Atomic expiring semaphore leases backed by Redis sorted sets."""

    _ACQUIRE = """
local key, limit, token, ttl = KEYS[1], tonumber(ARGV[1]), ARGV[2], tonumber(ARGV[3])
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local expires = now + ttl
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) >= limit then return 0 end
redis.call('ZADD', key, expires, token)
redis.call('PEXPIRE', key, ttl)
return 1
"""
    _RENEW = """
local key, token, ttl = KEYS[1], ARGV[1], tonumber(ARGV[2])
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local expires = now + ttl
if not redis.call('ZSCORE', key, token) then return 0 end
redis.call('ZADD', key, expires, token)
redis.call('PEXPIRE', key, ttl)
return 1
"""
    _RELEASE = "return redis.call('ZREM', KEYS[1], ARGV[1])"

    def __init__(
        self,
        client: Any,
        *,
        namespace: str = "cognitrix:limits",
        operation_timeout_seconds: float = 2.0,
    ):
        self.client = client
        self.namespace = namespace.rstrip(":")
        self.operation_timeout_seconds = max(
            0.001,
            float(operation_timeout_seconds),
        )

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        namespace: str = "cognitrix:limits",
        operation_timeout_seconds: float = 2.0,
    ):
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - deployment packaging guard
            raise LimitBackendUnavailable("redis concurrency backend is not installed") from exc
        timeout = max(0.001, float(operation_timeout_seconds))
        return cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
            ),
            namespace=namespace,
            operation_timeout_seconds=timeout,
        )

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    async def _eval(self, *args: Any) -> Any:
        try:
            return await asyncio.wait_for(
                self.client.eval(*args),
                timeout=self.operation_timeout_seconds,
            )
        except TimeoutError as exc:
            raise LimitBackendUnavailable(
                "redis concurrency operation timed out"
            ) from exc

    async def acquire(self, key: str, limit: int, ttl_seconds: float) -> str | None:
        ttl_ms = max(1, int(ttl_seconds * 1000))
        token = uuid.uuid4().hex
        acquired = await self._eval(
            self._ACQUIRE,
            1,
            self._key(key),
            limit,
            token,
            ttl_ms,
        )
        return token if int(acquired or 0) == 1 else None

    async def renew(self, key: str, token: str, ttl_seconds: float) -> bool:
        ttl_ms = max(1, int(ttl_seconds * 1000))
        renewed = await self._eval(
            self._RENEW,
            1,
            self._key(key),
            token,
            ttl_ms,
        )
        return int(renewed or 0) == 1

    async def release(self, key: str, token: str) -> None:
        await self._eval(self._RELEASE, 1, self._key(key), token)


def build_concurrency_limiter(
    *,
    environment: str,
    redis_url: str | None,
    provider_limit: int = 4,
    actor_limit: int = 4,
) -> "ConcurrencyLimiter":
    backend = RedisLeaseBackend.from_url(redis_url) if redis_url else None
    return ConcurrencyLimiter(
        provider_limit=provider_limit,
        actor_limit=actor_limit,
        backend=backend,
        environment=environment,
    )


class ConcurrencyLimiter:
    def __init__(
        self,
        *,
        provider_limit: int = 4,
        actor_limit: int = 4,
        backend: Any | None = None,
        environment: str = "development",
        lease_ttl_seconds: float = 30.0,
        renew_interval_seconds: float = 10.0,
        acquire_timeout_seconds: float = 30.0,
        acquire_poll_seconds: float = 0.05,
    ):
        if provider_limit < 1 or actor_limit < 1:
            raise ValueError("concurrency limits must be positive")
        self.provider_limit = provider_limit
        self.actor_limit = actor_limit
        self.backend = backend
        self.environment = environment
        self.lease_ttl_seconds = lease_ttl_seconds
        self.renew_interval_seconds = renew_interval_seconds
        self.acquire_timeout_seconds = max(0.001, float(acquire_timeout_seconds))
        self.acquire_poll_seconds = max(0.001, float(acquire_poll_seconds))
        self._provider_slots: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.provider_limit)
        )
        self._actor_slots: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self.actor_limit)
        )

    def slot(self, provider: str, model: str, actor_key: str) -> "LimitSlot":
        return LimitSlot(self, provider, model, actor_key)

    async def _acquire_local(self, provider_key: str, actor_key: str):
        provider_slot = self._provider_slots[provider_key]
        actor_slot = self._actor_slots[actor_key]
        await actor_slot.acquire()
        try:
            await provider_slot.acquire()
        except BaseException:
            actor_slot.release()
            raise
        return provider_slot, actor_slot


class LimitSlot(AbstractAsyncContextManager):
    def __init__(self, limiter: ConcurrencyLimiter, provider: str, model: str, actor_key: str):
        self.limiter = limiter
        self.provider_key = f"provider:{provider}:{model}"
        self.actor_key = f"actor:{actor_key}"
        self._backend_leases: list[tuple[str, str]] = []
        self._local_slots: tuple[asyncio.Semaphore, asyncio.Semaphore] | None = None
        self._renew_task: asyncio.Task | None = None
        self._owner: asyncio.Task | None = None
        self._renew_error: LimitBackendUnavailable | None = None

    async def __aenter__(self):
        self._owner = asyncio.current_task()
        backend = self.limiter.backend
        if backend is None:
            if self.limiter.environment == "production":
                raise LimitBackendUnavailable(
                    "a distributed concurrency backend is required in production"
                )
            self._local_slots = await self.limiter._acquire_local(
                self.provider_key, self.actor_key
            )
            return self

        deadline = time.monotonic() + self.limiter.acquire_timeout_seconds
        try:
            while True:
                provider_token = await backend.acquire(
                    self.provider_key,
                    self.limiter.provider_limit,
                    self.limiter.lease_ttl_seconds,
                )
                if provider_token:
                    self._backend_leases.append((self.provider_key, provider_token))
                    actor_token = await backend.acquire(
                        self.actor_key,
                        self.limiter.actor_limit,
                        self.limiter.lease_ttl_seconds,
                    )
                    if actor_token:
                        self._backend_leases.append((self.actor_key, actor_token))
                        break
                    await self._release_backend()
                if time.monotonic() >= deadline:
                    raise LimitExceeded("distributed concurrency capacity exhausted")
                await asyncio.sleep(
                    min(self.limiter.acquire_poll_seconds, max(0.0, deadline - time.monotonic()))
                )
        except (LimitExceeded, asyncio.CancelledError):
            await self._release_backend()
            raise
        except Exception as exc:
            await self._release_backend()
            if self.limiter.environment == "production":
                raise LimitBackendUnavailable("distributed concurrency backend unavailable") from exc
            self._local_slots = await self.limiter._acquire_local(
                self.provider_key, self.actor_key
            )
            return self

        self._renew_task = asyncio.create_task(self._renew_loop())
        return self

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.limiter.renew_interval_seconds)
                for key, token in tuple(self._backend_leases):
                    renewed = await self.limiter.backend.renew(
                        key, token, self.limiter.lease_ttl_seconds
                    )
                    if not renewed:
                        raise LimitBackendUnavailable(f"concurrency lease lost: {key}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.limiter.environment == "production" and self._owner is not None:
                self._renew_error = (
                    exc
                    if isinstance(exc, LimitBackendUnavailable)
                    else LimitBackendUnavailable("distributed concurrency lease renewal failed")
                )
                self._owner.cancel()

    async def _release_backend(self) -> None:
        backend = self.limiter.backend
        while self._backend_leases:
            key, token = self._backend_leases.pop()
            try:
                await backend.release(key, token)
            except Exception:
                # Expiry is the final safety net. Release errors must not mask
                # the body exception or leak past the bounded TTL.
                pass

    async def __aexit__(self, exc_type, exc, tb):
        if self._renew_task is not None:
            self._renew_task.cancel()
            await asyncio.gather(self._renew_task, return_exceptions=True)
            self._renew_task = None
        await self._release_backend()
        if self._local_slots is not None:
            provider_slot, actor_slot = self._local_slots
            actor_slot.release()
            provider_slot.release()
            self._local_slots = None
        if self._renew_error is not None:
            raise self._renew_error
        return False
