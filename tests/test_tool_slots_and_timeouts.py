"""Agents stuck on "running" tool calls: tool slots are per agent and chat,
a hung tool call is stopped, and tools that wait on other agents hold no slot."""

import asyncio

import pytest

from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM
from cognitrix.tools.resilient_tool_wrapper import ToolResult
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


def _llm():
    return LLM(provider="openai", base_url="http://x", api_key="k", model="m")


def _agent(monkeypatch, tools):
    import cognitrix.agents.base as agent_base

    agent = Agent(name="A", llm=_llm(), system_prompt="sys", tools=tools)
    monkeypatch.setattr(
        agent_base.ToolManager,
        "get_by_name",
        staticmethod(lambda name: next(tool for tool in tools if tool.name == name)),
    )
    return agent


async def _in_chat(agent, calls, session_id):
    token = set_execution_context(ToolExecutionContext(session_id=session_id))
    try:
        return await agent.call_tools(calls)
    finally:
        reset_execution_context(token)


@pytest.mark.asyncio
async def test_a_busy_chat_does_not_take_the_slots_of_another(monkeypatch):
    import cognitrix.agents.base as agent_base

    hang = Tool(name="Hang", description="d", parameters={})
    quick = Tool(name="Quick", description="d", parameters={})
    agent = _agent(monkeypatch, [hang, quick])
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 1)
    release = asyncio.Event()

    async def fake_run_tool(self, tool, params, **kwargs):
        if tool.name == "Hang":
            await release.wait()
        return ToolResult(success=True, data=tool.name)

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    stuck = asyncio.create_task(
        _in_chat(agent, [{"name": "Hang", "arguments": {}, "tool_call_id": "h"}], "chat-1")
    )
    await asyncio.sleep(0.01)
    # Another chat (a swarm worker, a helper agent) still gets to run its tool.
    other = await asyncio.wait_for(
        _in_chat(agent, [{"name": "Quick", "arguments": {}, "tool_call_id": "q"}], "chat-2"), 1
    )
    assert other["result"][0]["data"] == "Quick"
    release.set()
    await stuck


@pytest.mark.asyncio
async def test_a_hung_tool_call_is_stopped_and_frees_its_slot(monkeypatch):
    import cognitrix.agents.base as agent_base

    hang = Tool(name="Hang", description="d", parameters={})
    quick = Tool(name="Quick", description="d", parameters={})
    agent = _agent(monkeypatch, [hang, quick])
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 1)
    monkeypatch.setattr(agent_base, "tool_timeout_seconds", lambda: 0.05)

    async def fake_run_tool(self, tool, params, **kwargs):
        if tool.name == "Hang":
            await asyncio.Event().wait()
        return ToolResult(success=True, data=tool.name)

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    result = await asyncio.wait_for(agent.call_tools([
        {"name": "Hang", "arguments": {}, "tool_call_id": "h"},
        {"name": "Quick", "arguments": {}, "tool_call_id": "q"},
    ]), 2)
    hung, ran = result["result"]
    assert "was still running after" in hung["data"]
    assert hung["outcome"]["status"] != "success"
    assert ran["data"] == "Quick"


def test_tool_timeout_setting(monkeypatch):
    import cognitrix.agents.base as agent_base

    values = {"COGNITRIX_TOOL_TIMEOUT": ""}
    monkeypatch.setattr(agent_base, "app_setting", lambda name, default=None: values.get(name, default))
    assert agent_base.tool_timeout_seconds() == 900
    values["COGNITRIX_TOOL_TIMEOUT"] = "0"
    assert agent_base.tool_timeout_seconds() is None
    values["COGNITRIX_TOOL_TIMEOUT"] = "120"
    assert agent_base.tool_timeout_seconds() == 120


def test_tools_that_wait_on_other_agents_hold_no_slot():
    from cognitrix.tools.board import board
    from cognitrix.tools.misc import call_agent
    from cognitrix.tools.swarm import spawn_workers, swarm

    for tool in (call_agent, spawn_workers, swarm, board):
        assert tool.occupies_execution_slot is False, tool.name


@pytest.mark.asyncio
async def test_no_limit_runs_every_call_at_once(monkeypatch):
    import cognitrix.agents.base as agent_base

    tools = [Tool(name=f"T{i}", description="d", parameters={}) for i in range(12)]
    agent = _agent(monkeypatch, tools)
    monkeypatch.setattr(agent_base, "MAX_CONCURRENT_TOOL_CALLS", 0)
    started = 0
    all_started = asyncio.Event()

    async def fake_run_tool(self, tool, params, **kwargs):
        nonlocal started
        started += 1
        if started == len(tools):
            all_started.set()
        await asyncio.wait_for(all_started.wait(), 1)
        return ToolResult(success=True, data=tool.name)

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    result = await agent.call_tools(
        [{"name": t.name, "arguments": {}, "tool_call_id": t.name} for t in tools]
    )
    assert [r["data"] for r in result["result"]] == [t.name for t in tools]


def test_tool_round_and_script_limits_default_to_none():
    from cognitrix.sessions import base as session_base
    from cognitrix.tools import code_mode

    assert session_base.MAX_TOOL_ROUNDS == 0
    assert code_mode.MAX_CALLS_PER_SCRIPT == 0


@pytest.mark.asyncio
async def test_a_tool_in_a_worker_thread_is_left_to_its_own_limits(monkeypatch):
    """A worker thread can't be stopped, so the slot and path lock stay held
    until it ends instead of the model being told it stopped."""
    import cognitrix.agents.base as agent_base

    slow = Tool(name="Slow Sync", description="d", parameters={}, runs_in_thread=True)
    agent = _agent(monkeypatch, [slow])
    monkeypatch.setattr(agent_base, "tool_timeout_seconds", lambda: 0.01)

    async def fake_run_tool(self, tool, params, **kwargs):
        await asyncio.sleep(0.05)
        return ToolResult(success=True, data="finished")

    monkeypatch.setattr(
        "cognitrix.tools.resilient_tool_wrapper.ResilientToolManager.run_tool", fake_run_tool
    )
    result = await agent.call_tools([{"name": "Slow Sync", "arguments": {}, "tool_call_id": "s"}])
    assert result["result"][0]["data"] == "finished"


def test_sync_tools_are_marked_as_running_in_a_thread():
    from cognitrix.tools.misc import WebFetch, call_agent

    assert WebFetch.runs_in_thread is True
    assert call_agent.runs_in_thread is False
