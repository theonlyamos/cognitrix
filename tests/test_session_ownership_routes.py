import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _ctx(user_id: str, *, allowed_agents: list[str] | None = None, allowed_teams: list[str] | None = None):
    from cognitrix.common.security import AuthContext
    from cognitrix.models.api_key import APIKey

    key = None
    if allowed_agents is not None or allowed_teams is not None:
        key = APIKey(
            name='test', user_id=user_id, key_hash='hash', prefix='ctx_test',
            scopes=['read', 'write'], allowed_agents=allowed_agents or [],
            allowed_teams=allowed_teams or [], webhook_secret='secret',
        )
    return AuthContext(
        user=SimpleNamespace(id=user_id, email=f'{user_id}@example.com'),
        api_key=key,
    )


async def _initialize_routes_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session

    db_file = str(tmp_path / 'session-routes.db')
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


def _json_body(response):
    return json.loads(response.body.decode('utf-8'))


async def test_missing_foreign_and_unbound_are_404_before_session_load(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    foreign = await _owned_session('user-2', 'agent-1')
    legacy = Session(agent_id='agent-1')
    await legacy.save()

    calls = []
    original_get = Session.get

    async def tracked_get(session_id):
        calls.append(session_id)
        return await original_get(session_id)

    monkeypatch.setattr(routes.Session, 'get', tracked_get)
    for session_id in (str(foreign.id), str(legacy.id), 'missing-id'):
        with pytest.raises(HTTPException) as exc_info:
            await routes.get_session(session_id, _ctx('user-1'))
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == 'Session not found'
    assert calls == []


async def test_new_session_uses_server_id_and_claims_exact_principal_and_agent(tmp_path):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.session_ownership import require_owned
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    request = SimpleNamespace(state=SimpleNamespace(agent=SimpleNamespace(id='default-agent')))
    supplied = Session(id='client-chosen-id', agent_id='agent-1')

    response = await routes.new_session(request, supplied, _ctx('user-1'))
    stored = await Session.find_one({'agent_id': 'agent-1'})
    assert stored is not None
    assert str(stored.id) != 'client-chosen-id'
    assert response['id'] == stored.id
    binding = await require_owned(str(stored.id), 'user-1', 'agent-1')
    assert binding.session_id == stored.id

    with pytest.raises(HTTPException) as exc_info:
        await routes.new_session(
            request,
            Session(agent_id='agent-2'),
            _ctx('user-1', allowed_agents=['agent-1']),
        )
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await routes.new_session(
            request,
            Session(agent_id='agent-1', team_id='team-1'),
            _ctx(
                'user-1', allowed_agents=['agent-1'], allowed_teams=['team-2'],
            ),
        )
    assert exc_info.value.status_code == 403


async def test_new_session_claim_failure_and_cancellation_are_fully_compensated(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.session_ownership import SessionOwnership, claim_new as real_claim
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    request = SimpleNamespace(state=SimpleNamespace(agent=SimpleNamespace(id='agent-1')))

    async def fail_claim(*_args):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(routes, 'claim_new', fail_claim)
    with pytest.raises(RuntimeError, match='database unavailable'):
        await routes.new_session(request, Session(agent_id='agent-1'), _ctx('user-1'))
    assert await Session.all() == []
    assert await SessionOwnership.all() == []

    async def cancel_after_claim(session_id, user_id, agent_id):
        await real_claim(session_id, user_id, agent_id)
        raise asyncio.CancelledError()

    monkeypatch.setattr(routes, 'claim_new', cancel_after_claim)
    with pytest.raises(asyncio.CancelledError):
        await routes.new_session(request, Session(agent_id='agent-1'), _ctx('user-1'))
    assert await Session.all() == []
    assert await SessionOwnership.all() == []


async def test_new_session_save_cancellation_is_settled_then_compensated(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    request = SimpleNamespace(state=SimpleNamespace(agent=SimpleNamespace(id='agent-1')))
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
    task = asyncio.create_task(routes.new_session(
        request,
        Session(agent_id='agent-1'),
        _ctx('user-1'),
    ))
    await committed.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()
    assert await Session.all() == []
    assert await SessionOwnership.all() == []

async def test_lists_and_filters_load_only_owned_allowlisted_sessions(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)
    authorized_runs = []

    async def authorize_run(run_id, _ctx):
        authorized_runs.append(run_id)
        return SimpleNamespace(id=run_id)

    monkeypatch.setattr(routes, '_authorized_run', authorize_run)
    own_a1 = await _owned_session(
        'user-1', 'agent-1', team_id='team-1', task_id='task-1', run_id='run-1',
    )
    own_a2 = await _owned_session(
        'user-1', 'agent-2', team_id='team-2', task_id='task-2', run_id='run-2',
    )
    await _owned_session(
        'user-2', 'agent-1', team_id='team-1', task_id='task-1', run_id='run-1',
    )

    jwt = _ctx('user-1')
    assert {row['id'] for row in _json_body(await routes.get_all_sessions(jwt))} == {
        own_a1.id, own_a2.id,
    }
    assert [row['id'] for row in await routes.sessions_by_agent('agent-1', False, jwt)] == [own_a1.id]
    assert [row['id'] for row in await routes.sessions_by_team('team-1', jwt)] == [own_a1.id]
    assert [row['id'] for row in await routes.sessions_by_task('task-1', jwt)] == [own_a1.id]
    assert [row['id'] for row in await routes.sessions_by_run('run-1', jwt)] == [own_a1.id]
    assert authorized_runs == ['run-1']

    narrowed = _ctx('user-1', allowed_agents=['agent-1'])
    assert [row['id'] for row in _json_body(await routes.get_all_sessions(narrowed))] == [own_a1.id]
    with pytest.raises(HTTPException) as exc_info:
        await routes.get_session(str(own_a2.id), narrowed)
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as exc_info:
        await routes.sessions_by_agent('agent-2', False, narrowed)
    assert exc_info.value.status_code == 403

    team_narrowed = _ctx(
        'user-1', allowed_agents=['agent-1', 'agent-2'], allowed_teams=['team-2'],
    )
    with pytest.raises(HTTPException) as exc_info:
        await routes.sessions_by_team('team-1', team_narrowed)
    assert exc_info.value.status_code == 403


async def test_clear_and_delete_hold_lifecycle_and_delete_binding_last(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.session_ownership import OwnershipNotFound, OwnershipState, require_owned
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    clear_target = await _owned_session('user-1', 'agent-1', chat=[{
        'role': 'user', 'type': 'text', 'content': 'hello',
    }])
    delete_target = await _owned_session('user-1', 'agent-1')
    order = []

    async def cleanup(*, session_id, user_id, agent_id, generation):
        binding = await require_owned(session_id, user_id, agent_id)
        persisted = await Session.get(session_id)
        order.append((binding.state.value, session_id, generation, persisted is not None))
        if binding.state == OwnershipState.CLEARING:
            assert persisted.chat == []

    monkeypatch.setattr(routes, 'cleanup_owned_session_resources', cleanup)

    await routes.delete_chat(str(clear_target.id), _ctx('user-1'))
    cleared = await Session.get(str(clear_target.id))
    clear_binding = await require_owned(str(clear_target.id), 'user-1', 'agent-1')
    assert cleared.chat == []
    assert clear_binding.state == OwnershipState.ACTIVE and clear_binding.generation == 1

    await routes.delete_session(str(delete_target.id), _ctx('user-1'))
    assert await Session.get(str(delete_target.id)) is None
    with pytest.raises(OwnershipNotFound):
        await require_owned(str(delete_target.id), 'user-1', 'agent-1')
    assert order == [
        ('clearing', str(clear_target.id), 1, True),
        ('deleting', str(delete_target.id), 1, True),
    ]


async def test_clear_returns_409_while_promotion_lease_is_live(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.session_ownership import require_owned, reserve_intent
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    session = await _owned_session('user-1', 'agent-1', chat=[{
        'role': 'user', 'type': 'text', 'content': 'keep',
    }])
    binding = await require_owned(str(session.id), 'user-1', 'agent-1')
    await reserve_intent(
        str(session.id), 'user-1', 'agent-1', generation=binding.generation,
        promotion_token='live', size_bytes=1,
    )
    cleanup_called = False

    async def cleanup(**_kwargs):
        nonlocal cleanup_called
        cleanup_called = True

    monkeypatch.setattr(routes, 'cleanup_owned_session_resources', cleanup)
    with pytest.raises(HTTPException) as exc_info:
        await routes.delete_chat(str(session.id), _ctx('user-1'))
    assert exc_info.value.status_code == 409
    assert (await Session.get(str(session.id))).chat[0]['content'] == 'keep'
    assert cleanup_called is False


@pytest.mark.parametrize('operation', ['clear', 'delete'])
async def test_cleanup_failure_retains_lifecycle_and_can_resume(
    tmp_path, monkeypatch, operation,
):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.session_ownership import (
        OwnershipConflict,
        OwnershipState,
        require_owned,
        reserve_intent,
    )
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    session = await _owned_session('user-1', 'agent-1', chat=[{
        'role': 'user', 'type': 'text', 'content': 'message',
    }])
    attempts = 0

    async def flaky_cleanup(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('partial cleanup')

    monkeypatch.setattr(routes, 'cleanup_owned_session_resources', flaky_cleanup)
    endpoint = routes.delete_chat if operation == 'clear' else routes.delete_session
    with pytest.raises(RuntimeError, match='partial cleanup'):
        await endpoint(str(session.id), _ctx('user-1'))

    binding = await require_owned(str(session.id), 'user-1', 'agent-1')
    expected_state = OwnershipState.CLEARING if operation == 'clear' else OwnershipState.DELETING
    assert binding.state == expected_state
    with pytest.raises(OwnershipConflict):
        await reserve_intent(
            str(session.id), 'user-1', 'agent-1', generation=binding.generation,
            promotion_token='blocked', size_bytes=1,
        )

    # A retry resumes the same persisted lifecycle instead of trying to begin
    # a new generation or exposing the quarantined session as active.
    await endpoint(str(session.id), _ctx('user-1'))
    if operation == 'clear':
        resumed = await require_owned(str(session.id), 'user-1', 'agent-1')
        assert resumed.state == OwnershipState.ACTIVE
        assert (await Session.get(str(session.id))).chat == []
    else:
        assert await Session.get(str(session.id)) is None
        from cognitrix.session_ownership import OwnershipNotFound
        with pytest.raises(OwnershipNotFound):
            await require_owned(str(session.id), 'user-1', 'agent-1')


class _QueueRequest:
    def __init__(self, payload, manager):
        self._payload = payload
        self.state = SimpleNamespace(sse_manager=manager)
        self.json_calls = 0

    async def json(self):
        self.json_calls += 1
        return self._payload


async def test_sse_plumbing_authorizes_path_and_rejects_action_spoofing(tmp_path):
    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1')
    foreign = await _owned_session('user-2', 'agent-1')
    manager = SimpleNamespace(action_queue=asyncio.Queue())

    foreign_request = _QueueRequest({'message': '{}'}, manager)
    with pytest.raises(HTTPException) as exc_info:
        await routes.chat_endpoint(foreign_request, str(foreign.id), _ctx('user-1'))
    assert exc_info.value.status_code == 404
    assert foreign_request.json_calls == 0

    spoof = _QueueRequest({
        'message': json.dumps({
            'type': 'chat', 'session_id': str(foreign.id), 'agent_id': 'agent-1',
        })
    }, manager)
    with pytest.raises(HTTPException) as exc_info:
        await routes.chat_endpoint(spoof, str(own.id), _ctx('user-1'))
    assert exc_info.value.status_code == 404
    assert manager.action_queue.empty()

    valid = _QueueRequest({'message': json.dumps({'type': 'chat', 'content': 'hi'})}, manager)
    await routes.chat_endpoint(valid, str(own.id), _ctx('user-1'))
    queued = await manager.action_queue.get()
    assert queued['session_id'] == own.id
    assert queued['agent_id'] == 'agent-1'
