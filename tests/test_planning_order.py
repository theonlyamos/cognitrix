"""C5: the task handler executes plan steps in dependency order.

The handler now flattens StructuredPlanner.get_execution_order(plan) to decide
run order. This locks the contract it relies on: a step never runs before the
steps it depends on (diamond: 1 -> {2,3} -> 4).
"""

from cognitrix.planning.structured_planner import Step, StructuredPlanner, TaskPlan


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
