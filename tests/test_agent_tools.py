"""Tests for the agent-management tools: create_agent (model/temperature
passthrough), list_agents (registration + formatting), and the create_agent
classmethod's optional-temperature semantics (None = provider default)."""

import pytest

from cognitrix.providers.base import LLM
from cognitrix.tools.base import ToolManager


def test_list_agents_tool_is_registered():
    # ToolManager discovers tools from the cognitrix.tools package namespace, so
    # the tool must be exported from tools/__init__.py to be callable by name.
    assert ToolManager.get_by_name('list_agents') is not None
    assert ToolManager.get_by_name('create_agent') is not None


def test_create_agent_tool_exposes_optional_model_and_temperature():
    ca = ToolManager.get_by_name('create_agent')
    schema = ca.to_dict_format()['function']['parameters']
    props, required = schema['properties'], schema.get('required', [])
    assert 'model' in props and 'temperature' in props
    # Optional — the model may omit them (then provider defaults apply).
    assert 'model' not in required and 'temperature' not in required


@pytest.mark.asyncio
async def test_create_agent_classmethod_temperature_is_optional(monkeypatch):
    from cognitrix.agents import Agent

    monkeypatch.setenv('GOOGLE_API_KEY', 'test-google-key')
    provider_default = LLM.load_llm('google').temperature
    a_default = await Agent.create_agent('T0', 'sys', provider='google', ephemeral=True)
    a_none = await Agent.create_agent('TN', 'sys', provider='google', temperature=None, ephemeral=True)
    a_set = await Agent.create_agent('TV', 'sys', provider='google', temperature=1.3, ephemeral=True)

    assert a_default.llm.temperature == 0.0          # explicit default preserved
    assert a_none.llm.temperature == provider_default  # None -> provider default
    assert a_set.llm.temperature == 1.3              # explicit value honored
    # Empty model keeps the provider default model (from load_llm).
    assert a_default.llm.model == LLM.load_llm('google').model


@pytest.mark.asyncio
async def test_create_agent_tool_passes_model_and_temperature(monkeypatch):
    from cognitrix.tools.misc import create_agent

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return None  # error path — we only assert the passthrough

    monkeypatch.setattr('cognitrix.agents.Agent.create_agent', staticmethod(fake_create))

    await create_agent.run(name='X', provider='google', description='d', tools=[], model='m1', temperature=0.9)
    assert captured['model'] == 'm1' and captured['temperature'] == 0.9

    captured.clear()
    await create_agent.run(name='Y', provider='google', description='d', tools=[])
    # Omitted -> '' model / None temperature so the classmethod uses provider defaults.
    assert captured['model'] == '' and captured['temperature'] is None


@pytest.mark.asyncio
async def test_list_agents_tool_formats_agents(monkeypatch):
    from cognitrix.tools.misc import list_agents

    class FakeLLM:
        provider, model = 'google', 'gemini-3.5-flash'

    class FakeAgent:
        def __init__(self, name, sp):
            self.name, self.system_prompt, self.llm = name, sp, FakeLLM()

    async def fake_list(parent_id=None):
        return [FakeAgent('Alpha', 'You are Alpha. ' * 40), FakeAgent('Beta', '')]

    monkeypatch.setattr('cognitrix.agents.Agent.list_agents', staticmethod(fake_list))

    out = (await list_agents.run()).content
    assert '2 agent(s):' in out
    assert '- Alpha [google/gemini-3.5-flash]' in out
    assert '- Beta [google/gemini-3.5-flash]' in out
    assert '…' in out  # long description truncated


@pytest.mark.asyncio
async def test_list_agents_tool_handles_no_agents(monkeypatch):
    from cognitrix.tools.misc import list_agents

    async def fake_list(parent_id=None):
        return []

    monkeypatch.setattr('cognitrix.agents.Agent.list_agents', staticmethod(fake_list))
    assert (await list_agents.run()).content == 'No agents found.'
