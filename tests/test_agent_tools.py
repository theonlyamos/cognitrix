"""Tests for the agent-management tools: create_agent (model/temperature
passthrough), list_agents (registration + formatting), and the create_agent
classmethod's optional-temperature semantics (None = provider default)."""

import pytest

from cognitrix.providers.base import LLM
from cognitrix.tools.base import ToolManager
from cognitrix.tools.utils import (
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)


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


@pytest.mark.asyncio
async def test_list_agents_hides_targets_outside_caller_allowlist(monkeypatch):
    from cognitrix.tools.misc import list_agents

    class FakeLLM:
        provider, model = 'google', 'gemini-test'

    class FakeAgent:
        def __init__(self, agent_id, name):
            self.id = agent_id
            self.name = name
            self.system_prompt = ''
            self.llm = FakeLLM()

    async def fake_list(parent_id=None):
        return [FakeAgent('allowed', 'Allowed'), FakeAgent('secret', 'Secret')]

    monkeypatch.setattr('cognitrix.agents.Agent.list_agents', staticmethod(fake_list))
    token = set_execution_context(ToolExecutionContext(
        user_id='user-1',
        api_key_id='key-1',
        allowed_agents=frozenset({'allowed'}),
    ))
    try:
        out = (await list_agents.run()).content
    finally:
        reset_execution_context(token)

    assert 'Allowed' in out
    assert 'Secret' not in out
    assert '1 agent(s):' in out


@pytest.mark.asyncio
async def test_call_agent_rejects_disallowed_target_without_starting_session(monkeypatch):
    from cognitrix.tools.misc import call_agent

    target = type('Target', (), {'id': 'forbidden', 'name': 'Forbidden'})()

    async def find_one(_conditions):
        return target

    class ForbiddenSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError('a denied delegation must not create a session')

    monkeypatch.setattr('cognitrix.agents.Agent.find_one', staticmethod(find_one))
    monkeypatch.setattr('cognitrix.sessions.base.Session', ForbiddenSession)
    token = set_execution_context(ToolExecutionContext(
        user_id='user-1',
        api_key_id='key-1',
        allowed_agents=frozenset({'allowed'}),
    ))
    try:
        out = (await call_agent.run(name='Forbidden', task='secret task')).content
    finally:
        reset_execution_context(token)

    assert 'not found or is not allowed' in out


@pytest.mark.asyncio
async def test_call_agent_uses_ephemeral_history_and_propagates_authority(monkeypatch):
    from cognitrix.tools.misc import call_agent

    target = type('Target', (), {'id': 'allowed', 'name': 'Allowed'})()
    observed = {}

    async def find_one(_conditions):
        return target

    class EphemeralSession:
        def __init__(self, **kwargs):
            observed['session_init'] = kwargs

        @classmethod
        async def get_by_agent_id(cls, *_args, **_kwargs):
            raise AssertionError('delegation must not reuse persisted agent history')

        async def __call__(self, *args, **kwargs):
            observed['call_args'] = args
            observed['call_kwargs'] = kwargs
            await args[4]({'content': 'delegated answer'})

    monkeypatch.setattr('cognitrix.agents.Agent.find_one', staticmethod(find_one))
    monkeypatch.setattr('cognitrix.sessions.base.Session', EphemeralSession)
    authority = ToolExecutionContext(
        user_id='user-1',
        api_key_id='key-1',
        scopes=frozenset({'chat'}),
        allowed_agents=frozenset({'allowed'}),
        allowed_teams=frozenset({'team-1'}),
    )
    token = set_execution_context(authority)
    try:
        out = (
            await call_agent.run(
                name='Allowed',
                task='do work',
                interface='task',  # untrusted tool argument must not weaken policy
            )
        ).content
    finally:
        reset_execution_context(token)

    assert out == 'delegated answer'
    assert observed['session_init'] == {
        'agent_id': 'allowed',
        'user_id': 'user-1',
    }
    assert observed['call_args'][2] == 'web'
    assert observed['call_kwargs']['tool_context'] is authority
    assert observed['call_kwargs']['record_history'] is True
    assert observed['call_kwargs']['persist_history'] is False
    assert observed['call_kwargs']['compact_history'] is False


@pytest.mark.asyncio
async def test_call_agent_is_disabled_inside_durable_task_runs(monkeypatch):
    from cognitrix.tools.misc import call_agent

    async def forbidden_lookup(_conditions):
        raise AssertionError('durable execution must use its frozen planner/runtime')

    monkeypatch.setattr('cognitrix.agents.Agent.find_one', staticmethod(forbidden_lookup))
    token = set_execution_context(ToolExecutionContext(
        task_id='task-1',
        run_id='run-1',
        allowed_agents=frozenset({'agent-1'}),
    ))
    try:
        out = (await call_agent.run(name='Agent', task='bypass snapshot')).content
    finally:
        reset_execution_context(token)

    assert 'unavailable inside durable task runs' in out
