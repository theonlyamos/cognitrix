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
