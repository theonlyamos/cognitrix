"""OpenAI-compatible shim: model resolution, allowlists, message seeding."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.api.routes import openai_compat as shim
from cognitrix.common.security import AuthContext
from cognitrix.models.api_key import APIKey


def _key(**overrides):
    base = dict(name='k', user_id='u', key_hash='h', prefix='p', scopes=['chat'], webhook_secret='w')
    base.update(overrides)
    return APIKey(**base)


def _msg(role, content):
    return shim.ChatMessage(role=role, content=content)


class _FakeSession:
    """Captures what _seed_session builds without touching the DB."""
    def __init__(self, agent_id=None, **kw):
        self.agent_id = agent_id
        self.chat = []
        self.id = 'sess-1'


@pytest.fixture(autouse=True)
def patch_session(monkeypatch):
    monkeypatch.setattr(shim, 'Session', _FakeSession)


def test_seed_session_includes_final_user_message():
    agent = SimpleNamespace(id='a1')
    messages = [
        _msg('system', 'You are terse.'),
        _msg('user', 'Hi'),
        _msg('assistant', 'Hello'),
        _msg('user', 'What is 2+2?'),
    ]
    session = shim._seed_session(agent, messages)
    contents = [m['content'] for m in session.chat]
    # The final user message MUST be present — the whole bug this guards.
    assert 'What is 2+2?' in contents
    assert any('[system]' in c for c in contents)
    roles = [m['role'] for m in session.chat]
    assert roles == ['User', 'User', 'assistant', 'User']
    # Empty-content messages are skipped.
    assert shim._seed_session(agent, [_msg('user', '')]).chat == []


async def test_resolve_agent_by_name_and_id(monkeypatch):
    agents = [SimpleNamespace(id='a1', name='Assistant'), SimpleNamespace(id='a2', name='Coder')]
    monkeypatch.setattr(shim.Agent, 'all', staticmethod(lambda: _async(agents)))
    jwt = AuthContext(user=SimpleNamespace(id='u'))

    assert (await shim._resolve_agent('assistant', jwt)).id == 'a1'  # case-insensitive name
    assert (await shim._resolve_agent('a2', jwt)).id == 'a2'          # by id
    with pytest.raises(HTTPException) as e:
        await shim._resolve_agent('nope', jwt)
    assert e.value.status_code == 404


async def test_resolve_agent_allowlist(monkeypatch):
    agents = [SimpleNamespace(id='a1', name='Assistant')]
    monkeypatch.setattr(shim.Agent, 'all', staticmethod(lambda: _async(agents)))
    bound = AuthContext(user=SimpleNamespace(id='u'), api_key=_key(allowed_agents=['a2']))
    with pytest.raises(HTTPException) as e:
        await shim._resolve_agent('Assistant', bound)
    assert e.value.status_code == 403


async def test_list_models_filters_by_allowlist(monkeypatch):
    agents = [SimpleNamespace(id='a1', name='Assistant'), SimpleNamespace(id='a2', name='Coder')]
    monkeypatch.setattr(shim.Agent, 'all', staticmethod(lambda: _async(agents)))
    bound = AuthContext(user=SimpleNamespace(id='u'), api_key=_key(allowed_agents=['a1']))
    result = await shim.list_models(bound)
    assert [m['id'] for m in result['data']] == ['Assistant']
    assert result['object'] == 'list'


def _async(value):
    async def _coro():
        return value
    return _coro()
