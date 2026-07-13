"""Keystone protocol tests for the agent turn loop (C1, C2).

Verifies that a tool-using turn persists an OpenAI-spec-valid message sequence:
an assistant message carrying tool_calls precedes the matching tool-result
messages, and tool results map to the correct tool_call_id.
"""

import json

import pytest

from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM, LLMManager
from cognitrix.tools.resilient_tool_wrapper import ToolResult
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
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")

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
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")

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
async def test_tool_turn_persists_valid_sequence(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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
async def test_web_turn_emits_tool_events(monkeypatch):
    """A web turn surfaces each tool call as started -> completed events so the
    chat UI can show what the agent is running. CLI/task turns must not."""
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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
    assert tool_events == [{
        "type": "tool",
        "status": "error",
        "tool_name": "Malformed tool call",
        "tool_call_id": "malformed-1",
        "result": "Error: malformed tool call (no name)",
    }]


@pytest.mark.asyncio
async def test_unknown_tool_does_not_abort_batch(monkeypatch):
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")

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
    assert "not found" in data[1]
    assert data[2] == "ok:good2"
    assert [r["tool_call_id"] for r in res["result"]] == ["1", "2", "3"]
    assert [r["success"] for r in res["result"]] == [True, False, True]


@pytest.mark.asyncio
async def test_provider_error_not_persisted_as_answer(monkeypatch):
    from cognitrix.sessions.base import Session

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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
    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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

    agent = Agent(name="A", llm=_llm(), system_prompt="sys")
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
