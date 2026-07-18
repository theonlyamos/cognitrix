import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _ctx(user_id: str, *, allowed_agents=None, allowed_teams=None):
    from cognitrix.common.security import AuthContext
    from cognitrix.models.api_key import APIKey

    key = None
    if allowed_agents is not None or allowed_teams is not None:
        key = APIKey(
            name='test',
            user_id=user_id,
            key_hash='hash',
            prefix='ctx_test',
            scopes=['read', 'write', 'chat'],
            allowed_agents=allowed_agents or [],
            allowed_teams=allowed_teams or [],
            webhook_secret='secret',
        )
    return AuthContext(
        user=SimpleNamespace(id=user_id, email=f'{user_id}@example.com'),
        api_key=key,
    )


async def _initialize_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session

    db_file = str(tmp_path / 'cross-routes.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await Session.create_table()
    await SessionOwnership._create_table_async()


async def _owned_session(user_id: str, agent_id: str, **fields):
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions.base import Session

    session = Session(agent_id=agent_id, **fields)
    await session.save()
    await claim_new(str(session.id), user_id, agent_id)
    return session


async def test_generate_resolves_binding_before_session_load(tmp_path, monkeypatch):
    from cognitrix.api.routes import agents as routes
    from cognitrix.sessions.base import Session

    await _initialize_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1')
    foreign = await _owned_session('user-2', 'agent-1')
    legacy = Session(agent_id='agent-1')
    await legacy.save()
    loaded = []
    original_get = Session.get

    async def tracked_get(session_id):
        loaded.append(str(session_id))
        return await original_get(session_id)

    monkeypatch.setattr(routes.Session, 'get', tracked_get)
    agent = SimpleNamespace(id='agent-1')
    for denied in (str(foreign.id), str(legacy.id), 'missing'):
        with pytest.raises(HTTPException) as error:
            await routes._resolve_generate_session(agent, denied, _ctx('user-1'))
        assert error.value.status_code == 404
    assert loaded == []

    binding, resolved = await routes._resolve_generate_session(
        agent, str(own.id), _ctx('user-1'),
    )
    assert binding.session_id == own.id
    assert resolved.id == own.id
    assert loaded == [str(own.id)]


async def test_generate_fresh_session_claims_and_compensates_cancellation(
    tmp_path, monkeypatch,
):
    from cognitrix.api.routes import agents as routes
    from cognitrix.session_ownership import SessionOwnership, require_owned
    from cognitrix.sessions.base import Session

    await _initialize_db(tmp_path)
    agent = SimpleNamespace(id='agent-1')
    binding, session = await routes._resolve_generate_session(
        agent, None, _ctx('user-1'),
    )
    assert await require_owned(str(session.id), 'user-1', 'agent-1')
    assert binding.session_id == session.id

    await Session.delete_many({'id': str(session.id)})
    await SessionOwnership.delete_many({'session_id': str(session.id)})

    original_claim = routes.claim_new

    async def claim_then_cancel(session_id, user_id, agent_id):
        await original_claim(session_id, user_id, agent_id)
        raise asyncio.CancelledError()

    monkeypatch.setattr(routes, 'claim_new', claim_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await routes._resolve_generate_session(agent, None, _ctx('user-1'))
    assert await Session.all() == []
    assert await SessionOwnership.all() == []


async def test_generate_fresh_session_save_cancellation_is_settled_and_compensated(
    tmp_path, monkeypatch,
):
    from cognitrix.api.routes import agents as routes
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session

    await _initialize_db(tmp_path)
    original_save = Session.save
    committed = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def effect_then_wait(self):
        result = await original_save(self)
        committed.set()
        await release.wait()
        finished.set()
        return result

    monkeypatch.setattr(Session, 'save', effect_then_wait)
    task = asyncio.create_task(routes._resolve_generate_session(
        SimpleNamespace(id='agent-1'), None, _ctx('user-1'),
    ))
    await committed.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()
    assert await Session.all() == []
    assert await SessionOwnership.all() == []


async def test_generate_binds_exact_turn_context(tmp_path, monkeypatch):
    from cognitrix.api.routes import agents as routes
    from cognitrix.agents import Agent

    await _initialize_db(tmp_path)
    session = await _owned_session('user-1', 'agent-1')
    agent = SimpleNamespace(id='agent-1')
    calls = []

    async def find_agent(_query):
        return agent

    async def turn(self, message, selected_agent, **kwargs):
        calls.append((str(self.id), message, selected_agent.id, kwargs['tool_context']))

    monkeypatch.setattr(Agent, 'find_one', find_agent)
    monkeypatch.setattr(routes.Session, '__call__', turn)
    response = await routes.generate(
        'agent-1',
        routes.GenerateRequest(message='hello', session_id=str(session.id)),
        _ctx('user-1'),
    )

    assert response['session_id'] == session.id
    assert len(calls) == 1
    _, _, _, tool_context = calls[0]
    assert tool_context.user_id == 'user-1'
    assert tool_context.session_id == session.id
    assert tool_context.agent_id == 'agent-1'


async def test_agent_session_endpoint_reuses_only_owned_session(tmp_path, monkeypatch):
    from cognitrix.api.routes import agents as routes
    from cognitrix.agents import Agent
    from cognitrix.session_ownership import require_owned
    from cognitrix.sessions.base import Session

    await _initialize_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1')
    await _owned_session('user-2', 'agent-1')
    legacy = Session(agent_id='agent-1')
    await legacy.save()

    async def find_agent(_query):
        return SimpleNamespace(id='agent-1')

    monkeypatch.setattr(Agent, 'find_one', find_agent)
    response = await routes.load_session('agent-1', _ctx('user-1'))
    assert json.loads(response.body)['session_id'] == own.id

    response = await routes.load_session('agent-1', _ctx('user-3'))
    fresh_id = json.loads(response.body)['session_id']
    assert fresh_id not in {own.id, legacy.id}
    assert await require_owned(fresh_id, 'user-3', 'agent-1')


async def test_team_sessions_load_only_owned_allowlisted_rows(tmp_path):
    from cognitrix.api.routes import teams as routes

    await _initialize_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1', team_id='team-1')
    await _owned_session('user-1', 'agent-2', team_id='team-1')
    await _owned_session('user-2', 'agent-1', team_id='team-1')
    await _owned_session('user-1', 'agent-1', team_id='team-2')

    rows = await routes.sessions(
        'team-1',
        _ctx('user-1', allowed_agents=['agent-1'], allowed_teams=['team-1']),
    )
    assert [row['id'] for row in rows] == [own.id]

    with pytest.raises(HTTPException) as error:
        await routes.sessions(
            'team-1',
            _ctx('user-1', allowed_agents=['agent-1'], allowed_teams=['team-2']),
        )
    assert error.value.status_code == 403
