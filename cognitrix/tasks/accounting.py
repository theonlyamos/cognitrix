"""Context-bound accounting shared by direct and Session LLM/tool calls."""

from __future__ import annotations

import inspect
import json
import time
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from decimal import Decimal

from cognitrix.providers.limits import ConcurrencyLimiter, build_concurrency_limiter
from cognitrix.tasks.budget import BudgetLedger, TokenReservation

UsageCallback = Callable[[dict[str, int | str]], Awaitable[None] | None]


@dataclass
class TaskUsageCollector:
    """Task-local usage delta, isolated from concurrently running siblings."""

    parent: "TaskUsageCollector | None" = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_attempts: int = 0
    duration_seconds: float = 0.0
    cost_usd: Decimal = Decimal("0")

    def record_llm(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float,
        cost_usd: Decimal,
    ) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.llm_calls += 1
        self.duration_seconds += max(0.0, duration_seconds)
        self.cost_usd += cost_usd
        if self.parent is not None:
            self.parent.record_llm(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_seconds=duration_seconds,
                cost_usd=cost_usd,
            )

    def record_tool_attempt(self, *, first_for_call: bool) -> None:
        self.tool_attempts += 1
        if first_for_call:
            self.tool_calls += 1
        if self.parent is not None:
            self.parent.record_tool_attempt(first_for_call=first_for_call)

    def snapshot(self) -> dict[str, int | float | str]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tool_attempts": self.tool_attempts,
            "duration_seconds": self.duration_seconds,
            "cost_usd": format(self.cost_usd, "f"),
        }


_CURRENT_USAGE: ContextVar[TaskUsageCollector | None] = ContextVar(
    "cognitrix_task_usage_collector", default=None
)


@asynccontextmanager
async def capture_task_usage() -> AsyncIterator[TaskUsageCollector]:
    """Capture only usage produced in this async task and its children."""
    collector = TaskUsageCollector(parent=_CURRENT_USAGE.get())
    token = _CURRENT_USAGE.set(collector)
    try:
        yield collector
    finally:
        _CURRENT_USAGE.reset(token)


def estimate_prompt_tokens(prompt: list[dict[str, Any]]) -> int:
    """Return a conservative dependency-free prompt estimate."""
    encoded = json.dumps(prompt, ensure_ascii=False, default=str, separators=(",", ":"))
    return max(1, (len(encoded.encode("utf-8")) + 2) // 3)


def estimate_request_tokens(prompt: list[dict[str, Any]], max_output_tokens: int) -> int:
    """Compatibility helper for a complete prompt + output reservation."""
    return estimate_prompt_tokens(prompt) + max(0, int(max_output_tokens or 0))


class _LLMCall:
    def __init__(
        self,
        accounting: "TaskAccounting",
        reservation: TokenReservation,
        slot: Any,
        prompt_estimate: int,
        requested_output_tokens: int,
        output_tokens: int,
        runtime_llm: Any,
    ):
        self.accounting = accounting
        self.reservation: TokenReservation | None = reservation
        self.slot = slot
        self.prompt_estimate = prompt_estimate
        self.requested_output_tokens = requested_output_tokens
        self.output_tokens = output_tokens
        self.runtime_llm = runtime_llm
        self.closed = False
        self.provider_started = False
        self.started_at = time.monotonic()

    def mark_provider_started(self) -> None:
        self.provider_started = True

    def _record_usage(
        self,
        prompt: int,
        completion: int,
        reservation: TokenReservation,
    ) -> None:
        collector = _CURRENT_USAGE.get()
        if collector is None:
            return
        price = reservation.price
        cost = Decimal("0")
        if price is not None:
            prompt_rate = Decimal(str(price.get("prompt_per_million", 0)))
            completion_rate = Decimal(str(price.get("completion_per_million", 0)))
            cost = (
                Decimal(prompt) * prompt_rate
                + Decimal(completion) * completion_rate
            ) / Decimal(1_000_000)
        collector.record_llm(
            prompt_tokens=prompt,
            completion_tokens=completion,
            duration_seconds=time.monotonic() - self.started_at,
            cost_usd=cost,
        )

    async def _finish_attempt(self, response: Any) -> None:
        reservation = self.reservation
        if reservation is None:
            return
        usage = getattr(response, "usage", None) or {}
        prompt_tokens = _nonnegative_int(usage.get("prompt_tokens"))
        completion_tokens = _nonnegative_int(usage.get("completion_tokens"))
        has_provider_usage = prompt_tokens is not None or completion_tokens is not None
        if has_provider_usage:
            prompt = prompt_tokens or 0
            completion = completion_tokens or 0
            actual = prompt + completion
        else:
            # A provider that omits usage cannot be measured exactly. Charge the
            # conservative prompt/output split retained by this attempt. Keeping
            # the split matters when completion tokens have a higher price.
            prompt = self.prompt_estimate
            completion = self.output_tokens
            actual = prompt + completion

        self._record_usage(prompt, completion, reservation)
        self.reservation = None
        self.provider_started = False

        error: BaseException | None = None
        try:
            await reservation.reconcile(
                actual,
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
        except BaseException as exc:
            error = exc
        try:
            await self.accounting.publish_usage()
        except BaseException as exc:
            if error is None:
                error = exc
        if error is not None:
            raise error

    async def finish_failed_provider_attempt(self) -> None:
        """Conservatively close one request that reached the provider."""
        await self._finish_attempt(None)

    async def begin_retry(self) -> int:
        """Open a separately bounded reservation while retaining the slot."""
        if self.closed:
            raise RuntimeError("cannot retry a closed LLM call")
        if self.reservation is not None:
            raise RuntimeError("provider attempt must finish before retrying")
        reservation, output_tokens = await self.accounting.ledger.begin_bounded_llm_retry(
            self.prompt_estimate,
            self.requested_output_tokens,
            provider=str(self.runtime_llm.provider),
            model=str(self.runtime_llm.model),
        )
        self.reservation = reservation
        self.output_tokens = output_tokens
        self.provider_started = True
        self.started_at = time.monotonic()
        await self.accounting.publish_usage()
        return output_tokens

    async def complete(self, response: Any) -> None:
        if self.closed:
            return
        self.closed = True
        error: BaseException | None = None
        try:
            await self._finish_attempt(response)
        except BaseException as exc:
            error = exc
        try:
            await self.slot.__aexit__(
                type(error) if error else None,
                error,
                error.__traceback__ if error else None,
            )
        except BaseException as exc:
            if error is None:
                error = exc
        if error is not None:
            raise error

    async def abort(self) -> None:
        if self.closed:
            return
        self.closed = True
        error: BaseException | None = None
        try:
            if self.reservation is not None:
                if self.provider_started:
                    # Once the request may have reached a provider, missing
                    # usage must charge that attempt's full split reservation.
                    await self._finish_attempt(None)
                else:
                    reservation = self.reservation
                    self.reservation = None
                    self._record_usage(0, 0, reservation)
                    await reservation.release()
                    await self.accounting.publish_usage()
        except BaseException as exc:
            error = exc
        try:
            await self.slot.__aexit__(
                type(error) if error else None,
                error,
                error.__traceback__ if error else None,
            )
        except BaseException as exc:
            if error is None:
                error = exc
        if error is not None:
            raise error


_CURRENT_LLM_CALL: ContextVar[_LLMCall | None] = ContextVar(
    "cognitrix_task_llm_call", default=None
)


def _nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


@dataclass
class TaskAccounting:
    ledger: BudgetLedger
    actor_key: str
    limiter: ConcurrencyLimiter
    on_usage: UsageCallback | None = None

    def __post_init__(self) -> None:
        self._publish_lock = __import__("asyncio").Lock()

    async def begin_llm(self, llm: Any, prompt: list[dict[str, Any]]) -> _LLMCall:
        await self.ledger.checkpoint()
        slot = self.limiter.slot(str(llm.provider), str(llm.model), self.actor_key)
        await self.ledger.wait_within_wall(slot.__aenter__())
        requested_output = int(getattr(llm, "max_tokens", 0) or 0)
        prompt_estimate = estimate_prompt_tokens(prompt)
        try:
            reservation, output_tokens = await self.ledger.begin_bounded_llm_call(
                prompt_estimate,
                requested_output,
                provider=str(llm.provider),
                model=str(llm.model),
            )
        except BaseException as exc:
            await slot.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        runtime_llm = llm
        if output_tokens != requested_output:
            runtime_llm = llm.model_copy(deep=True)
            runtime_llm.max_tokens = output_tokens
        return _LLMCall(
            self,
            reservation,
            slot,
            prompt_estimate,
            requested_output,
            output_tokens,
            runtime_llm,
        )

    def _active_llm_call(self) -> _LLMCall | None:
        call = _CURRENT_LLM_CALL.get()
        if call is None or call.accounting is not self:
            return None
        return call

    async def finish_failed_provider_attempt(self) -> bool:
        call = self._active_llm_call()
        if call is None:
            return False
        await call.finish_failed_provider_attempt()
        return True

    async def begin_provider_retry(self) -> int | None:
        call = self._active_llm_call()
        if call is None:
            # Compatibility for direct manager use outside the LLM wrapper.
            await self.consume_provider_retry()
            return None
        return await call.begin_retry()

    async def consume_tool_attempt(self, *, first_for_call: bool) -> None:
        await self.ledger.consume_tool_attempt(first_for_call=first_for_call)
        collector = _CURRENT_USAGE.get()
        if collector is not None:
            collector.record_tool_attempt(first_for_call=first_for_call)
        await self.publish_usage()

    async def consume_retry(self) -> None:
        await self.ledger.consume_retry()
        await self.publish_usage()

    async def consume_provider_retry(self) -> None:
        await self.ledger.consume_provider_retry()
        await self.publish_usage()

    async def wait_within_wall(self, awaitable, *, timeout: float | None = None):
        return await self.ledger.wait_within_wall(awaitable, timeout=timeout)

    async def sleep_within_wall(self, delay: float) -> None:
        await self.ledger.sleep_within_wall(delay)

    async def publish_usage(self) -> None:
        if self.on_usage is None:
            return
        async with self._publish_lock:
            result = self.on_usage(self.ledger.snapshot())
            if inspect.isawaitable(result):
                await result


_CURRENT: ContextVar[TaskAccounting | None] = ContextVar(
    "cognitrix_task_accounting", default=None
)
_DEFAULT_LIMITER: ConcurrencyLimiter | None = None


def _default_limiter() -> ConcurrencyLimiter:
    global _DEFAULT_LIMITER
    if _DEFAULT_LIMITER is None:
        from cognitrix.config import settings

        configured_url = os.getenv("TASK_LIMIT_REDIS_URL") or os.getenv("CELERY_BROKER_URL")
        redis_url = configured_url if configured_url and configured_url.startswith(("redis://", "rediss://")) else None
        _DEFAULT_LIMITER = build_concurrency_limiter(
            environment=settings.env,
            redis_url=redis_url,
            provider_limit=max(1, int(os.getenv("TASK_PROVIDER_CONCURRENCY", "4"))),
            actor_limit=max(1, int(os.getenv("TASK_ACTOR_CONCURRENCY", "4"))),
        )
    return _DEFAULT_LIMITER


def current_task_accounting() -> TaskAccounting | None:
    return _CURRENT.get()


@asynccontextmanager
async def task_accounting_scope(
    ledger: BudgetLedger,
    *,
    actor_key: str,
    limiter: ConcurrencyLimiter | None = None,
    on_usage: UsageCallback | None = None,
) -> AsyncIterator[TaskAccounting]:
    accounting = TaskAccounting(
        ledger=ledger,
        actor_key=actor_key,
        limiter=limiter or _default_limiter(),
        on_usage=on_usage,
    )
    token = _CURRENT.set(accounting)
    try:
        yield accounting
    finally:
        _CURRENT.reset(token)


async def wrap_llm_result(
    llm: Any,
    prompt: list[dict[str, Any]],
    invoke: Callable[[Any], Awaitable[Any]],
    *,
    stream: bool,
) -> Any:
    """Instrument one LLM request while preserving its public return shape."""
    accounting = current_task_accounting()
    if accounting is None:
        return await invoke(llm)

    call = await accounting.begin_llm(llm, prompt)
    call_token = _CURRENT_LLM_CALL.set(call)
    try:
        if not stream:
            call.mark_provider_started()
        result = await accounting.wait_within_wall(invoke(call.runtime_llm))
    except BaseException:
        await call.abort()
        raise
    finally:
        _CURRENT_LLM_CALL.reset(call_token)

    if not stream:
        await call.complete(result)
        return result

    async def instrumented_stream():
        last = None
        stream_token = _CURRENT_LLM_CALL.set(call)
        try:
            call.mark_provider_started()
            iterator = result.__aiter__()
            while True:
                try:
                    response = await accounting.wait_within_wall(anext(iterator))
                except StopAsyncIteration:
                    break
                last = response
                yield response
        finally:
            try:
                if not call.closed:
                    await call.complete(last)
            finally:
                _CURRENT_LLM_CALL.reset(stream_token)

    return instrumented_stream()
