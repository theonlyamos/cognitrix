import asyncio
from decimal import Decimal

import pytest

from cognitrix.tasks.budget import (
    BudgetExceeded,
    BudgetLedger,
    TaskBudget,
    UnknownModelPricing,
    stable_actor_key,
)


@pytest.mark.asyncio
async def test_concurrent_token_reservations_cannot_exceed_limit():
    ledger = BudgetLedger(TaskBudget(max_tokens=100))

    async def reserve():
        try:
            return await ledger.reserve_tokens(60)
        except BudgetExceeded:
            return None

    first, second = await asyncio.gather(reserve(), reserve())
    assert (first is None) != (second is None)
    winner = first or second
    await winner.reconcile(40)
    next_reservation = await ledger.reserve_tokens(60)
    await next_reservation.reconcile(60)
    assert ledger.snapshot()["total_tokens"] == 100


@pytest.mark.asyncio
async def test_call_attempt_step_and_parallel_budgets_are_independent():
    ledger = BudgetLedger(TaskBudget(
        max_llm_calls=1,
        max_tool_calls=2,
        max_tool_attempts=3,
        max_retries=1,
        max_steps=1,
        max_parallel=1,
    ))
    await ledger.consume_llm_call()
    with pytest.raises(BudgetExceeded, match="llm_calls"):
        await ledger.consume_llm_call()

    await ledger.consume_tool_call(attempts=2)
    with pytest.raises(BudgetExceeded, match="tool_attempts"):
        await ledger.consume_tool_call(attempts=2)

    await ledger.consume_step()
    with pytest.raises(BudgetExceeded, match="steps"):
        await ledger.consume_step()

    await ledger.consume_retry()
    with pytest.raises(BudgetExceeded, match="retries"):
        await ledger.consume_retry()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with ledger.parallel_slot():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()
    with pytest.raises(BudgetExceeded, match="parallel"):
        await ledger.acquire_parallel(wait=False)
    release.set()
    await holder


@pytest.mark.asyncio
async def test_cost_budget_rejects_unknown_actual_model_before_call_reservation():
    ledger = BudgetLedger(TaskBudget(max_cost_usd=Decimal("1")), pricing={})
    with pytest.raises(UnknownModelPricing, match="openai/unknown"):
        await ledger.begin_bounded_llm_call(
            10,
            20,
            provider="openai",
            model="unknown",
        )
    assert ledger.snapshot()["llm_calls"] == 0


@pytest.mark.asyncio
async def test_usage_reconciliation_tracks_cost_from_known_pricing():
    ledger = BudgetLedger(
        TaskBudget(max_cost_usd=Decimal("1")),
        provider="openai",
        model="m",
        pricing={"openai/m": {"prompt_per_million": "1", "completion_per_million": "2"}},
    )
    reservation = await ledger.reserve_tokens(100)
    await reservation.reconcile(30, prompt_tokens=10, completion_tokens=20)
    usage = ledger.snapshot()
    assert usage["total_tokens"] == 30
    assert Decimal(usage["cost_usd"]) == Decimal("0.00005")


@pytest.mark.asyncio
async def test_mixed_models_charge_their_own_rates():
    ledger = BudgetLedger(
        TaskBudget(max_cost_usd=Decimal("1")),
        pricing={
            "openai/cheap": {
                "prompt_per_million": "1",
                "completion_per_million": "2",
            },
            "google/premium": {
                "prompt_per_million": "10",
                "completion_per_million": "20",
            },
        },
    )
    first, _ = await ledger.begin_bounded_llm_call(
        10,
        20,
        provider="openai",
        model="cheap",
    )
    await first.reconcile(30, prompt_tokens=10, completion_tokens=20)
    second, _ = await ledger.begin_bounded_llm_call(
        10,
        20,
        provider="google",
        model="premium",
    )
    await second.reconcile(30, prompt_tokens=10, completion_tokens=20)

    assert Decimal(ledger.snapshot()["cost_usd"]) == Decimal("0.00055")


@pytest.mark.asyncio
async def test_provider_retry_opens_an_independently_bounded_reservation():
    ledger = BudgetLedger(
        TaskBudget(max_tokens=100, max_llm_calls=2, max_retries=1),
    )
    first, first_output = await ledger.begin_bounded_llm_call(10, 20)
    assert first_output == 20
    await first.reconcile(30, prompt_tokens=10, completion_tokens=20)

    retry, retry_output = await ledger.begin_bounded_llm_retry(10, 20)
    assert retry_output == 20
    reserved = ledger.snapshot()
    assert reserved["llm_calls"] == 2
    assert reserved["retries"] == 1
    assert reserved["reserved_tokens"] == 30

    await retry.reconcile(15, prompt_tokens=10, completion_tokens=5)
    final = ledger.snapshot()
    assert final["total_tokens"] == 45
    assert final["reserved_tokens"] == 0


@pytest.mark.asyncio
async def test_wall_budget_stops_new_work():
    now = iter([10.0, 12.0])
    ledger = BudgetLedger(TaskBudget(wall_seconds=1), clock=lambda: next(now))
    with pytest.raises(BudgetExceeded, match="wall_seconds"):
        await ledger.checkpoint()


@pytest.mark.asyncio
async def test_ledger_restores_durable_usage_and_enforces_only_remaining_budget():
    ledger = BudgetLedger(
        TaskBudget(max_tokens=100, max_llm_calls=2, max_tool_attempts=3),
        initial_usage={
            "total_tokens": 80,
            "prompt_tokens": 60,
            "completion_tokens": 20,
            "llm_calls": 1,
            "tool_calls": 1,
            "tool_attempts": 2,
            "cost_usd": "0.25",
        },
    )

    assert ledger.snapshot()["total_tokens"] == 80
    assert ledger.snapshot()["cost_usd"] == "0.25"
    with pytest.raises(BudgetExceeded, match="tokens"):
        await ledger.begin_llm_call(21)
    await ledger.consume_tool_attempt(first_for_call=False)
    with pytest.raises(BudgetExceeded, match="tool_attempts"):
        await ledger.consume_tool_attempt(first_for_call=False)


def test_actor_keys_are_stable_sanitized_and_never_embed_subjects():
    first = stable_actor_key("jwt", "user@example.com / token")
    second = stable_actor_key("jwt", "user@example.com / token")
    assert first == second
    assert first.startswith("jwt:")
    assert "user@example.com" not in first
    assert stable_actor_key("scheduler") == "scheduler"
    assert stable_actor_key("system") == "system"
