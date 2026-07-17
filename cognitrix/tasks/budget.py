"""Concurrency-safe task budgets and usage accounting."""

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cognitrix.errors import ExecutionControlError


class BudgetExceeded(ExecutionControlError):
    """No new work may start because a snapshotted run limit is exhausted."""


class UnknownModelPricing(ValueError):
    """A cost budget cannot be enforced for an unpriced provider/model."""


ModelPrice = dict[str, str | int | float | Decimal]


def _estimated_cost(
    price: ModelPrice | None,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    conservative_tokens: int = 0,
) -> Decimal:
    if price is None:
        return Decimal("0")
    prompt_rate = Decimal(str(price.get("prompt_per_million", 0)))
    completion_rate = Decimal(str(price.get("completion_per_million", 0)))
    if conservative_tokens:
        return (
            Decimal(conservative_tokens) * max(prompt_rate, completion_rate)
        ) / Decimal(1_000_000)
    return (
        Decimal(prompt_tokens) * prompt_rate
        + Decimal(completion_tokens) * completion_rate
    ) / Decimal(1_000_000)


def configured_model_pricing(raw: str | None = None) -> dict[str, ModelPrice]:
    """Load and validate the production provider/model pricing registry."""
    source = raw if raw is not None else os.getenv("COGNITRIX_MODEL_PRICING_JSON", "{}")
    try:
        payload = json.loads(source or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("COGNITRIX_MODEL_PRICING_JSON must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("COGNITRIX_MODEL_PRICING_JSON must be an object")
    normalized: dict[str, ModelPrice] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or "/" not in key or not isinstance(value, dict):
            raise ValueError("model pricing entries must use provider/model object keys")
        rates: ModelPrice = {}
        for rate_name in ("prompt_per_million", "completion_per_million"):
            try:
                rate = Decimal(str(value.get(rate_name, 0)))
            except Exception as exc:
                raise ValueError(f"invalid {rate_name} for {key}") from exc
            if not rate.is_finite() or rate < 0:
                raise ValueError(f"invalid {rate_name} for {key}")
            rates[rate_name] = format(rate, "f")
        normalized[key] = rates
    return normalized


class TaskBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_seconds: float | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, ge=1)
    max_llm_calls: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_tool_attempts: int | None = Field(default=None, ge=1)
    max_retries: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=1)
    max_parallel: int | None = Field(default=None, ge=1)
    max_cost_usd: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _bounded_parallel(self):
        if self.max_steps is not None and self.max_parallel is not None:
            if self.max_parallel > self.max_steps:
                object.__setattr__(self, "max_parallel", self.max_steps)
        return self


def stable_actor_key(
    kind: Literal["jwt", "api_key", "scheduler", "system"],
    identifier: str | None = None,
) -> str:
    """Return a stable limiter identity without exposing a subject or key id."""
    if kind in {"scheduler", "system"}:
        return kind
    if not identifier:
        raise ValueError(f"{kind} actor identity is required")
    digest = hashlib.sha256(str(identifier).encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


class TokenReservation:
    def __init__(
        self,
        ledger: "BudgetLedger",
        estimate: int,
        price: ModelPrice | None,
        reserved_cost: Decimal = Decimal("0"),
    ):
        self._ledger = ledger
        self.estimate = estimate
        self.price = price
        self.reserved_cost = reserved_cost
        self._reconciled = False

    async def reconcile(
        self,
        actual_tokens: int,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        if self._reconciled:
            raise RuntimeError("token reservation already reconciled")
        self._reconciled = True
        await self._ledger._reconcile_tokens(
            self.estimate,
            actual_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            price=self.price,
            reserved_cost=self.reserved_cost,
        )

    async def release(self) -> None:
        if self._reconciled:
            return
        self._reconciled = True
        await self._ledger._release_token_reservation(
            self.estimate,
            self.reserved_cost,
        )


class BudgetLedger:
    """One per-run ledger; every reservation/check is serialized by one lock."""

    def __init__(
        self,
        budget: TaskBudget | dict[str, Any] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        pricing: dict[str, ModelPrice] | None = None,
        initial_usage: dict[str, Any] | None = None,
        initial_wall_seconds: float = 0.0,
        clock=None,
    ):
        self.budget = budget if isinstance(budget, TaskBudget) else TaskBudget.model_validate(budget or {})
        self.provider = provider or ""
        self.model = model or ""
        self._pricing = pricing or {}
        self._price = self._pricing.get(f"{self.provider}/{self.model}")
        self._clock = clock or time.monotonic
        self._started = self._clock() - max(0.0, float(initial_wall_seconds))
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        stored = initial_usage or {}
        self._reserved_tokens = max(0, int(stored.get("reserved_tokens", 0) or 0))
        self._reserved_cost = max(
            Decimal("0"),
            Decimal(str(stored.get("reserved_cost_usd", "0") or "0")),
        )
        self._active_parallel = 0
        defaults: dict[str, int | Decimal] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "tool_attempts": 0,
            "retries": 0,
            "steps": 0,
            "cost_usd": Decimal("0"),
        }
        self._usage = {
            key: (
                max(Decimal("0"), Decimal(str(stored.get(key, default) or 0)))
                if key == "cost_usd"
                else max(0, int(stored.get(key, default) or 0))
            )
            for key, default in defaults.items()
        }

    def _raise_if_wall_expired(self) -> None:
        limit = self.budget.wall_seconds
        if limit is not None and self._clock() - self._started > limit:
            raise BudgetExceeded("budget_exceeded: wall_seconds")

    def remaining_wall_seconds(self) -> float | None:
        """Return the live run deadline; raise once no execution time remains."""
        limit = self.budget.wall_seconds
        if limit is None:
            return None
        remaining = float(limit) - (self._clock() - self._started)
        if remaining <= 0:
            raise BudgetExceeded("budget_exceeded: wall_seconds")
        return remaining

    async def wait_within_wall(self, awaitable, *, timeout: float | None = None):
        """Bound an in-flight operation by both its own and the run deadline."""
        wall = self.remaining_wall_seconds()
        effective = timeout
        if wall is not None:
            effective = wall if effective is None else min(float(effective), wall)
        if effective is None:
            return await awaitable
        try:
            return await asyncio.wait_for(awaitable, timeout=max(0.001, effective))
        except asyncio.TimeoutError as exc:
            # An operation-specific timeout is its caller's concern. If the
            # wall was the tighter bound, preserve the budget control signal.
            if wall is not None and (timeout is None or wall <= float(timeout)):
                raise BudgetExceeded("budget_exceeded: wall_seconds") from exc
            raise

    async def sleep_within_wall(self, delay: float) -> None:
        await self.wait_within_wall(asyncio.sleep(max(0.0, float(delay))))

    async def checkpoint(self) -> None:
        async with self._lock:
            self._raise_if_wall_expired()

    def price_for(self, provider: str | None = None, model: str | None = None) -> ModelPrice | None:
        provider_name = self.provider if provider is None else str(provider)
        model_name = self.model if model is None else str(model)
        price = self._pricing.get(f"{provider_name}/{model_name}")
        if self.budget.max_cost_usd is not None and price is None:
            raise UnknownModelPricing(
                f"No pricing is configured for {provider_name}/{model_name}"
            )
        return price

    async def reserve_tokens(
        self,
        estimate: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> TokenReservation:
        if estimate < 0:
            raise ValueError("token estimate must be non-negative")
        price = self.price_for(provider, model)
        reserved_cost = _estimated_cost(price, conservative_tokens=estimate)
        async with self._lock:
            self._raise_if_wall_expired()
            limit = self.budget.max_tokens
            used = int(self._usage["total_tokens"])
            if limit is not None and used + self._reserved_tokens + estimate > limit:
                raise BudgetExceeded("budget_exceeded: tokens")
            if (
                self.budget.max_cost_usd is not None
                and Decimal(self._usage["cost_usd"])
                + self._reserved_cost
                + reserved_cost
                > self.budget.max_cost_usd
            ):
                raise BudgetExceeded("budget_exceeded: cost_usd")
            self._reserved_tokens += estimate
            self._reserved_cost += reserved_cost
        return TokenReservation(self, estimate, price, reserved_cost)

    async def _release_token_reservation(
        self,
        estimate: int,
        reserved_cost: Decimal,
    ) -> None:
        async with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - estimate)
            self._reserved_cost = max(
                Decimal("0"), self._reserved_cost - reserved_cost
            )

    async def _reconcile_tokens(
        self,
        estimate: int,
        actual: int,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        price: ModelPrice | None,
        reserved_cost: Decimal,
    ) -> None:
        if actual < 0:
            raise ValueError("actual token usage must be non-negative")
        prompt = actual if prompt_tokens is None and completion_tokens is None else int(prompt_tokens or 0)
        completion = 0 if prompt_tokens is None and completion_tokens is None else int(completion_tokens or 0)
        if prompt + completion != actual:
            raise ValueError("prompt and completion usage must sum to actual tokens")
        async with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - estimate)
            self._reserved_cost = max(
                Decimal("0"), self._reserved_cost - reserved_cost
            )
            self._usage["prompt_tokens"] = int(self._usage["prompt_tokens"]) + prompt
            self._usage["completion_tokens"] = int(self._usage["completion_tokens"]) + completion
            self._usage["total_tokens"] = int(self._usage["total_tokens"]) + actual
            if price is not None:
                prompt_rate = Decimal(str(price.get("prompt_per_million", 0)))
                completion_rate = Decimal(str(price.get("completion_per_million", 0)))
                cost = (
                    Decimal(prompt) * prompt_rate
                    + Decimal(completion) * completion_rate
                ) / Decimal(1_000_000)
                self._usage["cost_usd"] = Decimal(self._usage["cost_usd"]) + cost

            token_limit = self.budget.max_tokens
            cost_limit = self.budget.max_cost_usd
            if token_limit is not None and int(self._usage["total_tokens"]) > token_limit:
                raise BudgetExceeded("budget_exceeded: tokens")
            if cost_limit is not None and Decimal(self._usage["cost_usd"]) > cost_limit:
                raise BudgetExceeded("budget_exceeded: cost_usd")

    async def _consume(self, field: str, amount: int, limit: int | None) -> None:
        async with self._lock:
            self._raise_if_wall_expired()
            current = int(self._usage[field])
            if limit is not None and current + amount > limit:
                raise BudgetExceeded(f"budget_exceeded: {field}")
            self._usage[field] = current + amount

    async def consume_llm_call(self) -> None:
        await self._consume("llm_calls", 1, self.budget.max_llm_calls)

    async def consume_provider_retry(self) -> None:
        """Atomically authorize one additional real provider request."""
        async with self._lock:
            self._raise_if_wall_expired()
            calls = int(self._usage["llm_calls"])
            retries = int(self._usage["retries"])
            if self.budget.max_retries is not None and retries + 1 > self.budget.max_retries:
                raise BudgetExceeded("budget_exceeded: retries")
            if self.budget.max_llm_calls is not None and calls + 1 > self.budget.max_llm_calls:
                raise BudgetExceeded("budget_exceeded: llm_calls")
            self._usage["retries"] = retries + 1
            self._usage["llm_calls"] = calls + 1

    async def begin_llm_call(
        self,
        token_estimate: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> TokenReservation:
        """Atomically reserve one provider call and its concurrent token budget."""
        if token_estimate < 0:
            raise ValueError("token estimate must be non-negative")
        price = self.price_for(provider, model)
        reserved_cost = _estimated_cost(
            price,
            conservative_tokens=token_estimate,
        )
        async with self._lock:
            self._raise_if_wall_expired()
            calls = int(self._usage["llm_calls"])
            tokens = int(self._usage["total_tokens"])
            if self.budget.max_llm_calls is not None and calls + 1 > self.budget.max_llm_calls:
                raise BudgetExceeded("budget_exceeded: llm_calls")
            if (
                self.budget.max_tokens is not None
                and tokens + self._reserved_tokens + token_estimate > self.budget.max_tokens
            ):
                raise BudgetExceeded("budget_exceeded: tokens")
            if (
                self.budget.max_cost_usd is not None
                and Decimal(self._usage["cost_usd"])
                + self._reserved_cost
                + reserved_cost
                > self.budget.max_cost_usd
            ):
                raise BudgetExceeded("budget_exceeded: cost_usd")
            self._usage["llm_calls"] = calls + 1
            self._reserved_tokens += token_estimate
            self._reserved_cost += reserved_cost
        return TokenReservation(self, token_estimate, price, reserved_cost)

    async def begin_bounded_llm_call(
        self,
        prompt_estimate: int,
        requested_output_tokens: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[TokenReservation, int]:
        """Atomically clamp and reserve the first provider attempt."""
        return await self._begin_bounded_llm_attempt(
            prompt_estimate,
            requested_output_tokens,
            provider=provider,
            model=model,
            is_retry=False,
        )

    async def begin_bounded_llm_retry(
        self,
        prompt_estimate: int,
        requested_output_tokens: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[TokenReservation, int]:
        """Atomically authorize and reserve one additional provider attempt."""
        return await self._begin_bounded_llm_attempt(
            prompt_estimate,
            requested_output_tokens,
            provider=provider,
            model=model,
            is_retry=True,
        )

    async def _begin_bounded_llm_attempt(
        self,
        prompt_estimate: int,
        requested_output_tokens: int,
        *,
        provider: str | None,
        model: str | None,
        is_retry: bool,
    ) -> tuple[TokenReservation, int]:
        if prompt_estimate < 0 or requested_output_tokens < 1:
            raise ValueError("LLM token estimates must be positive")
        price = self.price_for(provider, model)
        async with self._lock:
            self._raise_if_wall_expired()
            calls = int(self._usage["llm_calls"])
            retries = int(self._usage["retries"])
            used = int(self._usage["total_tokens"])
            if (
                is_retry
                and self.budget.max_retries is not None
                and retries + 1 > self.budget.max_retries
            ):
                raise BudgetExceeded("budget_exceeded: retries")
            if (
                self.budget.max_llm_calls is not None
                and calls + 1 > self.budget.max_llm_calls
            ):
                raise BudgetExceeded("budget_exceeded: llm_calls")

            output_tokens = requested_output_tokens
            if self.budget.max_tokens is not None:
                remaining = (
                    self.budget.max_tokens
                    - used
                    - self._reserved_tokens
                    - prompt_estimate
                )
                if remaining < 1:
                    raise BudgetExceeded("budget_exceeded: tokens")
                output_tokens = min(output_tokens, remaining)

            reservation = prompt_estimate + output_tokens
            reserved_cost = _estimated_cost(
                price,
                prompt_tokens=prompt_estimate,
                completion_tokens=output_tokens,
            )
            if (
                self.budget.max_cost_usd is not None
                and Decimal(self._usage["cost_usd"])
                + self._reserved_cost
                + reserved_cost
                > self.budget.max_cost_usd
            ):
                raise BudgetExceeded("budget_exceeded: cost_usd")
            self._usage["llm_calls"] = calls + 1
            if is_retry:
                self._usage["retries"] = retries + 1
            self._reserved_tokens += reservation
            self._reserved_cost += reserved_cost
        return TokenReservation(
            self,
            reservation,
            price,
            reserved_cost,
        ), output_tokens

    async def consume_tool_call(self, *, attempts: int = 1) -> None:
        async with self._lock:
            self._raise_if_wall_expired()
            calls = int(self._usage["tool_calls"])
            used_attempts = int(self._usage["tool_attempts"])
            if self.budget.max_tool_calls is not None and calls + 1 > self.budget.max_tool_calls:
                raise BudgetExceeded("budget_exceeded: tool_calls")
            if self.budget.max_tool_attempts is not None and used_attempts + attempts > self.budget.max_tool_attempts:
                raise BudgetExceeded("budget_exceeded: tool_attempts")
            self._usage["tool_calls"] = calls + 1
            self._usage["tool_attempts"] = used_attempts + attempts

    async def consume_tool_attempt(self, *, first_for_call: bool) -> None:
        """Reserve an actual tool attempt, counting its logical call once."""
        async with self._lock:
            self._raise_if_wall_expired()
            calls = int(self._usage["tool_calls"])
            attempts = int(self._usage["tool_attempts"])
            next_calls = calls + (1 if first_for_call else 0)
            retries = int(self._usage["retries"])
            next_retries = retries + (0 if first_for_call else 1)
            if self.budget.max_tool_calls is not None and next_calls > self.budget.max_tool_calls:
                raise BudgetExceeded("budget_exceeded: tool_calls")
            if self.budget.max_tool_attempts is not None and attempts + 1 > self.budget.max_tool_attempts:
                raise BudgetExceeded("budget_exceeded: tool_attempts")
            if self.budget.max_retries is not None and next_retries > self.budget.max_retries:
                raise BudgetExceeded("budget_exceeded: retries")
            self._usage["tool_calls"] = next_calls
            self._usage["tool_attempts"] = attempts + 1
            self._usage["retries"] = next_retries

    async def consume_step(self) -> None:
        await self._consume("steps", 1, self.budget.max_steps)

    async def consume_retry(self) -> None:
        await self._consume("retries", 1, self.budget.max_retries)

    async def acquire_parallel(self, *, wait: bool = True) -> None:
        async with self._condition:
            self._raise_if_wall_expired()
            limit = self.budget.max_parallel
            if limit is None:
                self._active_parallel += 1
                return
            if not wait and self._active_parallel >= limit:
                raise BudgetExceeded("budget_exceeded: parallel")
            while self._active_parallel >= limit:
                await self._condition.wait()
                self._raise_if_wall_expired()
            self._active_parallel += 1

    async def release_parallel(self) -> None:
        async with self._condition:
            self._active_parallel = max(0, self._active_parallel - 1)
            self._condition.notify(1)

    @asynccontextmanager
    async def parallel_slot(self):
        await self.acquire_parallel()
        try:
            yield
        finally:
            await self.release_parallel()

    def snapshot(self) -> dict[str, int | str]:
        snapshot = {
            key: format(value, "f") if isinstance(value, Decimal) else int(value)
            for key, value in self._usage.items()
        }
        snapshot["reserved_tokens"] = self._reserved_tokens
        snapshot["reserved_cost_usd"] = format(self._reserved_cost, "f")
        return snapshot
