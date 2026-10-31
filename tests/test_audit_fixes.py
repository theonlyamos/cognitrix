"""Regression tests for the Oct 2026 harness audit fixes."""
import pytest

SERVER_KEY = 'srv-cerebras-key-123456'


# --- Server provider keys never go to a user-chosen base_url ----------------

@pytest.fixture
def server_key(monkeypatch):
    monkeypatch.setenv('CEREBRAS_API_KEY', SERVER_KEY)
    monkeypatch.delenv('CEREBRAS_BASE_URL', raising=False)
    monkeypatch.delenv('COGNITRIX_TRUSTED_BASE_URLS', raising=False)
    return SERVER_KEY


def test_server_key_is_refused_for_a_custom_base_url(server_key):
    from cognitrix.providers.base import LLM, ProviderKeyDestinationError

    with pytest.raises(ProviderKeyDestinationError):
        LLM(provider='cerebras', base_url='http://attacker.example/v1', model='x')


def test_server_key_is_used_for_the_default_endpoint(server_key):
    from cognitrix.providers.base import LLM

    llm = LLM(provider='cerebras', model='gpt-oss-120b')
    assert llm.api_key == server_key


def test_a_callers_own_key_may_go_to_any_base_url(server_key):
    from cognitrix.providers.base import LLM

    llm = LLM(provider='cerebras', base_url='http://my-proxy.example/v1',
              api_key='my-own-key-999', model='x')
    assert llm.api_key == 'my-own-key-999'


def test_operator_can_trust_extra_base_urls(server_key, monkeypatch):
    from cognitrix.providers.base import LLM

    monkeypatch.setenv('COGNITRIX_TRUSTED_BASE_URLS', 'https://gateway.internal/v1/')
    llm = LLM(provider='cerebras', base_url='https://gateway.internal/v1', model='x')
    assert llm.api_key == server_key


@pytest.mark.asyncio
async def test_generate_refuses_a_server_key_mutated_onto_a_foreign_url(server_key, monkeypatch):
    """Stored agents predate the check, and fields can change after init."""
    from cognitrix.providers import base

    llm = base.LLM(provider='cerebras', model='x')
    llm.base_url = 'http://attacker.example/v1'

    def no_client(*_a, **_k):
        raise AssertionError('no client may be created for a foreign URL')

    monkeypatch.setattr(base, '_get_or_create_client', no_client)
    result = await base.LLMManager.generate_response(llm, [{'role': 'user', 'content': 'hi'}])
    assert result.error and 'not a trusted endpoint' in result.error


# --- Runtime-only tool parameters --------------------------------------------

def test_mcp_tools_hide_and_skip_validation_of_parent():
    from cognitrix.tools.mcp import list_mcp_tools, run_mcp_tool

    for tool in (list_mcp_tools, run_mcp_tool):
        schema = tool.to_dict_format()['function']['parameters']
        assert 'parent' not in schema['properties']
        assert 'parent' not in schema['required']
        assert tool.internal_params == ['parent']

    # The live agent object passes through untouched (it used to fail with
    # "TypeAdapter[Agent] is not fully defined").
    sentinel = object()
    validated = run_mcp_tool.validate_parameters(
        {'server': 's', 'tool_name': 't', 'arguments': {}, 'parent': sentinel}
    )
    assert validated['parent'] is sentinel


def test_call_agent_interface_is_not_model_visible():
    from cognitrix.tools.misc import call_agent

    schema = call_agent.to_dict_format()['function']['parameters']
    assert 'interface' not in schema['properties']


def test_stale_stored_tool_copies_also_hide_parent():
    from cognitrix.models.tool import Tool
    from cognitrix.tools.mcp import run_mcp_tool

    legacy = Tool(**{**run_mcp_tool.model_dump(), 'internal_params': []})
    legacy.parameters = {**run_mcp_tool.parameters, 'parent': 'Agent'}
    schema = legacy.to_dict_format()['function']['parameters']
    assert 'parent' not in schema['properties']


# --- Long-term memory ----------------------------------------------------------

def test_personal_facts_now_reach_the_memory_threshold():
    from cognitrix.memory.hybrid_context import ImportanceScorer

    scorer = ImportanceScorer()
    assert scorer.score({'role': 'user', 'content': 'Remember my codename is Falcon'}) >= 0.7
    assert scorer.score({'role': 'user', 'content': 'hello there'}) < 0.7


@pytest.mark.asyncio
async def test_memories_are_tagged_and_retrieved_per_user(monkeypatch):
    from cognitrix.memory.hybrid_context import HybridContextManager

    monkeypatch.delenv('DISABLE_VECTOR_STORE', raising=False)
    stored, filters = [], []

    class FakeStore:
        async def store(self, content, metadata, importance):
            stored.append(metadata)

        async def retrieve(self, query, k, filter_metadata=None):
            filters.append(filter_metadata)
            return []

    mgr = HybridContextManager(agent_id='a1')

    async def ensure():
        return FakeStore()

    mgr._ensure_long_term = ensure
    await mgr.add_to_memory({'role': 'user', 'content': 'Remember my codename is Falcon'}, user_id='u1')
    assert stored and stored[0]['user_id'] == 'u1'

    class FakeAgent:
        def formatted_system_prompt(self):
            return 'sys'

    class FakeSession:
        user_id = 'u2'
        chat = [{'role': 'user', 'type': 'text', 'content': 'what is my codename?'}]

    async def short_term(_agent, _session):
        return [{'role': 'system', 'content': 'sys'}]

    mgr.short_term.build_prompt = short_term
    await mgr.build_prompt(FakeAgent(), FakeSession())
    assert filters == [{'user_id': 'u2'}]


# --- Fresh database ------------------------------------------------------------

@pytest.mark.asyncio
async def test_fresh_database_gets_core_tables(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'fresh.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()

    cursor = await DBMS.Database.query("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert {'users', 'agents', 'sessions', 'tasks', 'teams'} <= tables

    cursor = await DBMS.Database.query('PRAGMA table_info(agents)')
    assert 'owner_id' in {row[1] for row in cursor.fetchall()}


# --- Agent visibility ----------------------------------------------------------

def test_agent_visibility_rules():
    from cognitrix.agents import access

    class A:
        def __init__(self, owner):
            self.owner_id = owner

    assert access.can_view(A(None), 'u1')
    assert access.can_view(A('u1'), 'u1')
    assert not access.can_view(A('u2'), 'u1')
    assert not access.can_view(A('u2'), None)


@pytest.mark.asyncio
async def test_admins_come_from_env(monkeypatch):
    from cognitrix.agents import access

    class U:
        id = 'u1'
        email = 'Boss@Example.com'

    monkeypatch.setenv('COGNITRIX_ADMIN_EMAILS', 'boss@example.com, other@example.com')
    assert await access.is_admin(U())

    class Shared:
        owner_id = None

    class Other:
        id = 'u9'
        email = 'nobody@example.com'

    assert await access.can_edit(Shared(), U())
    assert not await access.can_edit(Shared(), Other())


# --- SSRF ----------------------------------------------------------------------

def test_webfetch_refuses_loopback(monkeypatch):
    import requests

    from cognitrix.tools.misc import WebFetch

    monkeypatch.delenv('COGNITRIX_WEBFETCH_ALLOW_PRIVATE', raising=False)

    def no_request(*_a, **_k):
        raise AssertionError('no request may be sent to a loopback address')

    monkeypatch.setattr(requests, 'get', no_request)
    import asyncio
    out = asyncio.run(WebFetch.run(url='http://127.0.0.1:8000/health')).content
    assert 'private/loopback' in out


# --- Web API ---------------------------------------------------------------------

def test_openapi_schema_builds_and_unknown_api_paths_404():
    from fastapi.testclient import TestClient

    from cognitrix.api.main import app

    client = TestClient(app)  # no lifespan: schema generation only
    assert client.get('/openapi.json').status_code == 200
    assert client.get('/api/v1/does-not-exist').status_code == 404


# --- Provider retries and malformed tool arguments ---------------------------

def test_retry_delay_honours_retry_after():
    from cognitrix.providers.base import _retry_delay

    class Resp:
        headers = {'retry-after': '6.6'}

    class Err(Exception):
        response = Resp()

    assert _retry_delay(Err(), 1) == 6.6
    Resp.headers = {'retry-after': '999'}
    assert _retry_delay(Err(), 1) == 30.0
    Resp.headers = {}
    assert 1.0 <= _retry_delay(Err(), 1) <= 1.5


@pytest.mark.asyncio
async def test_streaming_retries_a_429_before_the_first_chunk(monkeypatch):
    from cognitrix.providers import base

    class RateLimited(Exception):
        response = None

    monkeypatch.setattr(base.openai, 'RateLimitError', RateLimited)
    monkeypatch.setattr(base.asyncio, 'sleep', _no_sleep)
    calls = {'n': 0}

    class Chunk:
        def __init__(self, content, finish=None):
            self.usage = None
            delta = type('D', (), {'content': content, 'tool_calls': None})()
            self.choices = [type('C', (), {'delta': delta, 'finish_reason': finish})()]

    async def stream():
        yield Chunk('hello', 'stop')

    class Completions:
        async def create(self, **_params):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RateLimited('429')
            return stream()

    client = type('Client', (), {'chat': type('Chat', (), {'completions': Completions()})()})()
    chunks = [r async for r in base.LLMManager._handle_streaming_response(client, {})]
    assert calls['n'] == 2
    assert not chunks[-1].error
    assert chunks[-1].finish_reason == 'stop'


async def _no_sleep(*_a, **_k):
    return None


def test_malformed_tool_arguments_become_an_error_not_empty_args():
    from cognitrix.providers.base import LLMManager

    fn = type('F', (), {'name': 'Write', 'arguments': '{"file_path": "a.txt", "content": "unterminated'})()
    tc = type('T', (), {'function': fn, 'id': 'c1'})()
    parsed = LLMManager._parse_native_tool_calls([tc], truncated=True)
    assert parsed[0]['arguments'] == {}
    assert 'output token limit' in parsed[0]['arguments_error']


# --- Explicit task routing -----------------------------------------------------

def test_tasks_are_explicit_opt_in():
    from cognitrix.tasks.handler import parse_task_request

    assert parse_task_request('Create foo.py, then run it') is None
    assert parse_task_request('/task Create foo.py, then run it') == 'Create foo.py, then run it'
    assert parse_task_request('/taskforce rocks') is None


# --- Parallel edits to one file --------------------------------------------------

@pytest.mark.asyncio
async def test_same_path_tool_calls_share_a_lock_and_others_do_not():
    from cognitrix.agents.base import _path_lock_for

    a = _path_lock_for({'file_path': 'notes.txt'})
    b = _path_lock_for({'file_path': './notes.txt'})
    c = _path_lock_for({'file_path': 'other.txt'})
    assert a is b
    assert a is not c
    assert _path_lock_for({'query': 'x'}) is None


@pytest.mark.asyncio
async def test_parallel_edits_on_one_file_all_land(tmp_path, monkeypatch):
    from cognitrix.agents.base import AgentManager
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.tools.base import ToolManager

    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'tools_root', str(tmp_path))
    target = tmp_path / 'f.txt'
    target.write_text('a\nb\nc\nd\n')
    edit = ToolManager.get_by_name('Edit')
    agent = Agent(name='e', llm=LLM(provider='openai', api_key='k-1234567890', model='m'),
                  tools=[edit], system_prompt='s')
    calls = [
        {'name': 'Edit', 'tool_call_id': f'c{i}',
         'arguments': {'file_path': str(target), 'old_string': f'{ch}\n', 'new_string': f'{ch.upper()}\n'}}
        for i, ch in enumerate('abcd')
    ]
    manager = AgentManager(agent)

    async def approve(**_kwargs):
        return type('A', (), {'approved': True, 'cached': True, 'error': None})()

    monkeypatch.setattr(manager.approval_gate, 'check_approval', approve)
    await manager.call_tools(calls, interface='cli')
    assert target.read_text() == 'A\nB\nC\nD\n'


# --- Failed turns ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_unexpected_turn_error_is_shown_and_recorded(monkeypatch):
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.sessions.base import Session

    agent = Agent(name='x', llm=LLM(provider='openai', api_key='k-1234567890', model='m'),
                  tools=[], system_prompt='s')

    async def boom(*_a, **_k):
        raise RuntimeError('kaput')

    monkeypatch.setattr('cognitrix.providers.base.LLMManager.generate_response', staticmethod(boom))
    shown = []
    session = Session(agent_id='x')
    await session('hi', agent, 'cli', False, lambda *a, **k: shown.append(' '.join(map(str, a))),
                  None, False)
    assert session.last_turn_error and 'kaput' in session.last_turn_error
    assert any('kaput' in s for s in shown)


# --- Shell allowlist ------------------------------------------------------------

def test_shell_refuses_paths_outside_the_working_directory(tmp_path):
    from cognitrix.common.safe_exec import CommandNotAllowed, build_argv

    assert build_argv(f'cat {tmp_path}/notes.txt', root=tmp_path)[1].endswith('notes.txt')
    for bad in ('cat /etc/passwd', 'cat ~/.ssh/id_rsa', 'ls /', 'grep --file=/etc/shadow x'):
        with pytest.raises(CommandNotAllowed):
            build_argv(bad, root=tmp_path)


def test_shell_refuses_git_options_that_run_programs(tmp_path):
    from cognitrix.common.safe_exec import CommandNotAllowed, build_argv

    assert build_argv('git log -c', root=tmp_path) == ['git', 'log', '-c']
    for bad in ('git -c core.pager=id log', 'git clone --upload-pack=id x', 'git --exec-path=/x status'):
        with pytest.raises(CommandNotAllowed):
            build_argv(bad, root=tmp_path)


def test_sed_cannot_execute_or_write(monkeypatch, tmp_path):
    from cognitrix.common import safe_exec

    # GNU sed: --sandbox makes sed itself refuse e/r/w.
    monkeypatch.setattr(safe_exec, '_gnu_sed', lambda: True)
    assert safe_exec.build_argv('sed s/a/b/ f', root=tmp_path)[:2] == ['sed', '--sandbox']

    # Other seds: the script is checked instead.
    monkeypatch.setattr(safe_exec, '_gnu_sed', lambda: False)
    assert safe_exec.build_argv('sed -n 2p where.txt', root=tmp_path)
    for bad in ('sed s/a/id/e f', 'sed 1e\\ id f', 'sed w/tmp/x f', "sed 'r secrets' f", 'sed -f s.sed f'):
        with pytest.raises(safe_exec.CommandNotAllowed):
            safe_exec.build_argv(bad, root=tmp_path)


def test_shell_output_keeps_stderr():
    from cognitrix.common.safe_exec import _format_output

    assert _format_output('out', 'warning', 0) == 'out\n\n[stderr: warning]'
    assert _format_output('', 'warning', 0) == '[stderr: warning]'
    assert 'boom' in _format_output('', 'boom', 1)


# --- Logout ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_revokes_earlier_session_tokens(monkeypatch):
    import time
    from types import SimpleNamespace

    from fastapi import HTTPException

    from cognitrix.common import security

    user = SimpleNamespace(id='u1', email='a@example.com', tokens_valid_after=None)

    async def get_user(email=None, **_kw):
        return user if email == user.email else None

    monkeypatch.setattr(security, 'get_user', get_user)
    token = security.create_access_token({'sub': user.email})
    assert (await security._resolve_jwt(token)).user is user

    user.tokens_valid_after = int(time.time()) + 1  # logged out after issue
    with pytest.raises(HTTPException) as exc:
        await security._resolve_jwt(token)
    assert exc.value.status_code == 401
    assert await security.verify_token(token) is None
