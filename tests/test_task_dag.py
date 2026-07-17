"""Contracts for continuous durable-task DAG execution and finalization."""

import asyncio
import importlib
from decimal import Decimal

import pytest

from cognitrix.tasks.results import StepResult


def _dag_module():
    return importlib.import_module("cognitrix.tasks.dag")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_specs", "message"),
    [
        ([(0, ()), (0, ())], "duplicate node"),
        ([(0, ()), (1, (0, 0))], "duplicate dependency"),
        ([(0, (2,)), (1, ())], "missing dependency"),
        ([(0, (0,))], "depend on itself"),
        ([(0, (1,)), (1, (0,))], "cycle"),
    ],
)
async def test_invalid_graph_is_rejected_before_execution(node_specs, message):
    dag = _dag_module()
    nodes = [dag.DagNode(node_id=node_id, dependencies=deps) for node_id, deps in node_specs]
    executed = []

    async def execute(node):
        executed.append(node.node_id)
        return node.node_id

    with pytest.raises(dag.DagValidationError, match=message):
        await dag.run_dag(nodes, execute, max_parallel=2)

    assert executed == []


@pytest.mark.asyncio
async def test_fast_dependency_releases_before_unrelated_slow_node_finishes():
    dag = _dag_module()
    nodes = [
        dag.DagNode(node_id=0),
        dag.DagNode(node_id=1),
        dag.DagNode(node_id=2, dependencies=(0,)),
    ]
    slow_release = asyncio.Event()
    dependent_started = asyncio.Event()
    slow_finished = False

    async def execute(node):
        nonlocal slow_finished
        if node.node_id == 0:
            await asyncio.sleep(0)
            return "fast"
        if node.node_id == 1:
            await slow_release.wait()
            slow_finished = True
            return "slow"
        assert slow_finished is False
        dependent_started.set()
        return "dependent"

    runner = asyncio.create_task(dag.run_dag(nodes, execute, max_parallel=2))
    await asyncio.wait_for(dependent_started.wait(), timeout=1)
    assert slow_finished is False
    slow_release.set()

    results = await asyncio.wait_for(runner, timeout=1)
    assert results == {0: "fast", 1: "slow", 2: "dependent"}


@pytest.mark.asyncio
async def test_diamond_runs_each_node_once_with_bounded_parallelism():
    dag = _dag_module()
    nodes = [
        dag.DagNode(node_id=0),
        dag.DagNode(node_id=1, dependencies=(0,)),
        dag.DagNode(node_id=2, dependencies=(0,)),
        dag.DagNode(node_id=3, dependencies=(1, 2)),
    ]
    active = 0
    max_active = 0
    starts = []

    async def execute(node):
        nonlocal active, max_active
        starts.append(node.node_id)
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.01)
            return f"result-{node.node_id}"
        finally:
            active -= 1

    results = await dag.run_dag(nodes, execute, max_parallel=2)

    assert starts[0] == 0
    assert starts[-1] == 3
    assert sorted(starts) == [0, 1, 2, 3]
    assert max_active == 2
    assert results == {index: f"result-{index}" for index in range(4)}


@pytest.mark.asyncio
async def test_resume_seeds_precompleted_nodes_without_reexecution():
    dag = _dag_module()
    nodes = [
        dag.DagNode(node_id=0),
        dag.DagNode(node_id=1, dependencies=(0,)),
        dag.DagNode(node_id=2, dependencies=(1,)),
    ]
    seed = StepResult(text="already complete", structured_data={"seed": True})
    executed = []
    transitions = []

    async def execute(node):
        executed.append(node.node_id)
        return StepResult(text=f"step {node.node_id}")

    async def persist(node, state, result, error):
        transitions.append((node.node_id, state.value))

    results = await dag.run_dag(
        nodes,
        execute,
        max_parallel=2,
        completed={0: seed},
        persist=persist,
    )

    assert results[0] is seed
    assert executed == [1, 2]
    assert all(node_id != 0 for node_id, _ in transitions)


@pytest.mark.asyncio
async def test_node_failure_is_fail_fast_and_cleans_running_siblings():
    dag = _dag_module()
    nodes = [
        dag.DagNode(node_id=0),
        dag.DagNode(node_id=1),
        dag.DagNode(node_id=2, dependencies=(0,)),
    ]
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()
    executed = []
    transitions = []

    async def execute(node):
        executed.append(node.node_id)
        if node.node_id == 0:
            await sibling_started.wait()
            raise RuntimeError("step exploded")
        if node.node_id == 1:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cleaned.set()
        raise AssertionError("dependent must not launch after failure")

    async def persist(node, state, result, error):
        transitions.append((node.node_id, state.value, type(error).__name__ if error else None))

    with pytest.raises(dag.DagNodeFailed, match="node 0") as exc_info:
        await asyncio.wait_for(
            dag.run_dag(nodes, execute, max_parallel=2, persist=persist),
            timeout=1,
        )

    assert isinstance(exc_info.value.cause, RuntimeError)
    assert exc_info.value.completed == {}
    assert sibling_cleaned.is_set()
    assert 2 not in executed
    assert (0, "failed", "RuntimeError") in transitions
    assert (1, "cancelled", None) in transitions


@pytest.mark.asyncio
async def test_cooperative_cancellation_stops_and_cleans_all_children():
    dag = _dag_module()
    nodes = [dag.DagNode(node_id=0), dag.DagNode(node_id=1)]
    cancel_event = asyncio.Event()
    both_started = asyncio.Event()
    started = set()
    cleaned = set()

    async def execute(node):
        started.add(node.node_id)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.add(node.node_id)

    runner = asyncio.create_task(
        dag.run_dag(
            nodes,
            execute,
            max_parallel=2,
            cancel_event=cancel_event,
        )
    )
    await asyncio.wait_for(both_started.wait(), timeout=1)
    cancel_event.set()

    with pytest.raises(dag.DagExecutionCancelled):
        await asyncio.wait_for(runner, timeout=1)
    assert cleaned == {0, 1}


@pytest.mark.asyncio
async def test_simultaneous_completion_is_preserved_before_cancellation():
    dag = _dag_module()
    cancel_event = asyncio.Event()
    result = StepResult(text="completed at cancellation boundary")
    transitions = []

    async def execute(node):
        cancel_event.set()
        return result

    async def persist(node, state, value, error):
        transitions.append((node.node_id, state.value, value))

    with pytest.raises(dag.DagExecutionCancelled) as exc_info:
        await dag.run_dag(
            [dag.DagNode(node_id=0)],
            execute,
            max_parallel=1,
            persist=persist,
            cancel_event=cancel_event,
        )

    assert exc_info.value.completed == {0: result}
    assert transitions == [(0, "running", None), (0, "done", result)]


@pytest.mark.asyncio
async def test_persistence_failure_is_terminal_and_cleans_running_children():
    dag = _dag_module()
    nodes = [dag.DagNode(node_id=0), dag.DagNode(node_id=1)]
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()

    async def execute(node):
        if node.node_id == 0:
            await sibling_started.wait()
            return "done"
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_cleaned.set()

    async def persist(node, state, result, error):
        if node.node_id == 0 and state.value == "done":
            raise OSError("database unavailable")

    with pytest.raises(dag.DagPersistenceError, match="node 0") as exc_info:
        await asyncio.wait_for(
            dag.run_dag(nodes, execute, max_parallel=2, persist=persist),
            timeout=1,
        )

    assert exc_info.value.state == dag.DagNodeState.DONE
    assert isinstance(exc_info.value.cause, OSError)
    assert sibling_cleaned.is_set()


@pytest.mark.asyncio
async def test_cancelling_scheduler_task_awaits_child_cleanup():
    dag = _dag_module()
    nodes = [dag.DagNode(node_id=0), dag.DagNode(node_id=1)]
    both_started = asyncio.Event()
    started = set()
    cleaned = set()

    async def execute(node):
        started.add(node.node_id)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.add(node.node_id)

    runner = asyncio.create_task(dag.run_dag(nodes, execute, max_parallel=2))
    await asyncio.wait_for(both_started.wait(), timeout=1)
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await runner
    assert cleaned == {0, 1}


@pytest.mark.asyncio
async def test_single_result_finalization_returns_same_object_without_synthesis():
    dag = _dag_module()
    result = StepResult(
        text="exact result",
        artifacts=[{"id": "artifact-1"}],
        structured_data={"preserve": True},
    )
    calls = 0

    async def synthesize(results):
        nonlocal calls
        calls += 1
        return StepResult(text="must not be used")

    final = await dag.finalize_results([result], synthesize)

    assert final is result
    assert calls == 0


@pytest.mark.asyncio
async def test_multiple_results_use_async_typed_synthesizer_once():
    dag = _dag_module()
    inputs = [StepResult(text="one"), StepResult(text="two")]
    calls = []

    async def synthesize(results):
        calls.append(results)
        return StepResult(text="combined", structured_data={"count": 2})

    final = await dag.finalize_results(inputs, synthesize)

    assert final == StepResult(text="combined", structured_data={"count": 2})
    assert calls == [inputs]


@pytest.mark.asyncio
async def test_synthesis_failure_returns_deterministic_typed_fallback():
    dag = _dag_module()
    inputs = [
        StepResult(
            text="one",
            artifacts=[{"id": "artifact-1"}],
            structured_data={"part": 1},
            citations=[{"url": "https://example.test/one"}],
            warnings=["warning one"],
            usage={"prompt_tokens": 2, "completion_tokens": 3, "cost_usd": "0.10"},
        ),
        StepResult(
            text="two",
            artifacts=[{"id": "artifact-2"}],
            structured_data={"part": 2},
            citations=[{"url": "https://example.test/two"}],
            warnings=["warning two"],
            usage={"prompt_tokens": 5, "completion_tokens": 7, "cost_usd": "0.25"},
        ),
    ]

    async def synthesize(results):
        raise RuntimeError("provider unavailable")

    final = await dag.finalize_results(inputs, synthesize)

    assert final.text == "one\n\ntwo"
    assert [artifact.id for artifact in final.artifacts] == ["artifact-1", "artifact-2"]
    assert final.structured_data == {
        "step_results": [{"part": 1}, {"part": 2}],
    }
    assert [citation.url for citation in final.citations] == [
        "https://example.test/one",
        "https://example.test/two",
    ]
    assert final.warnings == [
        "warning one",
        "warning two",
        "Synthesis unavailable; combined step results returned.",
    ]
    assert final.usage.prompt_tokens == 7
    assert final.usage.completion_tokens == 10
    assert final.usage.cost_usd == Decimal("0.35")


@pytest.mark.asyncio
async def test_synthesis_control_error_is_never_downgraded_to_fallback():
    from cognitrix.tasks.budget import BudgetExceeded

    dag = _dag_module()

    async def synthesize(results):
        raise BudgetExceeded("budget_exceeded: tokens")

    with pytest.raises(BudgetExceeded, match="tokens"):
        await dag.finalize_results(
            [StepResult(text="one"), StepResult(text="two")],
            synthesize,
        )
