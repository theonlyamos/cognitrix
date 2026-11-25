"""Request Access: agents ask the person for a tool or connected app (cognitrix/tools/access.py)."""

import asyncio

import pytest

import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
import cognitrix.tools  # noqa: F401  (registers the tools)
from cognitrix.mcp.server_manager import MCPServerConfig, MCPTransportType
from cognitrix.models import Agent
from cognitrix.providers.base import LLM
from cognitrix.safety.approval_gate import (
    _WEB_PENDING,
    ApprovalGate,
    ApprovalResult,
    ToolCall,
    resolve_web_approval,
    web_turn_ctx,
)
from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskAssessment, RiskLevel
from cognitrix.tools import access
from cognitrix.tools.base import ToolManager


def _agent(tool_names=(), **kw):
    llm = LLM(provider="openai", base_url="http://x", api_key="k", model="m")
    tools = [ToolManager.get_by_name(n) for n in ("request_access", *tool_names)]
    return Agent(name="A", llm=llm, system_prompt="sys", tools=tools, owner_id="u1", **kw)


class _FakeGate:
    def __init__(self, answer):
        self.calls = []
        self.answer = answer

    async def check_approval(self, tool_call, risk, interface="cli", timeout=300.0, scope=None, person_only=False):
        self.calls.append((tool_call, risk))
        return self.answer


@pytest.fixture
def gate(monkeypatch):
    from cognitrix.agents import base as agent_base

    def use(answer):
        fake = _FakeGate(answer)
        monkeypatch.setattr(agent_base.AgentManager, "approval_gate", fake)
        return fake
    return use


@pytest.fixture
def person():
    token = web_turn_ctx.set({"user_key": "u1", "session_id": "s1"})
    yield
    web_turn_ctx.reset(token)


async def _ask(agent, **args):
    res = await agent.call_tools(
        [{"name": "Request Access", "arguments": args, "tool_call_id": "1"}], interface="web")
    return res["result"][0]


def _names(agent):
    return [t.name for t in agent.tools]


# ── asking ──────────────────────────────────────────────────────────────


def test_it_always_asks_a_person():
    risk = DestructiveOpDetector().analyze("Request Access", {"name": "Bash", "reason": "run the tests"})
    assert risk.risk_level == RiskLevel.MEDIUM
    assert risk.categories == ["access_request"]
    assert "Bash" in risk.details and "run the tests" in risk.details


async def test_allowed_in_this_chat_the_agent_can_use_it_right_away(gate, person):
    fake = gate(ApprovalResult(approved=True, grant="chat"))
    agent = _agent()
    saved = []
    token = access.set_grant_sink(lambda kind, name: saved.append((kind, name)))
    try:
        result = await _ask(agent, name="web fetch", reason="read the docs page")
    finally:
        access.reset_grant_sink(token)
    assert result["success"] is True, result
    assert "Webfetch" in _names(agent)
    assert saved == [("tool", "Webfetch")]
    tool_call, risk = fake.calls[0]
    assert "access_request" in risk.categories
    assert tool_call.access["label"] == "Webfetch" and tool_call.access["can_add"] is True


async def test_nothing_is_granted_without_a_persons_answer(gate, person):
    # Auto-approval (no grant from a person) gives nothing.
    gate(ApprovalResult(approved=True, auto=True))
    agent = _agent()
    result = await _ask(agent, name="Bash", reason="x")
    assert result["success"] is False
    assert "Bash" not in _names(agent)


async def test_a_denial_grants_nothing(gate, person):
    gate(ApprovalResult(approved=False))
    agent = _agent()
    result = await _ask(agent, name="Bash", reason="x")
    assert result["success"] is False and "denied" in result["data"]
    assert "Bash" not in _names(agent)


async def test_even_an_always_allow_agent_asks(gate, person):
    fake = gate(ApprovalResult(approved=False))
    agent = _agent(permission_level="allow", tool_permissions={"Request Access": "allow"})
    await _ask(agent, name="Bash", reason="x")
    assert len(fake.calls) == 1


@pytest.mark.parametrize("args, expect", [
    ({"name": "Read", "reason": "x"}, "already have"),
    ({"name": "Frobnicate Widgets", "reason": "x"}, "no tool called"),
    ({"name": "Pay", "reason": "x"}, "can't be asked for"),
    ({"name": "", "reason": "x"}, "which tool"),
])
async def test_nothing_to_ask_answers_without_a_card(gate, person, args, expect):
    fake = gate(ApprovalResult(approved=True, grant="chat"))
    result = await _ask(_agent(("read",)), **args)
    assert expect in result["data"].lower()
    assert fake.calls == []


async def test_a_tool_set_to_never_is_not_asked_for(gate, person):
    fake = gate(ApprovalResult(approved=True, grant="chat"))
    result = await _ask(_agent(tool_permissions={"Bash": "deny"}), name="Bash", reason="x")
    assert "never" in result["data"]
    assert fake.calls == []


def test_unknown_names_get_close_matches():
    assert "Webfetch" in access.suggest_tools("web fetch")
    assert "Grep" in access.suggest_tools("grepp")


async def test_add_to_agent_saves_it_on_the_agent(gate, person, monkeypatch):
    gate(ApprovalResult(approved=True, grant="agent"))
    agent = _agent()
    stored = _agent()
    saved = []

    async def fake_get(agent_id):
        return stored

    async def fake_save(self):
        saved.append([t.name for t in self.tools])

    monkeypatch.setattr(Agent, "get", staticmethod(fake_get))
    monkeypatch.setattr(Agent, "save", fake_save)
    result = await _ask(agent, name="Bash", reason="run tests")
    assert result["success"] is True
    assert "from now on" in result["data"]
    assert saved and "Bash" in saved[0]
    assert "Bash" in _names(agent)


async def test_only_the_owner_can_add_to_the_agent(gate, monkeypatch):
    gate(ApprovalResult(approved=True, grant="agent"))
    token = web_turn_ctx.set({"user_key": "someone-else", "session_id": "s1"})
    try:
        agent = _agent()

        async def no_save(*a, **k):
            raise AssertionError("saved on someone else's agent")

        monkeypatch.setattr(Agent, "get", staticmethod(no_save))
        result = await _ask(agent, name="Bash", reason="x")
    finally:
        web_turn_ctx.reset(token)
    assert result["success"] is True and "in this chat" in result["data"]


# ── connected apps ─────────────────────────────────────────────────────


def _gmail(status="connected"):
    return MCPServerConfig(name="gmail", transport=MCPTransportType.HTTP, url="https://x", id="srv-1",
                           owner_id="u1", auth_type="oauth", catalog_id="gmail", status=status)


@pytest.fixture
def servers(monkeypatch):
    from cognitrix.mcp import registry

    current = []

    async def visible(owner_key):
        return list(current)

    async def resolve(ref, owner_key):
        return next((c for c in current if ref in (c.id, c.name)), None)

    monkeypatch.setattr(registry, "visible_servers", visible)
    monkeypatch.setattr(registry, "resolve", resolve)
    return current


async def test_a_connected_app_is_given_with_the_tools_to_use_it(gate, person, servers):
    servers.append(_gmail())
    fake = gate(ApprovalResult(approved=True, grant="chat"))
    agent = _agent()
    result = await _ask(agent, kind="app", name="Gmail", reason="find the invoice")
    assert result["success"] is True, result
    assert agent.mcp_servers == ["srv-1"]
    assert {"List Mcp Tools", "Run Mcp Tool"} <= set(_names(agent))
    card = fake.calls[0][0].access
    assert card["kind"] == "app" and card["app"]["connected"] is True


async def test_an_app_that_is_not_connected_offers_to_connect(gate, person, servers, monkeypatch):
    from cognitrix.mcp import catalog

    monkeypatch.setattr(catalog, "available", lambda app: True)
    fake = gate(ApprovalResult(approved=True, grant="chat"))
    agent = _agent()
    result = await _ask(agent, kind="app", name="gmail", reason="x")
    card = fake.calls[0][0].access
    assert card["app"] == {"id": "gmail", "connected": False, "can_connect": True, "setup": card["app"]["setup"]}
    # Allowed without connecting: nothing to give yet.
    assert result["success"] is False and "isn't connected" in result["data"]
    assert agent.mcp_servers == []


async def test_an_app_asked_for_as_a_tool_is_still_found(gate, person, servers):
    servers.append(_gmail())
    fake = gate(ApprovalResult(approved=False))
    await _ask(_agent(), name="Gmail", reason="x")
    assert fake.calls[0][0].access["kind"] == "app"


async def test_unknown_apps_answer_without_a_card(gate, person, servers):
    fake = gate(ApprovalResult(approved=True, grant="chat"))
    result = await _ask(_agent(), kind="app", name="Fancy CRM 9000", reason="x")
    assert "isn't a connected app" in result["data"]
    assert fake.calls == []


# ── the chat keeps what was allowed ───────────────────────────────────


def test_chat_grants_come_back_on_every_turn_without_changing_the_agent():
    agent = _agent()
    agent.tools = [t for t in agent.tools if t.name != "Request Access"]
    turn = access.chat_grants(agent, {"tools": ["Bash"], "apps": ["srv-1"]}, request_tool=True)
    assert {"Request Access", "Bash", "List Mcp Tools", "Run Mcp Tool"} <= set(_names(turn))
    assert turn.mcp_servers == ["srv-1"]
    assert _names(agent) == [] and agent.mcp_servers == []
    # Off on the agent: no Request Access.
    assert "Request Access" not in _names(access.chat_grants(agent, None, request_tool=False))


def test_merge_grant():
    granted = access.merge_grant(None, "tool", "Bash")
    granted = access.merge_grant(granted, "tool", "Bash")
    granted = access.merge_grant(granted, "app", "srv-1")
    assert granted == {"tools": ["Bash"], "apps": ["srv-1"]}


# ── the approval gate ──────────────────────────────────────────────────


def _access_risk():
    return RiskAssessment(RiskLevel.MEDIUM, ["access_request"], "Asks for the tool Bash")


def test_bypass_mode_still_asks_and_the_card_carries_the_choices():
    async def run():
        gate = ApprovalGate()
        emitted = []

        async def emit(payload):
            emitted.append(payload)
            if payload["type"] == "approval_request":
                resolve_web_approval(payload["request_id"], approved=True, user_key="u1", grant="agent")

        token = web_turn_ctx.set({"emit": emit, "session_id": "s1", "bypass": True, "user_key": "u1"})
        try:
            call = ToolCall("Request Access", {"name": "Bash"},
                            access={"kind": "tool", "name": "Bash", "label": "Bash", "can_add": False})
            return await gate.check_approval(call, _access_risk(), interface="web", timeout=5, scope="a1"), emitted
        finally:
            web_turn_ctx.reset(token)

    result, emitted = asyncio.run(run())
    assert emitted[0]["access"]["label"] == "Bash"
    assert emitted[0]["always_ask"] is True
    # "Add to agent" isn't offered to this person, so it's this chat only.
    assert result.approved and result.grant == "chat"


def test_an_approve_without_a_choice_allows_it_in_the_chat():
    async def run():
        fut = asyncio.get_running_loop().create_future()
        _WEB_PENDING["rid-access"] = {"future": fut, "user_key": "u1", "access": {"can_add": True}}
        try:
            assert resolve_web_approval("rid-access", approved=True, user_key="u1")
            return fut.result()
        finally:
            _WEB_PENDING.pop("rid-access", None)

    assert asyncio.run(run()).grant == "chat"


def test_the_reviewer_never_answers_it():
    from cognitrix.safety import action_reviewer

    assert action_reviewer._needs_person(_access_risk()) is True


def test_auto_approve_setting_does_not_grant(monkeypatch):
    monkeypatch.setenv("COGNITRIX_AUTO_APPROVE", "1")
    result = asyncio.run(ApprovalGate().check_approval(
        ToolCall("Request Access", {}), _access_risk(), interface="web", timeout=1))
    assert result.approved and result.grant is None


# ── stored fields ──────────────────────────────────────────────────────


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / "access.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


async def test_grants_and_the_switch_are_saved(db):
    from cognitrix.sessions.base import Session

    agent = _agent(can_request_access=False)
    await agent.save()
    assert (await Agent.find_one({"id": agent.id})).can_request_access is False
    session = Session(agent_id=str(agent.id), granted_access={"tools": ["Bash"], "apps": []})
    await session.save()
    assert (await Session.find_one({"id": session.id})).granted_access == {"tools": ["Bash"], "apps": []}


# ── a whole turn ───────────────────────────────────────────────────────


async def test_a_granted_tool_is_offered_and_used_in_the_same_turn(gate, monkeypatch):
    from cognitrix.sessions.base import Session
    from cognitrix.tools.resilient_tool_wrapper import ResilientToolManager, ToolResult
    from cognitrix.utils.llm_response import LLMResponse

    gate(ApprovalResult(approved=True, grant="chat"))
    llm = LLM(provider="openai", base_url="http://x", api_key="k", model="m")
    agent = Agent(name="A", llm=llm, system_prompt="sys", tools=[ToolManager.get_by_name("read")], owner_id="u1")
    session = Session(agent_id="access-turn")
    offered = []
    ran = []

    async def fake_generate(llm, prompt, stream=False, tools=None, **kw):
        offered.append(sorted((t.get("function") or t).get("name", "") for t in tools or []))
        response = LLMResponse()
        step = len(offered)
        if step == 1:
            response.tool_calls = [{"name": "Request Access", "tool_call_id": "a1",
                                    "arguments": {"name": "Bash", "reason": "run the tests"}}]
        elif step == 2:
            response.tool_calls = [{"name": "Bash", "tool_call_id": "b1", "arguments": {"command": "ls"}}]
        else:
            response.add_chunk("done")
        return response

    real_run = ResilientToolManager.run_tool

    async def run_tool(self, tool, params, **kw):
        if tool.name == "Request Access":
            return await real_run(self, tool, params, **kw)
        ran.append(tool.name)
        return ToolResult(success=True, data="ok")

    async def no_save(self):
        return None

    monkeypatch.setattr("cognitrix.providers.base.LLMManager.generate_response", staticmethod(fake_generate))
    monkeypatch.setattr(ResilientToolManager, "run_tool", run_tool)
    monkeypatch.setattr(Session, "save", no_save)

    async def output(*a, **k):
        return None

    await session("run the tests", agent, "task", False, output)

    assert any("request" in n.lower() and "access" in n.lower() for n in offered[0])
    assert not any(n.lower() == "bash" for n in offered[0])
    assert any(n.lower() == "bash" for n in offered[1])
    assert ran == ["Bash"]
    assert session.granted_access == {"tools": ["Bash"], "apps": []}
    # The stored agent is unchanged: only this chat has it.
    assert [t.name for t in agent.tools] == ["Read"]
