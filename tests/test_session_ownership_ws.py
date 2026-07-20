import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect


class FakeWebSocket:
    def __init__(self, *queries):
        self.queries = list(queries)
        self.sent = []
        self.accepted = False
        self.closed = []

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if not self.queries:
            raise WebSocketDisconnect()
        return json.dumps(self.queries.pop(0))

    async def send_json(self, value):
        self.sent.append(value)

    async def close(self, code, reason):
        self.closed.append((code, reason))


async def _initialize_ws_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session

    db_file = str(tmp_path / 'session-ws.db')
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


def _manager(agent_id='agent-1'):
    from cognitrix.utils import ws

    manager = object.__new__(ws.WebSocketManager)
    manager.agent = SimpleNamespace(id=agent_id, name='Agent', llm=None)
    return manager


async def test_foreign_and_unbound_ws_ids_are_denied_before_session_load(tmp_path, monkeypatch):
    from cognitrix.sessions.base import Session

    await _initialize_ws_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1')
    foreign = await _owned_session('user-2', 'agent-1')
    legacy = Session(agent_id='agent-1')
    await legacy.save()

    loaded = []
    original_get = Session.get

    async def tracked_get(session_id):
        loaded.append(str(session_id))
        return await original_get(session_id)

    monkeypatch.setattr(Session, 'get', tracked_get)
    socket = FakeWebSocket(
        {'type': 'chat_history', 'action': 'get', 'session_id': str(foreign.id)},
        {'type': 'chat_history', 'action': 'get', 'session_id': str(legacy.id)},
    )
    await _manager().websocket_endpoint(socket, 'user-1')

    assert socket.accepted
    assert [item['content'] for item in socket.sent] == [
        'Session not found', 'Session not found',
    ]
    assert str(own.id) in loaded  # connection startup loads the owned session
    assert str(foreign.id) not in loaded
    assert str(legacy.id) not in loaded


async def test_ws_list_filters_to_owned_sessions(tmp_path):
    await _initialize_ws_db(tmp_path)
    own_a1 = await _owned_session('user-1', 'agent-1')
    own_a2 = await _owned_session('user-1', 'agent-2')
    await _owned_session('user-2', 'agent-1')
    socket = FakeWebSocket({'type': 'sessions', 'action': 'list'})

    await _manager().websocket_endpoint(socket, 'user-1')

    content = socket.sent[0]['content']
    assert {row['id'] for row in content} == {own_a1.id, own_a2.id}


async def test_ws_turn_binds_exact_tool_context_and_rejects_target_spoof(tmp_path, monkeypatch):
    from cognitrix.sessions.base import Session

    await _initialize_ws_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1')
    foreign = await _owned_session('user-2', 'agent-1')
    calls = []

    async def turn(self, message, agent, **kwargs):
        calls.append((str(self.id), message, str(agent.id), kwargs.get('tool_context')))

    monkeypatch.setattr(Session, '__call__', turn)
    socket = FakeWebSocket(
        {
            'type': 'chat', 'action': 'send', 'content': 'hello',
            'session_id': str(own.id), 'agent_id': 'agent-1', 'user_id': 'user-1',
        },
        {
            'type': 'chat', 'action': 'send', 'content': 'steal',
            'session_id': str(foreign.id), 'agent_id': 'agent-1', 'user_id': 'user-1',
        },
    )

    await _manager().websocket_endpoint(socket, 'user-1')

    assert len(calls) == 1
    session_id, message, agent_id, tool_context = calls[0]
    assert (session_id, message, agent_id) == (str(own.id), 'hello', 'agent-1')
    assert tool_context.user_id == 'user-1'
    assert tool_context.session_id == own.id
    assert tool_context.agent_id == 'agent-1'
    assert socket.sent[-1]['content'] == 'Session not found'


async def test_ws_multistep_message_type_is_authoritative(tmp_path, monkeypatch):
    from cognitrix.sessions.base import Session
    from cognitrix.utils import ws

    await _initialize_ws_db(tmp_path)
    own = await _owned_session('user-1', 'agent-1')
    calls = []

    async def forbidden_session(self, *_args, **_kwargs):
        pytest.fail('A multistep message must not fall back to direct chat')

    async def handle(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return 'task result'

    def forbidden_classifier(_prompt):
        raise AssertionError('WebSocket routing must not inspect prompt wording')

    monkeypatch.setattr(Session, '__call__', forbidden_session)
    monkeypatch.setattr(ws, 'handle_multi_step_task', handle)
    monkeypatch.setattr(ws, 'is_multi_step_task', forbidden_classifier, raising=False)
    socket = FakeWebSocket({
        'type': 'multistep',
        'prompt': 'hello',
        'session_id': str(own.id),
        'agent_id': 'agent-1',
    })

    await _manager().websocket_endpoint(socket, 'user-1')

    assert calls == ['hello']
    assert socket.sent[-1] == {'type': 'multistep_result', 'content': 'task result'}


async def test_ws_clear_and_delete_use_lifecycle_and_exact_cleanup(tmp_path, monkeypatch):
    from cognitrix.session_ownership import OwnershipNotFound, OwnershipState, require_owned
    from cognitrix.sessions.base import Session
    from cognitrix.utils import ws

    await _initialize_ws_db(tmp_path)
    clear_target = await _owned_session('user-1', 'agent-1', chat=[{
        'role': 'user', 'type': 'text', 'content': 'clear',
    }])
    delete_target = await _owned_session('user-1', 'agent-1')
    order = []

    async def cleanup(*, session_id, user_id, agent_id, generation):
        binding = await require_owned(session_id, user_id, agent_id)
        persisted = await Session.get(session_id)
        order.append((binding.state.value, session_id, generation, persisted is not None))
        if binding.state == OwnershipState.CLEARING:
            assert persisted.chat == []

    monkeypatch.setattr(ws, 'cleanup_owned_session_resources', cleanup, raising=False)
    clear_socket = FakeWebSocket({
        'type': 'chat_history', 'action': 'delete', 'session_id': str(clear_target.id),
    })
    await _manager().websocket_endpoint(clear_socket, 'user-1')
    assert (await Session.get(str(clear_target.id))).chat == []
    assert (await require_owned(str(clear_target.id), 'user-1', 'agent-1')).state == OwnershipState.ACTIVE

    delete_socket = FakeWebSocket({
        'type': 'sessions', 'action': 'delete', 'session_id': str(delete_target.id),
    })
    await _manager().websocket_endpoint(delete_socket, 'user-1')
    assert await Session.get(str(delete_target.id)) is None
    with pytest.raises(OwnershipNotFound):
        await require_owned(str(delete_target.id), 'user-1', 'agent-1')
    assert order == [
        ('clearing', str(clear_target.id), 1, True),
        ('deleting', str(delete_target.id), 1, True),
    ]


async def test_ws_owned_lookup_ignores_legacy_and_claims_fresh_session(tmp_path):
    from cognitrix.session_ownership import require_owned
    from cognitrix.sessions.base import Session
    from cognitrix.utils import ws

    await _initialize_ws_db(tmp_path)
    legacy = Session(agent_id='agent-1')
    await legacy.save()

    binding, session = await ws.get_or_create_owned_session('user-1', 'agent-1')

    assert session.id != legacy.id
    assert binding.session_id == session.id
    assert await require_owned(str(session.id), 'user-1', 'agent-1')


async def test_ws_fresh_session_save_cancellation_is_settled_then_compensated(
    tmp_path,
    monkeypatch,
):
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session
    from cognitrix.utils import ws

    await _initialize_ws_db(tmp_path)
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
    task = asyncio.create_task(
        ws.get_or_create_owned_session('user-1', 'agent-1'),
    )
    await committed.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()
    assert await Session.all() == []
    assert await SessionOwnership.all() == []


async def test_cli_dispatch_passes_stable_verified_user_key_and_rejects_invalid():
    from cognitrix.cli.ui import dispatch_verified_websocket

    calls = []

    class Manager:
        async def websocket_endpoint(self, websocket, user_key):
            calls.append((websocket, user_key))

    socket = FakeWebSocket()

    async def verify_id(_token):
        return SimpleNamespace(id='user-id', email='mail@example.com')

    await dispatch_verified_websocket(Manager(), socket, 'token', verify_id)
    assert calls == [(socket, 'user-id')]

    calls.clear()

    async def verify_email(_token):
        return SimpleNamespace(id=None, email='Mail@Example.COM')

    await dispatch_verified_websocket(Manager(), socket, 'token', verify_email)
    assert calls == [(socket, 'mail@example.com')]

    invalid = FakeWebSocket()

    async def reject(_token):
        return None

    await dispatch_verified_websocket(Manager(), invalid, 'bad', reject)
    assert invalid.closed == [(4003, 'Unauthorized')]


async def test_shared_ws_manager_routes_team_messages_to_originating_connection(
    tmp_path, monkeypatch
):
    from cognitrix.sessions.base import Session

    await _initialize_ws_db(tmp_path)
    await _owned_session('user-1', 'agent-1')
    await _owned_session('user-2', 'agent-1')
    manager = _manager()
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    first_sent = asyncio.Event()
    second_sent = asyncio.Event()

    async def turn(self, message, _agent, **_kwargs):
        if message == 'first':
            first_entered.set()
            await asyncio.wait_for(second_entered.wait(), timeout=1)
            await manager.send_team_message('agent-1', 'first-user', message)
            first_sent.set()
            await asyncio.wait_for(second_sent.wait(), timeout=1)
        else:
            second_entered.set()
            await asyncio.wait_for(first_entered.wait(), timeout=1)
            await asyncio.wait_for(first_sent.wait(), timeout=1)
            await manager.send_team_message('agent-1', 'second-user', message)
            second_sent.set()

    monkeypatch.setattr(Session, '__call__', turn)
    first = FakeWebSocket({
        'type': 'chat', 'action': 'send', 'content': 'first',
    })
    second = FakeWebSocket({
        'type': 'chat', 'action': 'send', 'content': 'second',
    })

    await asyncio.gather(
        manager.websocket_endpoint(first, 'user-1'),
        manager.websocket_endpoint(second, 'user-2'),
    )

    assert [item for item in first.sent if item['type'] == 'team_message'] == [{
        'type': 'team_message',
        'sender': 'agent-1',
        'receiver': 'first-user',
        'content': 'first',
    }]
    assert [item for item in second.sent if item['type'] == 'team_message'] == [{
        'type': 'team_message',
        'sender': 'agent-1',
        'receiver': 'second-user',
        'content': 'second',
    }]
