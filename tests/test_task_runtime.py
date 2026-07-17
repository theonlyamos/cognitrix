import json

import pytest
from pydantic import ValidationError

from cognitrix.agents.base import AgentManager
from cognitrix.models import Agent
from cognitrix.models.tool import MCPTool, Tool
from cognitrix.providers.base import LLM
from cognitrix.tasks.runtime import (
    AgentRuntimeSnapshot,
    LLMRuntimeSnapshot,
    MissingRequiredTools,
    build_runtime_snapshot,
    build_task_capability_registry,
    instantiate_runtime,
    strip_legacy_task_boilerplate,
)


def _llm() -> LLM:
    return LLM(
        provider="openai",
        base_url="http://provider.invalid/v1",
        api_key="super-secret",
        model="test-model",
        temperature=0.25,
        max_tokens=321,
        context_window=4096,
    )


def _agent(tools: list[Tool]) -> Agent:
    return Agent(
        name="Builder",
        llm=_llm(),
        tools=tools,
        system_prompt="Keep this user-authored scratchpad advice.\nDo the work.",
    )


def test_snapshot_is_secret_free_and_json_serializable():
    source = _agent([Tool(name="Web Search", description="search", parameters={})])

    snapshot = build_runtime_snapshot(source, required_tools=None)
    payload = snapshot.model_dump(mode="json")

    assert payload["tool_names"] == ["Web Search"]
    assert payload["llm"]["provider"] == "openai"
    assert payload["llm"]["model"] == "test-model"
    encoded = json.dumps(payload)
    assert "super-secret" not in encoded
    assert "api_key" not in encoded
    assert "client" not in encoded


def test_snapshot_rejects_normalized_nested_generation_secret_keys():
    source = _agent([])
    source.llm.extra_body = {
        "thinking_config": {
            "nested": {"client-secret": "must-not-persist"},
        },
    }

    with pytest.raises(ValidationError, match="secret-bearing"):
        build_runtime_snapshot(source, [])


def test_snapshot_rejects_secret_parameters_and_literal_schema_examples():
    secret_parameter = MCPTool(
        name="Remote Search",
        description="search remotely",
        mcp_schema={
            "type": "object",
            "properties": {
                "credentials": {"type": "string"},
            },
        },
    )
    with pytest.raises(ValidationError, match="secret-bearing tool schema"):
        build_runtime_snapshot(_agent([secret_parameter]), None)

    literal_example = MCPTool(
        name="Remote Search",
        description="search remotely",
        mcp_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "example": "private customer data"},
            },
        },
    )
    with pytest.raises(ValidationError, match="literal defaults or examples"):
        build_runtime_snapshot(_agent([literal_example]), None)


def test_custom_provider_profile_resumes_with_live_credentials(monkeypatch):
    source = _agent([])
    source.llm.provider = "custom"
    source.llm.base_url = "https://custom-provider.test/v1"
    source.llm.api_key = "old-secret"
    source.llm.extra_body = {"reasoning_effort": "low"}
    source.llm.response_format = {"type": "json_object"}
    snapshot = build_runtime_snapshot(source, [])

    live = LLM(
        provider="custom",
        base_url="https://default-provider.test/v1",
        api_key="rotated-secret",
        model="live-default",
    )
    monkeypatch.setattr(
        "cognitrix.tasks.runtime.LLM.load_llm",
        staticmethod(lambda _provider: live),
    )

    resumed = instantiate_runtime(snapshot)

    assert resumed.llm.base_url == "https://custom-provider.test/v1"
    assert resumed.llm.api_key == "rotated-secret"
    assert resumed.llm.model == "test-model"
    assert resumed.llm.extra_body == {"reasoning_effort": "low"}
    assert resumed.llm.response_format == {"type": "json_object"}
    assert "old-secret" not in json.dumps(snapshot.model_dump(mode="json"))


def test_required_tools_none_empty_and_exact_allowlist_semantics():
    alpha = Tool(name="Alpha Tool", description="a", parameters={})
    beta = Tool(name="Beta Tool", description="b", parameters={})
    source = _agent([alpha, beta])

    assert build_runtime_snapshot(source, None).tool_names == ("Alpha Tool", "Beta Tool")
    assert build_runtime_snapshot(source, []).tool_names == ()
    assert build_runtime_snapshot(source, ["Beta Tool"]).tool_names == ("Beta Tool",)

    with pytest.raises(MissingRequiredTools, match="beta tool"):
        build_runtime_snapshot(source, ["beta tool"])
    with pytest.raises(MissingRequiredTools, match="Missing Tool"):
        build_runtime_snapshot(source, ["Missing Tool"])


def test_parallel_attempts_get_fresh_runtime_objects(monkeypatch):
    assigned = Tool(name="Echo", description="echo", parameters={})
    snapshot = build_runtime_snapshot(_agent([assigned]), None)

    monkeypatch.setattr(
        "cognitrix.tasks.runtime.LLM.load_llm",
        staticmethod(lambda _provider: _llm()),
    )
    monkeypatch.setattr(
        "cognitrix.tasks.runtime.ToolManager.get_by_name",
        staticmethod(lambda name: assigned if name == "Echo" else None),
    )

    first = instantiate_runtime(snapshot)
    second = instantiate_runtime(snapshot)

    assert first is not second
    assert first.llm is not second.llm
    assert first.tools is not second.tools
    assert first.tools[0] is not second.tools[0]
    assert first.get_context_manager() is not second.get_context_manager()


def test_runtime_snapshot_is_stable_after_source_agent_changes(monkeypatch):
    source = _agent([Tool(name="Echo", description="old", parameters={})])
    snapshot = build_runtime_snapshot(source, None)
    source.name = "Changed"
    source.system_prompt = "Changed prompt"
    source.llm.model = "changed-model"
    source.tools.clear()

    monkeypatch.setattr(
        "cognitrix.tasks.runtime.LLM.load_llm",
        staticmethod(lambda _provider: _llm()),
    )
    monkeypatch.setattr(
        "cognitrix.tasks.runtime.ToolManager.get_by_name",
        staticmethod(lambda name: Tool(name=name, description="live", parameters={})),
    )

    resumed = instantiate_runtime(snapshot)
    assert resumed.name == "Builder"
    assert resumed.system_prompt == "Keep this user-authored scratchpad advice.\nDo the work."
    assert resumed.llm.model == "test-model"
    assert [tool.name for tool in resumed.tools] == ["Echo"]


def test_snapshot_freezes_capability_security_policy_against_live_loosening(monkeypatch):
    assigned = Tool(
        name="Private Action",
        description="do it",
        parameters={},
        user_id="user-1",
        retryable=False,
        max_attempts=1,
        supported_interfaces=["task"],
        approval_mode="always",
    )
    snapshot = build_runtime_snapshot(_agent([assigned]), None)
    assigned.retryable = True
    assigned.max_attempts = 99
    assigned.supported_interfaces = ["web"]
    assigned.approval_mode = "assigned_only"

    monkeypatch.setattr(
        "cognitrix.tasks.runtime.LLM.load_llm",
        staticmethod(lambda _provider: _llm()),
    )
    runtime = instantiate_runtime(
        snapshot,
        tool_resolver=lambda name: assigned if name == assigned.name else None,
    )
    rebound = runtime.tools[0]

    assert rebound.retryable is False
    assert rebound.max_attempts == 1
    assert rebound.approval_mode == "always"
    assert rebound.supported_interfaces == ["__no_supported_interface__"]
    assert rebound.user_id == "user-1"


def test_nested_tool_schemas_are_immutable_but_remain_json_serializable():
    source_schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "safe"]},
        },
        "required": ["mode"],
    }
    source = _agent([
        MCPTool(
            name="Remote Search",
            description="search remotely",
            mcp_schema=source_schema,
        )
    ])

    snapshot = build_runtime_snapshot(source, None)
    schema = snapshot.tool_schemas[0]

    with pytest.raises(TypeError, match="immutable"):
        schema["function"]["parameters"]["properties"]["mode"]["type"] = "integer"
    with pytest.raises(AttributeError):
        schema["function"]["parameters"]["properties"]["mode"]["enum"].append("new")

    source_schema["properties"]["mode"]["enum"].append("live-only")
    payload = snapshot.model_dump(mode="json")
    assert payload["tool_schemas"][0]["function"]["parameters"]["properties"]["mode"]["enum"] == [
        "fast",
        "safe",
    ]
    json.dumps(payload)


def test_snapshot_rejects_missing_or_misaligned_persisted_tool_schemas():
    base = {
        "agent_id": "agent-1",
        "name": "Builder",
        "system_prompt": "Do the work",
        "llm": LLMRuntimeSnapshot(provider="openai", model="m"),
        "tool_names": ("Echo",),
    }

    with pytest.raises(ValidationError, match="one persisted schema"):
        AgentRuntimeSnapshot(**base, tool_schemas=())

    wrong_name = {
        "type": "function",
        "function": {
            "name": "Other",
            "description": "echo",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
    with pytest.raises(ValidationError, match="does not match tool 'Echo'"):
        AgentRuntimeSnapshot(**base, tool_schemas=(wrong_name,))


@pytest.mark.asyncio
async def test_runtime_uses_persisted_schema_and_injected_assigned_tool_without_fallback(
    monkeypatch,
):
    original = Tool(
        name="Echo",
        description="saved description",
        parameters={"message": "str"},
        approval_mode="assigned_only",
    )
    snapshot = build_runtime_snapshot(_agent([original]), None)
    calls = []

    class LiveAssignedTool(MCPTool):
        async def run(self, **kwargs):
            calls.append(kwargs)
            return "resolved"

    live_assigned = LiveAssignedTool(
        name="Echo",
        description="changed live description",
        mcp_schema={
            "type": "object",
            "properties": {"different": {"type": "integer"}},
            "required": ["different"],
        },
        approval_mode="assigned_only",
    )

    monkeypatch.setattr(
        "cognitrix.tasks.runtime.LLM.load_llm",
        staticmethod(lambda _provider: _llm()),
    )
    monkeypatch.setattr(
        "cognitrix.agents.base.ToolManager.get_by_name",
        staticmethod(lambda _name: pytest.fail("global tool fallback must not run")),
    )

    runtime = instantiate_runtime(
        snapshot,
        tool_resolver=lambda name: live_assigned if name == "Echo" else None,
    )
    advertised = runtime.tools[0].to_dict_format()

    assert advertised == snapshot.model_dump(mode="json")["tool_schemas"][0]
    assert advertised != live_assigned.to_dict_format()
    advertised["function"]["description"] = "caller mutation"
    assert runtime.tools[0].to_dict_format()["function"]["description"] == "saved description"

    result = await AgentManager(runtime).call_tools(
        {"name": "Echo", "arguments": {"message": "hello"}, "tool_call_id": "call-1"},
        interface="task",
    )
    assert calls == [{"message": "hello"}]
    assert result["result"][0]["data"] == "resolved"


def test_injected_resolver_failure_does_not_fall_back_to_global_registry(monkeypatch):
    original = Tool(name="Echo", description="echo", parameters={})
    snapshot = build_runtime_snapshot(_agent([original]), None)
    monkeypatch.setattr(
        "cognitrix.tasks.runtime.LLM.load_llm",
        staticmethod(lambda _provider: _llm()),
    )
    monkeypatch.setattr(
        "cognitrix.tasks.runtime.ToolManager.get_by_name",
        staticmethod(lambda name: original),
    )

    with pytest.raises(MissingRequiredTools, match="Echo"):
        instantiate_runtime(snapshot, tool_resolver=lambda _name: None)


def test_only_exact_legacy_scratchpad_and_todo_boilerplate_is_removed():
    prompt = '''Keep this user-authored sentence about a scratchpad.
- Break down each task into a simple, actionable todo list and update it as you work.
- Update your "scratchpad" and "todo" fields as you work.
  "scratchpad": "{your running notes, calculations, or thoughts}",
  "todo": ["{first subtask or next step}", "{second subtask or next step}"],
- If you need to update your plan, edit the "todo" and "scratchpad" fields.
Keep this ending.'''

    cleaned = strip_legacy_task_boilerplate(prompt)

    assert "user-authored sentence about a scratchpad" in cleaned
    assert "Keep this ending." in cleaned
    assert "Break down each task into a simple, actionable todo list" not in cleaned
    assert '"scratchpad": "{your running notes' not in cleaned
    assert '"todo": ["{first subtask' not in cleaned


@pytest.mark.asyncio
async def test_worker_reconnects_allowed_mcp_server_for_db_hydrated_tool(monkeypatch):
    hydrated = Tool(
        name="docs_search",
        description="persisted schema",
        category="mcp_dynamic",
        parameters={"query": "str"},
    )
    agent = _agent([hydrated])
    agent.id = "agent-mcp"
    agent.mcp_servers = ["docs"]

    class FakeClient:
        def __init__(self):
            self.connected = False
            self.connect_calls = []

        def is_connected(self, server):
            return self.connected

        async def connect_to_server(self, config):
            self.connect_calls.append(config)
            self.connected = True
            return True

        async def list_tools(self, server):
            return [
                {
                    "name": "search",
                    "description": "live delegate",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": "admin",
                    "description": "not assigned to this agent",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ]

    client = FakeClient()
    config = type("Config", (), {"enabled": True})()

    async def get_client():
        return client

    monkeypatch.setattr("cognitrix.mcp.client.get_dynamic_client", get_client)
    monkeypatch.setattr(
        "cognitrix.mcp.server_manager.mcp_server_manager.get_server",
        lambda name: config if name == "docs" else None,
    )

    registry = await build_task_capability_registry([agent], actor_user_id=None)
    resolved = registry.resolver_for("agent-mcp")("docs_search")

    assert resolved is not None
    assert resolved.name == "docs_search"
    assert registry.resolver_for("agent-mcp")("docs_admin") is None
    assert client.connect_calls == [config]
