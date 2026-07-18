import json
import types

import pytest
from starlette.websockets import WebSocketDisconnect

from cognitrix.session_ownership import LifecycleToken, OwnershipState
from cognitrix.utils import ws


class FakeWebSocket:
    def __init__(self, query):
        self.query = query
        self.received = False
        self.sent = []

    async def accept(self):
        return None

    async def receive_text(self):
        if self.received:
            raise WebSocketDisconnect()
        self.received = True
        return json.dumps(self.query)

    async def send_json(self, value):
        self.sent.append(value)


def _manager():
    manager = object.__new__(ws.WebSocketManager)
    manager.agent = types.SimpleNamespace(id='agent-1', name='Agent')
    return manager


@pytest.mark.asyncio
async def test_websocket_session_delete_cleans_artifacts_before_session_row(monkeypatch):
    order = []
    finished = []
    initial = types.SimpleNamespace(id='initial', agent_id='agent-1')
    binding = types.SimpleNamespace(
        session_id='initial', user_id='user-1', agent_id='agent-1',
    )
    token = LifecycleToken(
        binding_id='binding-1',
        session_id='session-1',
        user_id='user-1',
        agent_id='agent-1',
        state=OwnershipState.DELETING,
        generation=1,
        version=1,
    )

    async def initialize(user_key, agent_id):
        assert (user_key, agent_id) == ('user-1', 'agent-1')
        return binding, initial

    async def begin(session_id, user_key, operation):
        assert (session_id, user_key, operation) == (
            'session-1', 'user-1', 'delete',
        )
        return token, types.SimpleNamespace(agent_id='agent-1'), False

    async def cleanup(*, session_id, user_id, agent_id, generation):
        assert (user_id, agent_id, generation) == ('user-1', 'agent-1', 1)
        order.append(('cleanup', session_id))

    async def delete_many(query):
        order.append(('delete-many', query))
        return 1

    async def finish(received):
        finished.append(received)

    monkeypatch.setattr(ws, 'get_or_create_owned_session', initialize)
    monkeypatch.setattr(ws, '_begin_lifecycle', begin)
    monkeypatch.setattr(ws, 'cleanup_owned_session_resources', cleanup)
    monkeypatch.setattr(ws.Session, 'delete_many', delete_many)
    monkeypatch.setattr(ws, 'finish_delete', finish)
    monkeypatch.setattr(ws, '_owned_sessions', lambda _key: _return([]))
    socket = FakeWebSocket({
        'type': 'sessions', 'action': 'delete', 'session_id': 'session-1'
    })

    await _manager().websocket_endpoint(socket, 'user-1')

    assert order == [
        ('cleanup', 'session-1'),
        ('delete-many', {'id': 'session-1'}),
    ]
    assert finished == [token]


@pytest.mark.asyncio
async def test_websocket_chat_clear_cleans_artifacts_after_history_save(monkeypatch):
    order = []
    finished = []

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'
        chat = ['message']

        async def save(self):
            order.append(('save', list(self.chat)))

    session = Session()
    binding = types.SimpleNamespace(
        session_id='session-1', user_id='user-1', agent_id='agent-1',
    )
    token = LifecycleToken(
        binding_id='binding-1',
        session_id='session-1',
        user_id='user-1',
        agent_id='agent-1',
        state=OwnershipState.CLEARING,
        generation=1,
        version=1,
    )

    async def initialize(user_key, agent_id):
        assert (user_key, agent_id) == ('user-1', 'agent-1')
        return binding, session

    async def load(session_id, user_key, *, agent_id=None):
        assert (session_id, user_key, agent_id) == (
            'session-1', 'user-1', None,
        )
        return binding, session

    async def begin(session_id, user_key, operation):
        assert (session_id, user_key, operation) == (
            'session-1', 'user-1', 'clear',
        )
        return token, session, False

    async def cleanup(*, session_id, user_id, agent_id, generation):
        assert (user_id, agent_id, generation) == ('user-1', 'agent-1', 1)
        order.append(('cleanup', session_id))

    async def finish(received):
        finished.append(received)

    monkeypatch.setattr(ws, 'get_or_create_owned_session', initialize)
    monkeypatch.setattr(ws, '_load_owned_session', load)
    monkeypatch.setattr(ws, '_begin_lifecycle', begin)
    monkeypatch.setattr(ws, 'cleanup_owned_session_resources', cleanup)
    monkeypatch.setattr(ws, 'finish_clear', finish)
    socket = FakeWebSocket({
        'type': 'chat_history', 'action': 'delete', 'session_id': 'session-1'
    })

    await _manager().websocket_endpoint(socket, 'user-1')

    assert order == [('save', []), ('cleanup', 'session-1')]
    assert finished == [token]


async def _return(value):
    return value
