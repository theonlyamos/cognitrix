"""C5: the task handler executes plan steps in dependency order.

The handler now flattens StructuredPlanner.get_execution_order(plan) to decide
run order. This locks the contract it relies on: a step never runs before the
steps it depends on (diamond: 1 -> {2,3} -> 4).
"""

from cognitrix.planning.structured_planner import Step, StructuredPlanner, TaskPlan
from cognitrix.tasks.handler import is_multi_step_task


def _step(n, deps):
    return Step(
        step_number=n, title=f"s{n}", description="d", expected_output="o",
        assigned_agent="auto", required_tools=[], dependencies=deps,
        estimated_duration="short", verification_criteria="v",
    )


def _diamond_plan():
    return TaskPlan(
        task_analysis="a", estimated_complexity="complex",
        steps=[_step(1, []), _step(2, [1]), _step(3, [1]), _step(4, [2, 3])],
        parallel_groups=[], fallback_strategy="f",
    )


def test_execution_order_respects_dependencies():
    planner = StructuredPlanner.__new__(StructuredPlanner)  # get_execution_order uses only `plan`
    batches = planner.get_execution_order(_diamond_plan())
    order = [s.step_number for batch in batches for s in batch]
    assert order.index(1) < order.index(2)
    assert order.index(1) < order.index(3)
    assert order.index(2) < order.index(4)
    assert order.index(3) < order.index(4)


class TestMultiStepRouting:
    def test_single_task_is_not_multi_step(self):
        assert not is_multi_step_task("find me a restaurant")
        assert not is_multi_step_task("what's the weather today?")
        assert not is_multi_step_task("research quantum computing")

    def test_genuine_multi_step_is_detected(self):
        assert is_multi_step_task("find hotels and book catering")
        assert is_multi_step_task("plan a trip then research restaurants")
        assert is_multi_step_task("1. gather data\n2. write the report")
