"""Validated continuous DAG scheduling for durable task steps.

The scheduler is intentionally persistence-agnostic. Callers provide one
executor and, optionally, a transition callback that persists lifecycle state.
All graph validation happens before either callback can run.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Awaitable, Callable, Generic, Mapping, Sequence, TypeVar

from cognitrix.errors import ExecutionControlError
from cognitrix.tasks.results import StepResult, UsageSummary


PayloadT = TypeVar("PayloadT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class DagNode(Generic[PayloadT]):
    """One immutable unit of DAG work."""

    node_id: int
    dependencies: tuple[int, ...] = ()
    payload: PayloadT | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))


class DagNodeState(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DagValidationError(ValueError):
    """The supplied graph cannot be executed safely."""


class DagNodeFailed(RuntimeError):
    """A node failed after zero or more other nodes completed."""

    def __init__(
        self,
        node_id: int,
        cause: BaseException,
        completed: Mapping[int, object],
    ) -> None:
        super().__init__(f"DAG node {node_id} failed: {cause}")
        self.node_id = node_id
        self.cause = cause
        self.completed = dict(completed)


class DagExecutionCancelled(RuntimeError):
    """Cooperative cancellation stopped the scheduler and its children."""

    def __init__(self, completed: Mapping[int, object]) -> None:
        super().__init__("DAG execution cancelled")
        self.completed = dict(completed)


class DagPersistenceError(RuntimeError):
    """A lifecycle transition could not be durably persisted."""

    def __init__(
        self,
        node_id: int,
        state: DagNodeState,
        cause: BaseException,
        completed: Mapping[int, object],
    ) -> None:
        super().__init__(
            f"Could not persist DAG node {node_id} as {state.value}: {cause}"
        )
        self.node_id = node_id
        self.state = state
        self.cause = cause
        self.completed = dict(completed)


Execute = Callable[[DagNode[PayloadT]], Awaitable[ResultT]]
Persist = Callable[
    [DagNode[PayloadT], DagNodeState, ResultT | None, BaseException | None],
    Awaitable[None],
]


def _validated_nodes(nodes: Sequence[DagNode[PayloadT]]) -> dict[int, DagNode[PayloadT]]:
    by_id: dict[int, DagNode[PayloadT]] = {}
    for node in nodes:
        if node.node_id in by_id:
            raise DagValidationError(f"duplicate node id {node.node_id}")
        if len(node.dependencies) != len(set(node.dependencies)):
            raise DagValidationError(
                f"duplicate dependency on node {node.node_id}"
            )
        by_id[node.node_id] = node

    known = set(by_id)
    for node in by_id.values():
        if node.node_id in node.dependencies:
            raise DagValidationError(f"node {node.node_id} cannot depend on itself")
        missing = set(node.dependencies) - known
        if missing:
            raise DagValidationError(
                f"missing dependency for node {node.node_id}: {sorted(missing)}"
            )

    indegree = {node_id: len(node.dependencies) for node_id, node in by_id.items()}
    dependents: dict[int, list[int]] = {node_id: [] for node_id in by_id}
    for node in by_id.values():
        for dependency in node.dependencies:
            dependents[dependency].append(node.node_id)

    ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while ready:
        node_id = ready.popleft()
        visited += 1
        for dependent in sorted(dependents[node_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited != len(by_id):
        raise DagValidationError("cycle detected in task graph")
    return by_id


async def run_dag(
    nodes: Sequence[DagNode[PayloadT]],
    execute: Execute[PayloadT, ResultT],
    *,
    max_parallel: int,
    completed: Mapping[int, ResultT] | None = None,
    persist: Persist[PayloadT, ResultT] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[int, ResultT]:
    """Execute validated nodes as soon as their own dependencies are ready.

    Successful results are returned by node id. A pre-completed mapping seeds
    resumed work and suppresses both execution and transition callbacks for
    those nodes.
    """
    if max_parallel <= 0:
        raise DagValidationError("max_parallel must be positive")
    by_id = _validated_nodes(nodes)
    results: dict[int, ResultT] = dict(completed or {})
    unknown_completed = set(results) - set(by_id)
    if unknown_completed:
        raise DagValidationError(
            f"pre-completed nodes are absent from graph: {sorted(unknown_completed)}"
        )

    pending = set(by_id) - set(results)
    completed_ids = set(results)
    active: dict[asyncio.Task[ResultT], DagNode[PayloadT]] = {}
    cancel_waiter = (
        asyncio.create_task(cancel_event.wait(), name="task-dag-cancel-waiter")
        if cancel_event is not None
        else None
    )

    async def notify(
        node: DagNode[PayloadT],
        state: DagNodeState,
        result: ResultT | None = None,
        error: BaseException | None = None,
    ) -> None:
        if persist is None:
            return
        try:
            await persist(node, state, result, error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise DagPersistenceError(
                node.node_id,
                state,
                exc,
                results,
            ) from exc

    async def cleanup_active() -> None:
        remaining = list(active.items())
        for task, _ in remaining:
            if task.done():
                continue
            task.cancel()
        if remaining:
            await asyncio.gather(
                *(task for task, _ in remaining),
                return_exceptions=True,
            )
        if persist is not None:
            for task, node in remaining:
                try:
                    if task.cancelled():
                        await persist(node, DagNodeState.CANCELLED, None, None)
                        continue
                    error = task.exception()
                    if error is not None:
                        await persist(node, DagNodeState.FAILED, None, error)
                        continue
                    result = task.result()
                    await persist(node, DagNodeState.DONE, result, None)
                    results[node.node_id] = result
                except BaseException:
                    # Cleanup must not hide the execution/persistence/caller
                    # exception that caused sibling cancellation.
                    pass
        active.clear()

    try:
        while pending or active:
            if cancel_event is not None and cancel_event.is_set():
                raise DagExecutionCancelled(results)

            ready = sorted(
                node_id
                for node_id in pending
                if set(by_id[node_id].dependencies) <= completed_ids
            )
            for node_id in ready[: max_parallel - len(active)]:
                node = by_id[node_id]
                await notify(node, DagNodeState.RUNNING)
                pending.remove(node_id)
                task = asyncio.create_task(
                    execute(node),
                    name=f"task-dag-node-{node_id}",
                )
                active[task] = node

            if not active:
                # Validation makes this unreachable unless caller-owned state
                # was mutated during execution.
                raise DagValidationError("task graph made no scheduling progress")

            waiters: set[asyncio.Task] = set(active)
            if cancel_waiter is not None:
                waiters.add(cancel_waiter)
            done, _ = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
            cancellation_requested = bool(
                cancel_waiter is not None and cancel_waiter in done
            )

            first_failure: tuple[DagNode[PayloadT], BaseException] | None = None
            finished = sorted(
                (task for task in done if task in active),
                key=lambda task: active[task].node_id,
            )
            for task in finished:
                node = active.pop(task)
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    await notify(node, DagNodeState.CANCELLED)
                    raise DagExecutionCancelled(results)
                except Exception as exc:
                    await notify(node, DagNodeState.FAILED, error=exc)
                    if first_failure is None:
                        first_failure = (node, exc)
                else:
                    await notify(node, DagNodeState.DONE, result=result)
                    results[node.node_id] = result
                    completed_ids.add(node.node_id)

            # Persist every child that was already complete in this scheduler
            # tick before honoring cancellation. Typed results that won the
            # race remain available to resume and final observability.
            if cancellation_requested:
                raise DagExecutionCancelled(results)
            if first_failure is not None:
                node, cause = first_failure
                raise DagNodeFailed(node.node_id, cause, results) from cause

        return {node_id: results[node_id] for node_id in sorted(results)}
    finally:
        await cleanup_active()
        if cancel_waiter is not None:
            cancel_waiter.cancel()
            await asyncio.gather(cancel_waiter, return_exceptions=True)


def _fallback_result(results: Sequence[StepResult]) -> StepResult:
    structured = [result.structured_data for result in results]
    has_structured = any(item is not None for item in structured)
    return StepResult(
        text="\n\n".join(result.text for result in results if result.text),
        artifacts=[artifact for result in results for artifact in result.artifacts],
        structured_data={"step_results": structured} if has_structured else None,
        citations=[citation for result in results for citation in result.citations],
        warnings=[
            *(warning for result in results for warning in result.warnings),
            "Synthesis unavailable; combined step results returned.",
        ],
        usage=UsageSummary(
            prompt_tokens=sum(result.usage.prompt_tokens for result in results),
            completion_tokens=sum(result.usage.completion_tokens for result in results),
            llm_calls=sum(result.usage.llm_calls for result in results),
            tool_calls=sum(result.usage.tool_calls for result in results),
            tool_attempts=sum(result.usage.tool_attempts for result in results),
            duration_seconds=sum(result.usage.duration_seconds for result in results),
            cost_usd=sum(
                (result.usage.cost_usd for result in results),
                Decimal("0"),
            ),
        ),
    )


async def finalize_results(
    results: Sequence[StepResult],
    synthesize: Callable[
        [Sequence[StepResult]],
        Awaitable[StepResult | str | dict],
    ],
) -> StepResult:
    """Finalize typed step results with a deterministic no-provider fallback."""
    normalized = [StepResult.from_stored(result) for result in results]
    if not normalized:
        return StepResult()
    if len(normalized) == 1:
        return normalized[0]

    try:
        synthesized = StepResult.from_stored(await synthesize(normalized))
        if (
            synthesized.text.strip()
            or synthesized.artifacts
            or synthesized.structured_data is not None
            or synthesized.citations
        ):
            return synthesized
    except ExecutionControlError:
        raise
    except Exception:
        pass
    return _fallback_result(normalized)
