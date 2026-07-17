import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cognitrix.providers.base import LLM, LLMManager
from cognitrix.tasks.accounting import (
    capture_task_usage,
    estimate_prompt_tokens,
    estimate_request_tokens,
    task_accounting_scope,
)
from cognitrix.tasks.budget import BudgetExceeded, BudgetLedger, TaskBudget
from cognitrix.tools.resilient_tool_wrapper import ResilientToolManager
from cognitrix.utils.llm_response import LLMResponse


class RecordingSlot:
    def __init__(self, recorder):
        self.recorder = recorder

    async def __aenter__(self):
        self.recorder.append("entered")
        return self

    async def __aexit__(self, *_args):
        self.recorder.append("released")


class RecordingLimiter:
    def __init__(self):
        self.events = []
        self.keys = []

    def slot(self, provider, model, actor_key):
        self.keys.append((provider, model, actor_key))
        return RecordingSlot(self.events)


def fake_llm(*, max_tokens=12):
    return LLM(
        provider="test",
        model="model",
        max_tokens=max_tokens,
        temperature=0,
        api_key="secret",
        base_url="https://provider.test/v1",
        supports_tool_use=True,
    )


def response(*, prompt_tokens=5, completion_tokens=3):
    item = LLMResponse()
    item.llm_response = "ok"
    item.result = "ok"
    item.usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return item


@pytest.mark.asyncio
async def test_direct_llm_call_accounts_provider_usage_and_releases_slot(monkeypatch):
    async def generate(*_args, **_kwargs):
        return response()

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    ledger = BudgetLedger(TaskBudget(max_tokens=100, max_llm_calls=1))
    limiter = RecordingLimiter()
    snapshots = []

    async with task_accounting_scope(
        ledger,
        actor_key="jwt:opaque",
        limiter=limiter,
        on_usage=lambda value: snapshots.append(value),
    ):
        result = await fake_llm()([{"role": "user", "content": "hello"}])

    assert result.llm_response == "ok"
    assert ledger.snapshot()["llm_calls"] == 1
    assert ledger.snapshot()["total_tokens"] == 8
    assert limiter.keys == [("test", "model", "jwt:opaque")]
    assert limiter.events == ["entered", "released"]
    assert snapshots[-1]["total_tokens"] == 8


@pytest.mark.asyncio
async def test_task_local_usage_capture_is_parallel_safe(monkeypatch):
    entered = 0

    async def generate(llm, prompt, *_args, **_kwargs):
        nonlocal entered
        entered += 1
        # Yield while both context-local collectors are live.  A barrier makes
        # this test deadlock when a regression correctly rejects one request
        # before it reaches the provider, obscuring the useful assertion.
        await asyncio.sleep(0.01)
        label = prompt[-1]["content"]
        return response(
            prompt_tokens=2 if label == "left" else 7,
            completion_tokens=3 if label == "left" else 11,
        )

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    ledger = BudgetLedger(TaskBudget(max_tokens=200, max_llm_calls=2))
    llm = fake_llm(max_tokens=12)

    async def invoke(label):
        async with capture_task_usage() as usage:
            await llm([{"role": "user", "content": label}])
        return usage.snapshot()

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        left = asyncio.create_task(invoke("left"))
        right = asyncio.create_task(invoke("right"))
        left_usage, right_usage = await asyncio.gather(left, right)

    assert left_usage["prompt_tokens"] == 2
    assert left_usage["completion_tokens"] == 3
    assert left_usage["llm_calls"] == 1
    assert right_usage["prompt_tokens"] == 7
    assert right_usage["completion_tokens"] == 11
    assert right_usage["llm_calls"] == 1
    assert ledger.snapshot()["total_tokens"] == 23


@pytest.mark.asyncio
async def test_streaming_holds_slot_until_iterator_finishes(monkeypatch):
    async def generate(*_args, **_kwargs):
        async def stream():
            yield response(prompt_tokens=7, completion_tokens=2)

        return stream()

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    ledger = BudgetLedger(TaskBudget(max_tokens=100))
    limiter = RecordingLimiter()

    async with task_accounting_scope(ledger, actor_key="system", limiter=limiter):
        stream = await fake_llm()([{"role": "user", "content": "hello"}], stream=True)
        assert limiter.events == ["entered"]
        chunks = [item async for item in stream]

    assert len(chunks) == 1
    assert limiter.events == ["entered", "released"]
    assert ledger.snapshot()["total_tokens"] == 9


@pytest.mark.asyncio
async def test_cancelled_partial_stream_reconciles_latest_usage(monkeypatch):
    release = asyncio.Event()

    async def generate(*_args, **_kwargs):
        async def stream():
            yield response(prompt_tokens=4, completion_tokens=3)
            await release.wait()

        return stream()

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    ledger = BudgetLedger(TaskBudget(max_tokens=100))

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        stream = await fake_llm(max_tokens=20)(
            [{"role": "user", "content": "hello"}],
            stream=True,
        )
        assert (await anext(stream)).usage["completion_tokens"] == 3
        await stream.aclose()

    assert ledger.snapshot()["total_tokens"] == 7


@pytest.mark.asyncio
async def test_cancelled_stream_without_usage_charges_full_reservation(monkeypatch):
    release = asyncio.Event()

    async def generate(*_args, **_kwargs):
        async def stream():
            item = response()
            item.usage = {}
            yield item
            await release.wait()

        return stream()

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    ledger = BudgetLedger(TaskBudget(max_tokens=100))
    prompt = [{"role": "user", "content": "hello"}]

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        stream = await fake_llm(max_tokens=20)(prompt, stream=True)
        await anext(stream)
        await stream.aclose()

    assert ledger.snapshot()["total_tokens"] == estimate_request_tokens(prompt, 20)


@pytest.mark.asyncio
async def test_tool_retries_are_counted_at_each_real_attempt():
    class RecoveringManager(ResilientToolManager):
        async def _attempt_param_recovery(self, *_args):
            return {"fixed": True}

    attempts = 0

    async def run(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("retry me")
        return "done"

    tool = SimpleNamespace(
        name="retry tool",
        description="test",
        run=run,
        validate_parameters=lambda value: value,
    )
    ledger = BudgetLedger(TaskBudget(max_tool_calls=1, max_tool_attempts=2))

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        async with capture_task_usage() as usage:
            result = await RecoveringManager(llm=SimpleNamespace()).run_tool(
                tool,
                {},
                max_retries=2,
                attempt_recovery=True,
            )

    assert result.success is True
    assert ledger.snapshot()["tool_calls"] == 1
    assert ledger.snapshot()["tool_attempts"] == 2
    assert ledger.snapshot()["retries"] == 1
    assert usage.snapshot()["tool_calls"] == 1
    assert usage.snapshot()["tool_attempts"] == 2


@pytest.mark.asyncio
async def test_tool_budget_control_error_is_not_downgraded_to_tool_failure():
    calls = 0

    async def run(**_kwargs):
        nonlocal calls
        calls += 1
        return "done"

    tool = SimpleNamespace(
        name="limited tool",
        description="test",
        run=run,
        validate_parameters=lambda value: value,
    )
    ledger = BudgetLedger(TaskBudget(max_tool_calls=1, max_tool_attempts=1))
    manager = ResilientToolManager()

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        await manager.run_tool(tool, {}, max_retries=1, attempt_recovery=False)
        with pytest.raises(BudgetExceeded, match="tool_calls"):
            await manager.run_tool(tool, {}, max_retries=1, attempt_recovery=False)

    assert calls == 1


@pytest.mark.asyncio
async def test_parallel_llm_reservations_stop_before_second_provider_call(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    provider_calls = 0

    async def generate(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        entered.set()
        await release.wait()
        return response(prompt_tokens=2, completion_tokens=2)

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    # Each call reserves more than half this cap before entering the provider.
    ledger = BudgetLedger(TaskBudget(max_tokens=35))
    llm = fake_llm(max_tokens=20)

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        first = asyncio.create_task(llm([{"role": "user", "content": "one"}]))
        await entered.wait()
        with pytest.raises(BudgetExceeded, match="tokens"):
            await llm([{"role": "user", "content": "two"}])
        release.set()
        await first

    assert provider_calls == 1


@pytest.mark.asyncio
async def test_total_token_budget_clamps_a_cloned_call_below_model_default(monkeypatch):
    seen_max_tokens = []

    async def generate(llm, *_args, **_kwargs):
        seen_max_tokens.append(llm.max_tokens)
        return response(prompt_tokens=20, completion_tokens=30)

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    ledger = BudgetLedger(TaskBudget(max_tokens=2_000, max_llm_calls=1))
    llm = fake_llm(max_tokens=8_192)

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        result = await llm([{"role": "user", "content": "bounded request"}])

    assert result.llm_response == "ok"
    assert 0 < seen_max_tokens[0] < 2_000
    assert llm.max_tokens == 8_192
    assert ledger.snapshot()["total_tokens"] == 50


@pytest.mark.asyncio
async def test_real_provider_retry_consumes_call_and_retry_budgets(monkeypatch):
    class Transient(Exception):
        pass

    provider_calls = 0
    provider_max_tokens = []

    async def create(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        provider_max_tokens.append(kwargs["max_tokens"])
        if provider_calls == 1:
            raise Transient("retry")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content="ok",
                tool_calls=[],
                reasoning_content=None,
            ))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("cognitrix.providers.base.openai.APITimeoutError", Transient)
    monkeypatch.setattr("cognitrix.providers.base.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("cognitrix.providers.base._get_or_create_client", lambda *_a, **_k: client)
    prompt = [{"role": "user", "content": "hello"}]
    prompt_estimate = estimate_prompt_tokens(prompt)
    token_budget = 2 * prompt_estimate + 23
    ledger = BudgetLedger(
        TaskBudget(
            max_llm_calls=2,
            max_retries=1,
            max_tokens=token_budget,
            max_cost_usd=Decimal("1"),
        ),
        pricing={
            "test/model": {
                "prompt_per_million": "1",
                "completion_per_million": "10",
            },
        },
    )
    limiter = RecordingLimiter()

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=limiter,
    ):
        result = await fake_llm(max_tokens=20)(prompt)

    assert result.llm_response == "ok"
    assert provider_calls == 2
    assert provider_max_tokens == [20, 3]
    assert limiter.events == ["entered", "released"]
    usage = ledger.snapshot()
    assert usage["llm_calls"] == 2
    assert usage["retries"] == 1
    assert usage["prompt_tokens"] == prompt_estimate + 4
    assert usage["completion_tokens"] == 20 + 2
    assert usage["total_tokens"] == prompt_estimate + 26
    assert Decimal(usage["cost_usd"]) == (
        Decimal(prompt_estimate + 4) + Decimal(20 + 2) * Decimal(10)
    ) / Decimal(1_000_000)
    assert usage["reserved_tokens"] == 0
    assert Decimal(usage["reserved_cost_usd"]) == Decimal("0")


@pytest.mark.asyncio
async def test_missing_provider_usage_retains_prompt_output_cost_split(monkeypatch):
    async def generate(*_args, **_kwargs):
        result = response()
        result.usage = {}
        return result

    monkeypatch.setattr(LLMManager, "generate_response", generate)
    prompt = [{"role": "user", "content": "price this conservatively"}]
    prompt_estimate = estimate_prompt_tokens(prompt)
    ledger = BudgetLedger(
        TaskBudget(max_tokens=100, max_cost_usd=Decimal("1")),
        pricing={
            "test/model": {
                "prompt_per_million": "1",
                "completion_per_million": "25",
            },
        },
    )

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        async with capture_task_usage() as captured:
            await fake_llm(max_tokens=9)(prompt)

    usage = ledger.snapshot()
    expected_cost = (
        Decimal(prompt_estimate) + Decimal(9) * Decimal(25)
    ) / Decimal(1_000_000)
    assert usage["prompt_tokens"] == prompt_estimate
    assert usage["completion_tokens"] == 9
    assert usage["total_tokens"] == prompt_estimate + 9
    assert Decimal(usage["cost_usd"]) == expected_cost
    assert captured.prompt_tokens == prompt_estimate
    assert captured.completion_tokens == 9
    assert captured.cost_usd == expected_cost


@pytest.mark.asyncio
async def test_provider_retry_budget_control_error_is_never_downgraded(monkeypatch):
    class Transient(Exception):
        pass

    provider_calls = 0

    async def create(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise Transient("retry")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("cognitrix.providers.base.openai.APITimeoutError", Transient)
    monkeypatch.setattr("cognitrix.providers.base.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("cognitrix.providers.base._get_or_create_client", lambda *_a, **_k: client)
    ledger = BudgetLedger(TaskBudget(max_llm_calls=3, max_retries=0, max_tokens=100))

    async with task_accounting_scope(
        ledger,
        actor_key="system",
        limiter=RecordingLimiter(),
    ):
        with pytest.raises(BudgetExceeded, match="retries"):
            await fake_llm(max_tokens=20)([{"role": "user", "content": "hello"}])

    assert provider_calls == 1


@pytest.mark.asyncio
async def test_per_call_response_format_reaches_provider_completion(monkeypatch):
    captured = []

    async def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"ok":true}',
                tool_calls=[],
                reasoning_content=None,
            ))],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=2),
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("cognitrix.providers.base._get_or_create_client", lambda *_a, **_k: client)

    await fake_llm()(
        [{"role": "user", "content": "json"}],
        response_format={"type": "json_object"},
    )

    assert captured[0]["response_format"] == {"type": "json_object"}
