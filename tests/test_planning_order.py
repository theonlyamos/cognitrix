"""C5: the task handler executes plan steps in dependency order.

The handler now flattens StructuredPlanner.get_execution_order(plan) to decide
run order. This locks the contract it relies on: a step never runs before the
steps it depends on (diamond: 1 -> {2,3} -> 4).
"""

import pytest

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


def test_topological_fallback_respects_deps():
    from cognitrix.tasks.handler import _topological_fallback
    # Diamond: 1 -> {2,3} -> 4, deliberately out of order in the plan list.
    steps = [
        {"step_number": 4, "dependencies": [2, 3]},
        {"step_number": 2, "dependencies": [1]},
        {"step_number": 3, "dependencies": [1]},
        {"step_number": 1, "dependencies": []},
    ]
    order = _topological_fallback(steps)
    assert order.index(1) < order.index(2)
    assert order.index(1) < order.index(3)
    assert order.index(2) < order.index(4)
    assert order.index(3) < order.index(4)


def test_topological_fallback_tolerates_cycle():
    from cognitrix.tasks.handler import _topological_fallback
    # 1<->2 cycle plus an independent 3; must return all steps, not hang.
    steps = [
        {"step_number": 1, "dependencies": [2]},
        {"step_number": 2, "dependencies": [1]},
        {"step_number": 3, "dependencies": []},
    ]
    order = _topological_fallback(steps)
    assert sorted(order) == [1, 2, 3]
    assert order[0] == 3  # the resolvable step comes first


def test_step_summary_fallback_for_tool_heavy_step():
    import types

    from cognitrix.tasks.handler import _summarize_recent_activity
    # No final assistant text -> summarize the tools it invoked.
    s = types.SimpleNamespace(chat=[
        {"role": "assistant", "type": "tool_calls", "tool_calls": [{"name": "Write"}, {"name": "Write"}]},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "type": "tool_calls", "tool_calls": [{"name": "Bash"}]},
        {"role": "system", "type": "turn_timing", "content": "Took 3s"},
    ])
    out = _summarize_recent_activity(s)
    assert "3 tool call" in out and "Write (x2)" in out and "Bash" in out


def test_step_summary_prefers_final_text():
    import types

    from cognitrix.tasks.handler import _summarize_recent_activity
    s = types.SimpleNamespace(chat=[
        {"role": "assistant", "type": "tool_calls", "tool_calls": [{"name": "Read"}]},
        {"role": "assistant", "type": "text", "content": "Done: created 4 files."},
        {"role": "system", "type": "turn_timing", "content": "x"},
    ])
    assert _summarize_recent_activity(s) == "Done: created 4 files."


def test_referenced_paths_ignores_version_numbers():
    from cognitrix.tasks.handler import _referenced_paths
    assert _referenced_paths("version 1.0.0 and build 2.3") == []
    assert set(_referenced_paths("create REPORT.md and src/main.py")) == {"REPORT.md", "src/main.py"}


@pytest.mark.asyncio
async def test_verify_step_checks_real_file(tmp_path, monkeypatch):
    from cognitrix.config import settings
    from cognitrix.tasks.handler import verify_step
    monkeypatch.setattr(settings, "tools_root", str(tmp_path), raising=False)

    step = {"verification_criteria": "A file REPORT.md exists with the summary",
            "expected_output": "", "description": ""}
    # File not created yet -> the previous text-only verifier said NO too, but
    # now it's a real filesystem fact.
    assert await verify_step(step, "I wrote the report") is False
    # Create it in a subdir -> found via basename search -> True.
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "REPORT.md").write_text("done")
    assert await verify_step(step, "I wrote the report") is True


@pytest.mark.asyncio
async def test_verify_step_nonfile_uses_output(tmp_path, monkeypatch):
    from cognitrix.config import settings
    from cognitrix.tasks.handler import verify_step
    monkeypatch.setattr(settings, "tools_root", str(tmp_path), raising=False)
    step = {"verification_criteria": "The tests pass", "expected_output": "", "description": ""}
    assert await verify_step(step, "All 3 tests passed") is True
    assert await verify_step(step, "Error: something broke") is False
    assert await verify_step(step, "") is False
