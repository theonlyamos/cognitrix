"""Per-agent settings from the agent page: the reviewer override, coding agent
limits, saved sign-ins, sub-agents, self check-ins, run caps, the model list
endpoint and the settings carried into task runs."""

import os
import stat
import sys

import pytest
from fastapi import HTTPException

import cognitrix.agents  # noqa: F401  (attaches call_tools to Agent)
from cognitrix.api.routes import agents as agent_routes
from cognitrix.api.routes import providers as provider_routes
from cognitrix.common import coding_agents as ca
from cognitrix.common.folders import Folder
from cognitrix.common.process_security import HostProcessMode
from cognitrix.credentials import only_allowed
from cognitrix.models import Agent
from cognitrix.models.tool import Tool
from cognitrix.providers.base import LLM
from cognitrix.safety import action_reviewer
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
from cognitrix.safety.permissions import decide_tool_call, switched_off, visible_tools
from cognitrix.tasks import automation
from cognitrix.tasks.runtime import build_runtime_snapshot, instantiate_runtime
from cognitrix.tools.misc import coding_agent
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

LOW = RiskAssessment(RiskLevel.LOW, [], "fine")


def _llm():
    return LLM(provider="openai", base_url="http://x", api_key="k", model="m")


def _agent(**kw):
    return Agent(name="A", llm=_llm(), system_prompt="sys", **kw)


# ── stored values ────────────────────────────────────────────────────────


def test_old_rows_load_with_defaults():
    agent = _agent(description=None, reviewer=None, coding_agents='', credentials='',
                   can_create_sub_agents=None, sub_agent_model=None, automation=None)
    assert agent.description == ''
    assert agent.reviewer == {} and agent.coding_agents == {} and agent.automation == {}
    assert agent.credentials is None
    assert agent.can_create_sub_agents is True
    assert agent.sub_agent_model == ''


def test_json_text_columns_are_read():
    agent = _agent(reviewer='{"mode": "always"}', credentials='["GITHUB_TOKEN"]')
    assert agent.reviewer == {'mode': 'always'}
    assert agent.credentials == ['GITHUB_TOKEN']


def test_save_keeps_settings_the_client_left_out():
    existing = _agent(description='Keeps code tidy', reviewer={'mode': 'always'}, credentials=['A_KEY'],
                      can_create_sub_agents=False, automation={'max_runs_per_day': 5})
    incoming = _agent()
    agent_routes._check_policy(incoming, {'name': 'A'}, existing)
    assert incoming.description == 'Keeps code tidy'
    assert incoming.reviewer == {'mode': 'always'}
    assert incoming.credentials == ['A_KEY']
    assert incoming.can_create_sub_agents is False
    assert incoming.automation == {'max_runs_per_day': 5}


def test_save_cleans_and_validates():
    agent = _agent(reviewer={'mode': 'Unattended', 'max_allow': 'HIGH', 'model': ''},
                   coding_agents={'clis': ['Claude Code', 'codex', 'claude'], 'max_mode': 'read-only'},
                   credentials=['github_token', ' ', 'GITHUB_TOKEN'],
                   automation={'self_checkins': 0, 'max_runs_per_day': 12, 'other': 1})
    data = {k: True for k in ('reviewer', 'coding_agents', 'credentials', 'automation')}
    agent_routes._check_policy(agent, data, None)
    assert agent.reviewer == {'mode': 'unattended', 'max_allow': 'high'}
    assert agent.coding_agents == {'clis': ['claude', 'codex'], 'max_mode': 'read_only'}
    assert agent.credentials == ['GITHUB_TOKEN']
    assert agent.automation == {'self_checkins': False, 'max_runs_per_day': 12}

    for bad in ({'reviewer': {'mode': 'sometimes'}}, {'coding_agents': {'clis': ['vim']}},
                {'automation': {'max_runs_per_day': 0}}, {'description': 'x' * 301}):
        agent = _agent(**bad)
        with pytest.raises(HTTPException):
            agent_routes._check_policy(agent, bad, None)


# ── second-model check ───────────────────────────────────────────────────


def test_agent_reviewer_settings_override_the_environment(monkeypatch):
    monkeypatch.setenv('COGNITRIX_REVIEWER', 'off')
    monkeypatch.setenv('COGNITRIX_REVIEWER_PROVIDER', 'openai')
    monkeypatch.setenv('COGNITRIX_REVIEWER_MODEL', 'env-model')
    assert action_reviewer.reviewer_mode() == 'off'
    token = action_reviewer.agent_settings.set({'mode': 'always', 'provider': 'anthropic',
                                                 'model': 'claude-haiku-5-5', 'max_allow': 'high'})
    try:
        cfg = action_reviewer.reviewer_config()
        assert cfg['mode'] == 'always'
        assert (cfg['provider'], cfg['model']) == ('anthropic', 'claude-haiku-5-5')
        assert cfg['max_allow'] == 'high'
        medium = RiskAssessment(RiskLevel.MEDIUM, ['write'], 'writes')
        assert action_reviewer.should_review(medium, 'chat')
    finally:
        action_reviewer.agent_settings.reset(token)
    # A mode alone keeps the app's model.
    token = action_reviewer.agent_settings.set({'mode': 'unattended'})
    try:
        cfg = action_reviewer.reviewer_config()
        assert (cfg['mode'], cfg['provider'], cfg['model']) == ('unattended', 'openai', 'env-model')
    finally:
        action_reviewer.agent_settings.reset(token)
    assert action_reviewer.reviewer_mode() == 'off'


# ── switches that turn tools off ─────────────────────────────────────────


def test_sub_agents_and_self_checkins_can_be_switched_off():
    create = Tool(name='Create Agent', description='d', parameters={})
    wake = Tool(name='Schedule Next Check', description='d', parameters={}, approval_mode='assigned_only')
    other = Tool(name='Read File', description='d', parameters={})
    agent = _agent(can_create_sub_agents=False, automation={'self_checkins': False})
    assert switched_off(agent, 'create_agent')
    assert [t.name for t in visible_tools(agent, [create, wake, other])] == ['Read File']
    assert decide_tool_call(agent, create, {}, LOW).action == 'block'
    assert decide_tool_call(agent, wake, {}, LOW).action == 'block'
    # Defaults leave them on.
    plain = _agent()
    assert len(visible_tools(plain, [create, wake, other])) == 3
    assert decide_tool_call(plain, wake, {}, LOW).action == 'run'


# ── saved sign-ins ───────────────────────────────────────────────────────


def test_only_allowed_credentials():
    class Row:
        def __init__(self, name):
            self.name = name

    rows = [Row('GITHUB_TOKEN'), Row('NPM_PUBLISH')]
    assert only_allowed(rows, None) == rows
    assert [r.name for r in only_allowed(rows, ['github-token'])] == ['GITHUB_TOKEN']
    assert only_allowed(rows, []) == []


# ── coding agent limits ──────────────────────────────────────────────────


def test_coding_agent_limits():
    assert ca.apply_agent_limits('auto', 'edit', None) == 'auto'
    assert ca.apply_agent_limits('auto', 'edit', {'clis': ['codex', 'gemini']}) == 'auto:codex,gemini'
    assert ca.apply_agent_limits('codex', 'read_only', {'clis': ['codex'], 'max_mode': 'edit'}) == 'codex'
    with pytest.raises(ca.CodingAgentError, match='at most'):
        ca.apply_agent_limits('auto', 'full', {'max_mode': 'edit'})
    with pytest.raises(ca.CodingAgentError, match='only use'):
        ca.apply_agent_limits('claude', 'edit', {'clis': ['codex']})
    with pytest.raises(ca.CodingAgentError, match='any coding agent'):
        ca.apply_agent_limits('auto', 'edit', {'clis': []})


FAKE_CLI = f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nprint('ran')\n"


@pytest.fixture
def folder_ctx(tmp_path, monkeypatch):
    work = (tmp_path / 'work').resolve()
    work.mkdir()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    monkeypatch.setenv('PATH', str(bin_dir))
    for key in ca.CLIS:
        monkeypatch.delenv(f'COGNITRIX_{key.upper()}_PATH', raising=False)

    def use(access):
        return set_execution_context(ToolExecutionContext(
            user_id='u1', session_id='s-' + access, host_process_mode=HostProcessMode.TRUSTED_LOCAL,
            folders=(Folder(path=str(work), access=access, name='work', primary=True),),
        ))

    def add_cli(name):
        path = bin_dir / name
        path.write_text(FAKE_CLI, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    return work, use, add_cli


async def _run(**kwargs) -> str:
    return (await coding_agent.run(**kwargs)).content


async def test_coding_agent_refuses_edits_in_a_read_only_folder(folder_ctx):
    _work, use, _add = folder_ctx
    token = use('read')
    try:
        out = await _run(task='fix it', mode='edit', approved=True)
    finally:
        reset_execution_context(token)
    assert 'read only' in out


@pytest.mark.skipif(os.name == 'nt', reason='uses a POSIX script as a fake CLI')
async def test_coding_agent_follows_agent_limits(folder_ctx):
    work, use, add_cli = folder_ctx
    add_cli('claude')
    token = use('write')
    try:
        parent = _agent(coding_agents={'clis': ['codex']})
        out = await _run(task='x', approved=True, parent=parent)
        assert 'None of the coding agents this agent may use is installed' in out
        parent = _agent(coding_agents={'max_mode': 'read_only'})
        out = await _run(task='x', mode='edit', approved=True, parent=parent)
        assert "mode 'read_only' at most" in out
        out = await _run(task='x', mode='read_only', approved=True, parent=parent, wait_seconds=10)
        assert f'Claude Code is working in {work}' in out
    finally:
        reset_execution_context(token)


# ── task runs and caps ───────────────────────────────────────────────────


def test_task_runs_carry_the_agent_settings(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    source = _agent(reviewer={'mode': 'always'}, coding_agents={'max_mode': 'edit'},
                    credentials=['GITHUB_TOKEN'], can_create_sub_agents=False,
                    sub_agent_model='same', automation={'self_checkins': False})
    snapshot = build_runtime_snapshot(source, required_tools=None)
    runtime = instantiate_runtime(snapshot, tool_resolver=lambda name: None)
    assert runtime.reviewer == {'mode': 'always'}
    assert runtime.coding_agents == {'max_mode': 'edit'}
    assert runtime.credentials == ['GITHUB_TOKEN']
    assert runtime.can_create_sub_agents is False
    assert runtime.sub_agent_model == 'same'
    assert runtime.automation == {'self_checkins': False}


async def test_agent_cap_is_the_default_for_its_tasks(monkeypatch):
    agent = _agent(automation={'max_runs_per_day': 3})

    async def find_one(query):
        return agent if query == {'id': 'agent-1'} else None

    async def runs_last_day(task_id, now=None):
        return 3

    monkeypatch.setattr(Agent, 'find_one', staticmethod(find_one))
    monkeypatch.setattr(automation, 'automation_runs_last_day', runs_last_day)

    class Task:
        id = 't1'
        assigned_agents = ['agent-1']
        max_runs_per_day = None

    assert await automation.agent_daily_cap(Task) == 3
    assert await automation.over_daily_cap(Task)
    Task.max_runs_per_day = 10  # the task's own cap wins
    assert not await automation.over_daily_cap(Task)


# ── model lists ──────────────────────────────────────────────────────────


async def test_provider_models_lists_and_caches(monkeypatch):
    calls = []

    def fake_list(provider):
        calls.append(provider)
        return ['gpt-5.5', 'gpt-5.4-mini']

    provider_routes._models_cache.clear()
    monkeypatch.setattr(provider_routes, '_list_models', fake_list)
    first = await provider_routes.provider_models('openai', _ctx=None)
    second = await provider_routes.provider_models('openai', _ctx=None)
    assert first == second == {'models': ['gpt-5.5', 'gpt-5.4-mini'], 'live': True}
    assert calls == ['openai']

    def broken(provider):
        raise ValueError('No API key for groq')

    monkeypatch.setattr(provider_routes, '_list_models', broken)
    out = await provider_routes.provider_models('groq', _ctx=None)
    assert out == {'models': [], 'live': False, 'reason': "Couldn't list groq models"}
    assert 'No API key' not in str(out)
    with pytest.raises(HTTPException):
        await provider_routes.provider_models('nope', _ctx=None)


def test_list_models_filters_and_sorts(monkeypatch):
    class Row:
        def __init__(self, id, created):
            self.id = id
            self.created = created

    class Client:
        def __init__(self, **kw):
            self.models = self

        def list(self):
            class Page:
                data = [Row('whisper-1', 9), Row('gpt-5.4', 5), Row('gpt-5.5', 7),
                        Row('text-embedding-3', 8), Row('models/gemini-x', 1)]
            return Page()

    import openai

    monkeypatch.setattr(openai, 'OpenAI', Client)
    monkeypatch.setattr('cognitrix.providers.base._resolve_runtime_config',
                        lambda p: {'api_key': 'k', 'base_url': 'http://x'})
    assert provider_routes._list_models('google') == ['gpt-5.5', 'gpt-5.4', 'gemini-x']
