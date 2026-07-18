"""Keystone protocol tests for the agent turn loop (C1, C2).

Verifies that a tool-using turn persists an OpenAI-spec-valid message sequence:
an assistant message carrying tool_calls precedes the matching tool-result
messages, and tool results map to the correct tool_call_id.
"""

import asyncio
import json

import pytest

from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM, LLMManager
from cognitrix.tools.resilient_tool_wrapper import ToolResult
from cognitrix.tools.utils import ToolCallResult, ToolOutcome
from cognitrix.utils.llm_response import LLMResponse


def _llm():
    return LLM(provider="openai", base_url="http://x", api_key="k", model="m")


def _assert_spec_valid(messages):
    """Every tool message must reference a tool_call_id issued by a prior assistant."""
    issued = set()
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                issued.add(tc["id"])
        if m.get("role") == "tool":
            assert m.get("tool_call_id") in issued, f"orphan tool message: {m}"


def test_format_query_reconstructs_assistant_tool_calls():
    llm = _llm()
    chat = [
        {"role": "system", "type": "text", "content": "sys"},
        {"role": "User", "type": "text", "content": "weather?"},
        {"role": "assistant", "type": "tool_calls", "content": "",
         "tool_calls": [{"name": "Get Weather", "arguments": {"city": "NYC"}, "tool_call_id": "call_1"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        {"role": "assistant", "type": "text", "content": "It is sunny."},
    ]
    out = LLMManager.format_query(llm, chat)
    asst = [m for m in out if m["role"] == "assistant" and m.get("tool_calls")]
    assert len(asst) == 1
    tc = asst[0]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "Get_Weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "NYC"}
    _assert_spec_valid(out)


@pytest.mark.asyncio
async def test_call_tools_maps_results_to_correct_ids(monkeypatch):
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[
        Tool(name="t1", description="d", parameters={}), Tool(name="t2", description="d", parameters={}),
        Tool(name="t3", description="d", parameters={}),
    ])

    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data=f"ran:{tool.name}")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    # Middle call intentionally has no id — results must still map by position.
    tool_calls = [
        {"name": "t1", "arguments": {}, "tool_call_id": "a"},
        {"name": "t2", "arguments": {}, "tool_call_id": None},
        {"name": "t3", "arguments": {}, "tool_call_id": "c"},
    ]
    result = await agent.call_tools(tool_calls)
    assert [r["tool_call_id"] for r in result["result"]] == ["a", None, "c"]
    assert [r["success"] for r in result["result"]] == [True, True, True]
    assert result["result"][0]["data"] == "ran:t1"
    assert result["result"][2]["data"] == "ran:t3"


@pytest.mark.asyncio
async def test_call_tools_preserves_execution_failure_state(monkeypatch):
    tools = [Tool(name=name, description="d", parameters={}) for name in ("Raises", "Exhausted", "Works")]
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=tools)

    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        if tool.name == "Raises":
            raise RuntimeError("tool crashed")
        if tool.name == "Exhausted":
            return ToolResult(success=False, error="still broken", attempts=3)
        return ToolResult(success=True, data="worked")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    result = await agent.call_tools([
        {"name": "Raises", "arguments": {}, "tool_call_id": "raise-1"},
        {"name": "Exhausted", "arguments": {}, "tool_call_id": "retry-1"},
        {"name": "Works", "arguments": {}, "tool_call_id": "ok-1"},
    ])

    assert [item["success"] for item in result["result"]] == [False, False, True]
    assert "tool crashed" in result["result"][0]["data"]
    assert "attempted 3 times" in result["result"][1]["data"]
    assert result["result"][2]["data"] == "worked"


@pytest.mark.asyncio
async def test_call_tools_denies_a_registered_tool_not_assigned_to_agent(monkeypatch):
    """The global registry must not grant a model extra capabilities."""
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    registered = Tool(name="Sensitive Action", description="d", parameters={})
    ran = False

    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: registered),
    )

    async def fake_run_tool(self, tool, params, **kw):
        nonlocal ran
        ran = True
        return ToolResult(success=True, data="should not run")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    result = await agent.call_tools(
        [{"name": "Sensitive Action", "arguments": {}, "tool_call_id": "call_1"}]
    )

    assert not ran
    assert "not assigned" in result["result"][0]["data"].lower()


@pytest.mark.asyncio
async def test_call_tools_normalizes_provider_name_and_runs_registry_implementation(monkeypatch):
    """Persisted Tool rows authorize a capability; live registry tools execute it."""
    assigned = Tool(name="Create Task", description="persisted", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    canonical = Tool(name="Create Task", description="live", parameters={})
    seen = {}

    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: canonical if name.replace('_', ' ').lower() == 'create task' else None),
    )

    async def fake_run_tool(self, tool, params, **kw):
        seen['tool'] = tool
        return ToolResult(success=True, data="created")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    result = await agent.call_tools(
        [{"name": "Create_Task", "arguments": {}, "tool_call_id": "call_1"}]
    )
    assert result["result"][0]["data"] == "created"
    assert seen['tool'] is canonical


@pytest.mark.asyncio
async def test_repeated_tool_calls_are_not_limited(monkeypatch):
    assigned = Tool(name="Generate Image", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name", staticmethod(lambda name: assigned)
    )

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data="image")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    result = await agent.call_tools([
        {"name": "Generate_Image", "arguments": {}, "tool_call_id": "1"},
        {"name": "Generate_Image", "arguments": {}, "tool_call_id": "2"},
    ])
    assert [item['outcome']['status'] for item in result['result']] == ['success', 'success']


@pytest.mark.asyncio
async def test_call_tools_bounds_concurrency_without_dropping_or_reordering_calls(monkeypatch):
    import cognitrix.agents.base as agent_base

    tool_names = [f"Tool {i}" for i in range(6)]
    assigned = [Tool(name=name, description="d", parameters={}) for name in tool_names]
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=assigned)
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: next(tool for tool in assigned if tool.name == name)),
    )
    # The production default is intentionally small, while this override makes
    # the regression deterministic and documents the configuration surface.
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 2, raising=False)

    active = 0
    peak = 0
    completed = []

    async def fake_run_tool(self, tool, params, **kw):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            completed.append(tool.name)
            return ToolResult(success=True, data=f"ran:{tool.name}")
        finally:
            active -= 1

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    calls = [
        {"name": name, "arguments": {}, "tool_call_id": f"call-{i}"}
        for i, name in enumerate(tool_names)
    ]
    result = await agent.call_tools(calls)

    assert peak <= 2
    assert len(completed) == len(calls)
    assert [item["tool_call_id"] for item in result["result"]] == [
        call["tool_call_id"] for call in calls
    ]
    assert [item["data"] for item in result["result"]] == [
        f"ran:{name}" for name in tool_names
    ]


@pytest.mark.asyncio
async def test_tool_concurrency_limit_is_shared_across_batches(monkeypatch):
    import cognitrix.agents.base as agent_base

    assigned = [Tool(name=f"Tool {i}", description="d", parameters={}) for i in range(4)]
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=assigned)
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 2)
    monkeypatch.setattr(
        agent_base.ToolManager,
        "get_by_name",
        staticmethod(lambda name: next(tool for tool in assigned if tool.name == name)),
    )
    active = 0
    peak = 0

    async def fake_run_tool(self, tool, params, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            return ToolResult(success=True, data=tool.name)
        finally:
            active -= 1

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )
    calls = [
        {"name": tool.name, "arguments": {}, "tool_call_id": f"{batch}-{i}"}
        for batch in ("a", "b")
        for i, tool in enumerate(assigned)
    ]

    first, second = await asyncio.gather(
        agent.call_tools(calls[:4]), agent.call_tools(calls[4:])
    )

    assert peak <= 2
    assert len(first["result"]) == 4
    assert len(second["result"]) == 4


@pytest.mark.asyncio
async def test_large_tool_batch_creates_only_one_worker_per_execution_slot(monkeypatch):
    import cognitrix.agents.base as agent_base

    assigned = Tool(name="Slow Tool", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 3)
    monkeypatch.setattr(
        agent_base.ToolManager, "get_by_name", staticmethod(lambda _name: assigned)
    )
    release = asyncio.Event()
    started = 0

    async def fake_run_tool(self, tool, params, **kwargs):
        nonlocal started
        started += 1
        await release.wait()
        return ToolResult(success=True, data=params["index"])

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )
    calls = [
        {
            "name": assigned.name,
            "arguments": {"index": i},
            "tool_call_id": f"call-{i}",
        }
        for i in range(50)
    ]
    loop = asyncio.get_running_loop()
    original_create_task = loop.create_task
    created_tasks = []

    def counting_create_task(coro, *args, **kwargs):
        task = original_create_task(coro, *args, **kwargs)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(loop, "create_task", counting_create_task)
    turn = original_create_task(agent.call_tools(calls))
    for _ in range(100):
        if started == 3:
            break
        await asyncio.sleep(0)

    try:
        assert started == 3
        assert len(created_tasks) <= 3
    finally:
        release.set()
        result = await turn

    assert [item["tool_call_id"] for item in result["result"]] == [
        call["tool_call_id"] for call in calls
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 4), ("", 4), ("invalid", 4), ("0", 4), ("-3", 4), ("9999", 4), ("7", 7)],
)
def test_concurrency_config_uses_bounded_fallback(raw, expected):
    import cognitrix.agents.base as agent_base

    assert agent_base._parse_max_concurrent_tool_calls(raw) == expected


@pytest.mark.asyncio
async def test_call_tools_propagates_a_child_cancelled_error(monkeypatch):
    assigned = Tool(name="Cancel Tool", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda _name: assigned),
    )

    async def fake_run_tool(self, tool, params, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )

    with pytest.raises(asyncio.CancelledError):
        await agent.call_tools([
            {"name": assigned.name, "arguments": {}, "tool_call_id": "cancel-1"}
        ])


@pytest.mark.asyncio
async def test_execution_control_error_cancels_workers_before_propagating(monkeypatch):
    from cognitrix.errors import ExecutionControlError
    import cognitrix.agents.base as agent_base

    sibling = Tool(name="Running Sibling", description="d", parameters={})
    failing = Tool(name="Control Failure", description="d", parameters={})
    queued = Tool(name="Queued Side Effect", description="d", parameters={})
    assigned = [sibling, failing, queued]
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=assigned)
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 2)
    monkeypatch.setattr(
        agent_base.ToolManager,
        "get_by_name",
        staticmethod(lambda name: next(tool for tool in assigned if tool.name == name)),
    )
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    release_sibling = asyncio.Event()
    queued_side_effects = []

    async def fake_run_tool(self, tool, params, **kwargs):
        if tool.name == sibling.name:
            sibling_started.set()
            try:
                await release_sibling.wait()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise
            return ToolResult(success=True, data="sibling finished")
        if tool.name == failing.name:
            await sibling_started.wait()
            raise ExecutionControlError("stop the batch")
        queued_side_effects.append(tool.name)
        return ToolResult(success=True, data="side effect ran")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )
    calls = [
        {"name": tool.name, "arguments": {}, "tool_call_id": f"call-{index}"}
        for index, tool in enumerate(assigned)
    ]
    loop = asyncio.get_running_loop()
    original_create_task = loop.create_task
    worker_tasks = []

    def recording_create_task(coro, *args, **kwargs):
        task = original_create_task(coro, *args, **kwargs)
        worker_tasks.append(task)
        return task

    monkeypatch.setattr(loop, "create_task", recording_create_task)

    try:
        with pytest.raises(ExecutionControlError, match="stop the batch"):
            await agent.call_tools(calls)
    finally:
        # On the broken path this releases the orphaned worker, which then
        # consumes the queued non-idempotent call and makes the regression
        # observable without leaking a task into the rest of the test suite.
        release_sibling.set()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    assert sibling_cancelled.is_set()
    assert queued_side_effects == []


@pytest.mark.asyncio
async def test_cancelled_tool_batch_exposes_completed_results_and_unresolved_calls(monkeypatch):
    import cognitrix.agents.base as agent_base

    fast = Tool(name="Fast Tool", description="d", parameters={})
    slow = Tool(name="Slow Tool", description="d", parameters={})
    assigned = [fast, slow]
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=assigned)
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 2)
    monkeypatch.setattr(
        agent_base.ToolManager,
        "get_by_name",
        staticmethod(lambda name: next(tool for tool in assigned if tool.name == name)),
    )
    slow_started = asyncio.Event()
    fast_finished = asyncio.Event()

    async def fake_run_tool(self, tool, params, **kwargs):
        if tool.name == fast.name:
            fast_finished.set()
            outcome = ToolOutcome.success(
                "fast result",
                artifacts=[{
                    "id": "artifact-1",
                    "mime_type": "image/png",
                    "filename": "fast.png",
                }],
            )
            value = ToolCallResult(
                tool_name=tool.name, content="fast result", outcome=outcome
            )
            return ToolResult(success=True, data=value)
        slow_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )
    calls = [
        {"name": fast.name, "arguments": {}, "tool_call_id": "fast-1"},
        {"name": slow.name, "arguments": {}, "tool_call_id": "slow-1"},
    ]
    batch = asyncio.create_task(agent.call_tools(calls))
    await asyncio.wait_for(fast_finished.wait(), timeout=5)
    await asyncio.wait_for(slow_started.wait(), timeout=5)
    await asyncio.sleep(0)
    batch.cancel()

    with pytest.raises(asyncio.CancelledError) as exc:
        await batch

    completed = exc.value.completed_result
    assert [item["tool_call_id"] for item in completed["result"]] == ["fast-1"]
    assert completed["result"][0]["outcome"]["status"] == "success"
    assert completed["result"][0]["outcome"]["artifacts"][0]["id"] == "artifact-1"
    assert exc.value.unresolved_tool_calls == [calls[1]]


@pytest.mark.asyncio
async def test_same_tool_can_run_across_multiple_llm_tool_rounds(monkeypatch):
    from cognitrix.sessions.base import Session

    assigned = Tool(name="Generate Image", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    session = Session(agent_id="multi-round")
    llm_round = 0
    executed = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        nonlocal llm_round
        llm_round += 1
        response = LLMResponse()
        if llm_round <= 2:
            response.tool_calls = [{
                "name": "Generate Image",
                "arguments": {"round": llm_round},
                "tool_call_id": f"image-{llm_round}",
            }]
        else:
            response.add_chunk("both images generated")
        return response

    async def fake_run_tool(self, tool, params, **kw):
        executed.append(params["round"])
        return ToolResult(success=True, data=f"image:{params['round']}")

    async def fake_save(self):
        return None

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name", staticmethod(lambda name: assigned)
    )
    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    monkeypatch.setattr(Session, "save", fake_save)

    await session("make two", agent, "cli", False, lambda *args, **kwargs: None)

    assert executed == [1, 2]
    assert [
        message["tool_call_id"]
        for message in session.chat
        if message.get("role") == "tool"
    ] == ["image-1", "image-2"]
    assert session.chat[-2]["content"] == "both images generated"


@pytest.mark.asyncio
async def test_task_turn_records_protocol_history_without_persisting_or_compacting(monkeypatch):
    from cognitrix.sessions.base import Session

    assigned = Tool(name="Echo", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    session = Session(agent_id="ephemeral-task")
    calls = 0

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        nonlocal calls
        calls += 1
        response = LLMResponse()
        if calls == 1:
            response.tool_calls = [{
                "name": "Echo",
                "arguments": {},
                "tool_call_id": "echo-1",
            }]
        else:
            response.add_chunk("finished")
        return response

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data="echoed")

    async def forbidden(*args, **kwargs):
        raise AssertionError("ephemeral task history must not touch persistence")

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name", staticmethod(lambda _name: assigned)
    )
    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    monkeypatch.setattr(Session, "save", forbidden)
    monkeypatch.setattr(Session, "_maybe_compact", forbidden)

    async def sink(*args, **kwargs):
        return None

    await session(
        "run",
        agent,
        interface="task",
        stream=False,
        output=sink,
        record_history=True,
        persist_history=False,
        compact_history=False,
    )

    assert [(item.get("role"), item.get("type")) for item in session.chat[:4]] == [
        ("User", "text"),
        ("assistant", "tool_calls"),
        ("tool", None),
        ("assistant", "text"),
    ]


@pytest.mark.asyncio
async def test_task_turn_forwards_provider_artifacts_to_ephemeral_executor(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[])
    session = Session(agent_id="ephemeral-artifacts")
    emitted = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        response = LLMResponse()
        response.add_chunk("generated")
        response.artifacts = [{
            "id": "artifact-1",
            "name": "image.png",
            "mime_type": "image/png",
        }]
        return response

    async def sink(payload, *args, **kwargs):
        emitted.append(payload)

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response",
        staticmethod(fake_generate),
    )

    await session(
        "generate",
        agent,
        interface="task",
        stream=False,
        output=sink,
        record_history=True,
        persist_history=False,
        compact_history=False,
    )

    artifact_events = [item for item in emitted if item.get("artifacts")]
    assert artifact_events == [{
        "type": None,
        "content": "",
        "action": None,
        "artifacts": [{
            "id": "artifact-1",
            "name": "image.png",
            "mime_type": "image/png",
        }],
        "complete": False,
    }]


def test_tool_model_has_no_per_turn_call_limit_metadata():
    tool = Tool(name="Echo", description="d", parameters={})
    assert not hasattr(tool, 'max_calls_per_turn')


@pytest.mark.asyncio
async def test_call_tools_exposes_a_normalized_outcome(monkeypatch):
    assigned = Tool(name="Echo", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data="echoed")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    result = await agent.call_tools(
        [{"name": "Echo", "arguments": {}, "tool_call_id": "call_1"}]
    )

    outcome = result["result"][0]["outcome"]
    assert outcome == {
        "status": "success",
        "text": "echoed",
        "artifacts": [],
        "entities": [],
        "warnings": [],
        "error": None,
    }


@pytest.mark.asyncio
async def test_tool_turn_persists_valid_sequence(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[Tool(name="Echo", description="d", parameters={})])
    session = Session(agent_id="s1")

    # Scripted LLM: first call emits a tool_call, second returns the final answer.
    calls = {"n": 0}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            r = LLMResponse()
            r.tool_calls = [{"name": "Echo", "arguments": {"x": 1}, "tool_call_id": "call_1"}]
            return r
        r = LLMResponse()
        r.add_chunk("final answer")
        return r

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data="echoed")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)

    await session(  # Session.__call__
        "hello", agent, "cli", False, lambda *a, **k: None, None, True
    )

    roles = [(m.get("role"), m.get("type")) for m in session.chat]
    # user -> assistant(tool_calls) -> tool -> assistant(text) -> turn_timing
    assert roles[0] == ("User", "text")
    assert session.chat[1]["role"] == "assistant" and session.chat[1].get("tool_calls")
    assert session.chat[2]["role"] == "tool" and session.chat[2]["tool_call_id"] == "call_1"
    assert session.chat[3]["role"] == "assistant" and session.chat[3]["content"] == "final answer"
    assert not session.chat[3].get("tool_calls")

    # The full message list sent to the provider is spec-valid.
    _assert_spec_valid(LLMManager.format_query(_llm(), [{"role": "system", "content": "s"}] + session.chat))


@pytest.mark.asyncio
async def test_cancelled_turn_persists_stopped_results_for_outstanding_tool_calls(monkeypatch):
    from cognitrix.sessions.base import Session

    assigned = Tool(name="Slow Tool", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    session = Session(agent_id="cancelled-turn")
    tool_started = asyncio.Event()
    saved_histories = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        response = LLMResponse()
        response.tool_calls = [{
            "name": "Slow Tool",
            "arguments": {},
            "tool_call_id": "slow-1",
        }]
        return response

    async def fake_run_tool(self, tool, params, **kw):
        tool_started.set()
        await asyncio.Event().wait()

    async def fake_save(self):
        saved_histories.append([dict(message) for message in self.chat])

    async def sink(payload=None, *args, **kwargs):
        return None

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name", staticmethod(lambda name: assigned)
    )
    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    monkeypatch.setattr(Session, "save", fake_save)

    turn = asyncio.create_task(session("run slowly", agent, "web", False, sink))
    await asyncio.wait_for(tool_started.wait(), timeout=1)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    tool_messages = [message for message in session.chat if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "slow-1"
    assert tool_messages[0]["content"] == "Stopped by user."
    assert tool_messages[0]["outcome"] == {
        "status": "stopped",
        "text": "Stopped by user.",
        "artifacts": [],
        "entities": [],
        "warnings": [],
        "error": None,
    }
    assert saved_histories
    assert saved_histories[-1][-1] == tool_messages[0]


@pytest.mark.asyncio
async def test_cancelled_turn_preserves_completed_sibling_and_stops_only_unresolved(monkeypatch):
    import cognitrix.agents.base as agent_base
    from cognitrix.sessions.base import Session

    fast = Tool(name="Fast Tool", description="d", parameters={})
    slow = Tool(name="Slow Tool", description="d", parameters={})
    assigned = [fast, slow]
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=assigned)
    session = Session(agent_id="partial-cancel")
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 2)
    slow_started = asyncio.Event()
    fast_finished = asyncio.Event()
    saved_histories = []
    events = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        response = LLMResponse()
        response.tool_calls = [
            {"name": slow.name, "arguments": {}, "tool_call_id": "slow-1"},
            {"name": fast.name, "arguments": {}, "tool_call_id": "fast-1"},
        ]
        return response

    async def fake_run_tool(self, tool, params, **kwargs):
        if tool.name == fast.name:
            fast_finished.set()
            outcome = ToolOutcome.success(
                "fast result",
                artifacts=[{
                    "id": "artifact-1",
                    "mime_type": "image/png",
                    "filename": "fast.png",
                }],
            )
            value = ToolCallResult(
                tool_name=tool.name, content="fast result", outcome=outcome
            )
            return ToolResult(success=True, data=value)
        slow_started.set()
        await asyncio.Event().wait()

    async def fake_save(self):
        saved_histories.append([dict(message) for message in self.chat])

    async def sink(payload=None, *args, **kwargs):
        events.append(payload)

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response",
        staticmethod(fake_generate),
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: next(tool for tool in assigned if tool.name == name)),
    )
    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )
    monkeypatch.setattr(Session, "save", fake_save)

    turn = asyncio.create_task(session("run both", agent, "web", False, sink))
    await asyncio.wait_for(fast_finished.wait(), timeout=5)
    await asyncio.wait_for(slow_started.wait(), timeout=5)
    await asyncio.sleep(0)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    tool_messages = [message for message in session.chat if message.get("role") == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["slow-1", "fast-1"]
    assert tool_messages[0]["outcome"]["status"] == "stopped"
    assert tool_messages[1]["outcome"]["status"] == "success"
    assert tool_messages[1]["outcome"]["artifacts"][0]["id"] == "artifact-1"
    assert saved_histories[-1][-2:] == tool_messages
    terminal_events = {
        event["tool_call_id"]: event
        for event in events
        if (
            isinstance(event, dict)
            and event.get("type") == "tool"
            and event.get("status") != "started"
        )
    }
    assert set(terminal_events) == {"fast-1"}
    assert terminal_events["fast-1"]["status"] == "completed"
    assert terminal_events["fast-1"]["result"] == "fast result"
    assert terminal_events["fast-1"]["artifacts"][0]["id"] == "artifact-1"


@pytest.mark.asyncio
async def test_cancellation_during_completed_event_keeps_completed_tool_outcome(monkeypatch):
    from cognitrix.sessions.base import Session

    assigned = Tool(name="Fast Tool", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    session = Session(agent_id="completed-event-cancel")

    async def fake_generate(llm, prompt, stream=False, tools=None, **kwargs):
        response = LLMResponse()
        response.tool_calls = [{
            "name": assigned.name,
            "arguments": {},
            "tool_call_id": "fast-1",
        }]
        return response

    async def fake_run_tool(self, tool, params, **kwargs):
        return ToolResult(success=True, data="finished")

    async def fake_save(self):
        return None

    async def cancelling_sink(payload=None, *args, **kwargs):
        if isinstance(payload, dict) and payload.get("status") == "completed":
            raise asyncio.CancelledError()

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response",
        staticmethod(fake_generate),
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda _name: assigned),
    )
    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool",
        fake_run_tool,
    )
    monkeypatch.setattr(Session, "save", fake_save)

    with pytest.raises(asyncio.CancelledError):
        await session("run", agent, "web", False, cancelling_sink)

    tool_messages = [message for message in session.chat if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "fast-1"
    assert tool_messages[0]["content"] == "finished"
    assert tool_messages[0]["outcome"]["status"] == "success"


@pytest.mark.asyncio
async def test_web_turn_emits_tool_events(monkeypatch):
    """A web turn surfaces each tool call as started -> completed events so the
    chat UI can show what the agent is running. CLI/task turns must not."""
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[Tool(name="Echo", description="d", parameters={})])
    session = Session(agent_id="s5")

    calls = {"n": 0}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            r = LLMResponse()
            r.tool_calls = [{"name": "Echo", "arguments": {"x": 1}, "tool_call_id": "call_1"}]
            return r
        r = LLMResponse()
        r.add_chunk("final answer")
        return r

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data="echoed")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)

    events = []

    async def sink(payload=None, *a, **k):
        events.append(payload)

    await session("hello", agent, "web", False, sink, None, True)

    tool_events = [e for e in events if isinstance(e, dict) and e.get("type") == "tool"]
    started = next(e for e in tool_events if e["status"] == "started")
    completed = next(e for e in tool_events if e["status"] == "completed")
    # started carries name + params; completed carries the result. Paired by id.
    assert started["tool_name"] == "Echo" and started["tool_call_id"] == "call_1"
    assert '"x": 1' in started["params"]
    assert completed["tool_name"] == "Echo" and completed["tool_call_id"] == "call_1"
    assert completed["result"] == "echoed"
    # started must precede completed (chip appears running, then resolves).
    assert tool_events.index(started) < tool_events.index(completed)


@pytest.mark.asyncio
async def test_web_turn_emits_error_for_failed_and_denied_tools(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(
        name="A",
        llm=_llm(),
        system_prompt="sys",
        tools=[
            Tool(name="Works", description="d", parameters={}),
            Tool(name="Broken", description="d", parameters={}),
            Tool(name="bash", description="d", parameters={}),
        ],
    )
    session = Session(agent_id="s6")
    calls = {"n": 0}
    ran_bash = {"value": False}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            response = LLMResponse()
            response.tool_calls = [
                {"name": "Works", "arguments": {}, "tool_call_id": "ok-1"},
                {"name": "Broken", "arguments": {}, "tool_call_id": "fail-1"},
                {"name": "bash", "arguments": {"command": "rm x"}, "tool_call_id": "deny-1"},
            ]
            return response
        response = LLMResponse()
        response.add_chunk("final answer")
        return response

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        if tool.name == "bash":
            ran_bash["value"] = True
            return ToolResult(success=True, data="must not run")
        if tool.name == "Broken":
            return ToolResult(success=False, error="boom", attempts=3)
        return ToolResult(success=True, data="worked")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)
    events = []

    async def sink(payload=None, *a, **k):
        events.append(payload)

    await session("hello", agent, "web", False, sink, None, True)

    tool_events = [event for event in events if isinstance(event, dict) and event.get("type") == "tool"]
    terminal = {
        event["tool_call_id"]: event
        for event in tool_events
        if event["status"] != "started"
    }
    assert terminal["ok-1"]["status"] == "completed"
    assert terminal["ok-1"]["tool_name"] == "Works"
    assert terminal["ok-1"]["result"] == "worked"
    assert terminal["fail-1"]["status"] == "error"
    assert terminal["fail-1"]["tool_name"] == "Broken"
    assert "attempted 3 times" in terminal["fail-1"]["result"]
    assert terminal["deny-1"]["status"] == "error"
    assert terminal["deny-1"]["tool_name"] == "bash"
    assert "denied" in terminal["deny-1"]["result"].lower()
    assert ran_bash["value"] is False


@pytest.mark.asyncio
async def test_web_turn_emits_terminal_error_for_nameless_tool_call(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    session = Session(agent_id="s7")
    calls = {"n": 0}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            response = LLMResponse()
            response.tool_calls = [{
                "arguments": {"value": "x"},
                "tool_call_id": "malformed-1",
            }]
            return response
        response = LLMResponse()
        response.add_chunk("final answer")
        return response

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)
    events = []

    async def sink(payload=None, *args, **kwargs):
        events.append(payload)

    await session("hello", agent, "web", False, sink, None, True)

    tool_events = [
        event
        for event in events
        if isinstance(event, dict) and event.get("type") == "tool"
    ]
    assert len(tool_events) == 1
    event = tool_events[0]
    assert event["type"] == "tool"
    assert event["status"] == "error"
    assert event["tool_name"] == "Malformed tool call"
    assert event["tool_call_id"] == "malformed-1"
    assert event["result"] == "Error: malformed tool call (no name)"
    assert event["outcome"]["error"]["code"] == "malformed_tool_call"
    assert event["artifacts"] == []


@pytest.mark.asyncio
async def test_unknown_tool_does_not_abort_batch(monkeypatch):
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[
        Tool(name="good1", description="d", parameters={}), Tool(name="good2", description="d", parameters={}),
    ])

    def get_by_name(name):
        return None if name == "missing" else Tool(name=name, description="d", parameters={})

    monkeypatch.setattr("cognitrix.agents.base.ToolManager.get_by_name", staticmethod(get_by_name))

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data=f"ok:{tool.name}")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    calls = [
        {"name": "good1", "arguments": {}, "tool_call_id": "1"},
        {"name": "missing", "arguments": {}, "tool_call_id": "2"},
        {"name": "good2", "arguments": {}, "tool_call_id": "3"},
    ]
    res = await agent.call_tools(calls)
    data = [r["data"] for r in res["result"]]
    assert data[0] == "ok:good1"
    assert "not assigned" in data[1]
    assert data[2] == "ok:good2"
    assert [r["tool_call_id"] for r in res["result"]] == ["1", "2", "3"]
    assert [r["success"] for r in res["result"]] == [True, False, True]


@pytest.mark.asyncio
async def test_provider_error_not_persisted_as_answer(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[Tool(name="bash", description="d", parameters={})])
    session = Session(agent_id="s2")

    async def err_generate(llm, prompt, stream=False, tools=None, **kw):
        return LLMResponse(llm_response="Error: boom", error="Error: boom")

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(err_generate)
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)

    # Must terminate (no infinite loop) and not persist the error as an answer.
    await session("hi", agent, "cli", False, lambda *a, **k: None, None, True)
    assert len(session.chat) == 1
    assert session.chat[0]["role"] == "User"


@pytest.mark.asyncio
async def test_risky_tool_denied_over_web(monkeypatch):
    # A risky tool over the web interface must be denied (not executed, and not
    # blocked on server-side input()).
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[Tool(name="bash", description="d", parameters={})])
    ran = {"called": False}

    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        ran["called"] = True
        return ToolResult(success=True, data="ran")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    res = await agent.call_tools(
        [{"name": "bash", "arguments": {"command": "ls"}, "tool_call_id": "1"}],
        interface="web",
    )
    assert "denied" in res["result"][0]["data"].lower()
    assert res["result"][0]["success"] is False
    assert ran["called"] is False


@pytest.mark.asyncio
async def test_malformed_tool_call_does_not_abort_batch(monkeypatch):
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[Tool(name="good", description="d", parameters={})])
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_run_tool(self, tool, params, **kw):
        return ToolResult(success=True, data="ok")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    res = await agent.call_tools([
        {"arguments": {}, "tool_call_id": "1"},          # missing 'name'
        {"name": "good", "arguments": {}, "tool_call_id": "2"},
    ])
    assert "malformed" in res["result"][0]["data"].lower()
    assert res["result"][0]["success"] is False
    assert res["result"][1]["data"] == "ok"
    assert res["result"][1]["success"] is True


@pytest.mark.asyncio
async def test_approval_cache_is_scoped():
    from cognitrix.safety.approval_gate import ApprovalGate, ToolCall
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    gate = ApprovalGate()
    tc = ToolCall(tool_name="bash", params={"command": "ls"})
    risk = RiskAssessment(risk_level=RiskLevel.HIGH, categories=["code_execution"], details="x")

    # A remembered approval for user A must not approve the same op for user B.
    gate.session_cache.add(f"userA:{gate._hash_operation(tc)}")
    ra = await gate.check_approval(tc, risk, interface="cli", scope="userA")
    assert ra.approved and ra.cached
    rb = await gate.check_approval(tc, risk, interface="web", scope="userB")
    assert not rb.approved


@pytest.mark.asyncio
async def test_denied_tool_retry_loop_is_broken(monkeypatch):
    # If every call in a batch is denied and the model re-issues the exact
    # same batch, the turn must stop instead of re-prompting round after round.
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[Tool(name="bash", description="d", parameters={})])
    session = Session(agent_id="s3")
    calls = {"n": 0}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        calls["n"] += 1
        r = LLMResponse()
        r.tool_calls = [{"name": "bash", "arguments": {"command": "rm x"}, "tool_call_id": str(calls["n"])}]
        return r

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="d", parameters={})),
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)

    outputs = []

    async def sink(payload=None, *a, **k):
        outputs.append(payload)

    # bash over web is auto-denied; identical retry must break the loop.
    await session("do it", agent, "web", False, sink, None, True)
    assert calls["n"] == 2, f"expected 2 rounds (deny, identical retry), got {calls['n']}"


@pytest.mark.asyncio
async def test_structured_denied_tool_retry_loop_is_broken(monkeypatch):
    """Structured policy denials must use the same identical-retry breaker."""
    from cognitrix.sessions import base as session_base
    from cognitrix.sessions.base import Session

    assigned = Tool(name="Generate Image", description="d", parameters={})
    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=[assigned])
    session = Session(agent_id="structured-denial")
    calls = {"n": 0}

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        calls["n"] += 1
        response = LLMResponse()
        response.tool_calls = [{
            "name": "Generate Image",
            "arguments": {
                "prompt": "make it coral",
                "source_artifact_id": "input_file_0.png",
            },
            "tool_call_id": str(calls["n"]),
        }]
        return response

    monkeypatch.setattr(
        "cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate)
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name", staticmethod(lambda name: assigned)
    )
    monkeypatch.setattr(session_base, "MAX_TOOL_ROUNDS", 4)

    async def fake_run_tool(self, tool, params, **kw):
        outcome = ToolOutcome.failure(
            "invalid_edit_source",
            "The selected image is the only available edit source for this turn",
            denied=True,
        )
        return ToolResult(
            success=True,
            data=ToolCallResult(tool_name=tool.name, content=outcome.text, outcome=outcome),
        )

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )

    async def fake_save(self):
        return None

    monkeypatch.setattr(Session, "save", fake_save)
    outputs = []

    async def sink(payload=None, *a, **k):
        outputs.append(payload)

    await session("edit it", agent, "web", False, sink, None, True)

    assert calls["n"] == 2, f"expected 2 rounds (deny, identical retry), got {calls['n']}"
    assert any(
        isinstance(item, dict)
        and item.get("content") == "Stopped: the requested operation was denied and will not be retried."
        for item in outputs
    )


@pytest.mark.asyncio
async def test_window_anchors_to_last_user_message():
    # A long tool loop can push the user message out of the sliding window;
    # the window must then anchor at the last user message instead of going
    # empty (an empty prompt is a provider error).
    from cognitrix.sessions.base import Session
    from cognitrix.sessions.context import SlidingWindowContextManager

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    session = Session(agent_id="s4")
    session.chat = [{"role": "User", "type": "text", "content": "do the thing"}]
    for i in range(12):
        session.chat.append({
            "role": "assistant", "type": "tool_calls", "content": "",
            "tool_calls": [{"name": "t", "arguments": {}, "tool_call_id": str(i)}],
        })
        session.chat.append({"role": "tool", "tool_call_id": str(i), "content": "blocked"})

    mgr = SlidingWindowContextManager(max_messages=10)
    prompt = await mgr.build_prompt(agent, session)
    non_system = [m for m in prompt if m.get("role") != "system"]
    assert non_system, "window must never be empty"
    assert str(non_system[0]["role"]).lower() == "user"


@pytest.mark.asyncio
async def test_task_turn_propagates_provider_error_instead_of_returning_error_text(
    monkeypatch,
):
    from cognitrix.errors import ProviderExecutionError
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
    session = Session(agent_id="provider-error")

    async def failed_provider(*_args, **_kwargs):
        return LLMResponse(
            llm_response="transport details must not become a result",
            error="transport failure",
        )

    async def sink(*_args, **_kwargs):
        return None

    monkeypatch.setattr(LLMManager, "generate_response", failed_provider)

    with pytest.raises(ProviderExecutionError, match="provider request failed"):
        await session(
            "do it",
            agent,
            interface="task",
            stream=False,
            output=sink,
            record_history=True,
            persist_history=False,
            compact_history=False,
        )
