"""Chat turns keep running after the browser leaves, and can be reattached."""

import asyncio
import json
import types

import pytest

from cognitrix.sessions import runs as chat_runs
from cognitrix.utils import sse
from cognitrix.utils.sse import _SSE_MANAGERS, get_sse_manager


@pytest.fixture(autouse=True)
def _clean_registries():
    chat_runs._RUNS.clear()
    _SSE_MANAGERS.clear()
    yield
    chat_runs._RUNS.clear()
    _SSE_MANAGERS.clear()


async def _collect(run, limit=50):
    items = []
    async for item in run.follow(idle_timeout=0.05):
        if item is chat_runs.IDLE:
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


@pytest.mark.asyncio
async def test_text_chunks_merge_and_replay_as_one_event():
    run = chat_runs.start_run('u', 'a', 's1', prompt='hi')
    for chunk in ('Hel', 'lo ', 'there'):
        run.record({'type': 'generate', 'content': chunk, 'action': 'chat_message',
                    'complete': False, 'session_id': 's1'})
    run.record({'type': 'tool', 'status': 'started', 'tool_name': 'Search', 'session_id': 's1'})
    run.record({'type': 'generate', 'content': 'Done.', 'action': 'chat_message',
                'complete': False, 'session_id': 's1'})
    run.finish({'type': 'turn_complete', 'content': '', 'session_id': 's1'})

    assert len(run.events) == 4
    items = await _collect(run)
    assert [i['type'] for i in items] == ['generate', 'tool', 'generate', 'turn_complete']
    assert items[0]['content'] == 'Hello there'
    assert run.status == 'complete'
    assert chat_runs.active_runs('u') == []


@pytest.mark.asyncio
async def test_follower_gets_live_deltas_after_replay():
    run = chat_runs.start_run('u', 'a', 's1')
    run.record({'type': 'generate', 'content': 'A', 'session_id': 's1'})
    seen: list[str] = []

    async def follow():
        async for item in run.follow(idle_timeout=0.05):
            if item is chat_runs.IDLE:
                continue
            seen.append(item.get('content') or item['type'])

    task = asyncio.create_task(follow())
    await asyncio.sleep(0.01)
    run.record({'type': 'generate', 'content': 'B', 'session_id': 's1'})
    await asyncio.sleep(0.01)
    run.record({'type': 'generate', 'content': 'C', 'session_id': 's1'})
    await asyncio.sleep(0.01)
    run.finish({'type': 'turn_stopped', 'content': '', 'session_id': 's1'})
    await asyncio.wait_for(task, timeout=1)

    assert ''.join(s for s in seen if s != 'turn_stopped') == 'ABC'
    assert seen[-1] == 'turn_stopped'
    assert run.status == 'stopped'


@pytest.mark.asyncio
async def test_answered_question_is_not_replayed():
    run = chat_runs.start_run('u', 'a', 's1')
    run.record({'type': 'question_request', 'request_id': 'gone', 'session_id': 's1'})
    run.finish({'type': 'turn_complete', 'content': '', 'session_id': 's1'})
    items = await _collect(run)
    assert [i['type'] for i in items] == ['turn_complete']


def test_runs_are_scoped_to_their_user():
    chat_runs.start_run('alice', 'a', 's1')
    assert chat_runs.get_run('bob', 's1') is None
    assert chat_runs.active_runs('bob') == []
    assert [r.session_id for r in chat_runs.active_runs('alice', 'a')] == ['s1']
    assert chat_runs.active_runs('alice', 'other-agent') == []


def _manager_with_session(session, stream_id='browser-a'):
    agent = types.SimpleNamespace(id='agent1', name='A')
    manager = get_sse_manager('userA', 'agent1', agent, stream_id=stream_id)
    assert manager.begin_turn() is True

    async def resolve_session(_session_id):
        return session

    manager._resolve_session = resolve_session
    return manager


@pytest.mark.asyncio
async def test_turn_finishes_when_nobody_is_reading(monkeypatch):
    """The browser left mid-turn: output must not fill up and stall the turn."""
    monkeypatch.setattr(sse, '_SSE_QUEUE_MAXSIZE', 2)

    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    class Session:
        id = 'session-1'
        finished = False

        async def __call__(self, *args, output, **kwargs):
            for n in range(20):
                await output({'type': 'generate', 'content': f'{n} ',
                              'action': 'chat_message', 'complete': False})
                await asyncio.sleep(0)
            self.finished = True

        async def save(self):
            return None

    session = Session()
    manager = _manager_with_session(session)
    await manager.action_queue.put({
        'type': 'chat_message', 'content': 'count', 'session_id': session.id,
    })
    request = Request()
    response = await manager.sse_endpoint(request)
    events = response.body_iterator
    first = await asyncio.wait_for(anext(events), timeout=1)
    assert json.loads(first['data'])['type'] == 'generate'

    # User navigates away.
    request.disconnected = True
    await events.aclose()

    for _ in range(100):
        if session.finished and manager.active_task is None:
            break
        await asyncio.sleep(0.01)
    assert session.finished is True
    assert manager.turn_pending is False

    run = chat_runs.get_run('userA', session.id)
    assert run is not None and run.status == 'complete'
    items = await _collect(run)
    assert items[0]['content'] == ''.join(f'{n} ' for n in range(20))
    assert items[-1]['type'] == 'turn_complete'

    # Coming back on the same stream: told to replay from the run log.
    back = await manager.sse_endpoint(Request())
    resync = await asyncio.wait_for(anext(back.body_iterator), timeout=1)
    assert json.loads(resync['data']) == {'type': 'run_resync', 'session_id': session.id}
    await back.body_iterator.aclose()


@pytest.mark.asyncio
async def test_two_conversations_run_at_once_and_stop_by_session():
    import cognitrix.api.routes.agents as agent_routes

    gate = asyncio.Event()

    class Session:
        def __init__(self, sid):
            self.id = sid

        async def __call__(self, *args, output, **kwargs):
            await output({'type': 'generate', 'content': f'from {self.id}'})
            await gate.wait()

        async def save(self):
            return None

    class Request:
        async def is_disconnected(self):
            return False

    first = _manager_with_session(Session('s1'), stream_id='stream-1')
    second = _manager_with_session(Session('s2'), stream_id='stream-2')
    responses = []
    for manager, sid in ((first, 's1'), (second, 's2')):
        await manager.action_queue.put({'type': 'chat_message', 'content': 'go', 'session_id': sid})
        response = await manager.sse_endpoint(Request())
        responses.append(response)
        await asyncio.wait_for(anext(response.body_iterator), timeout=1)

    running = {r.session_id for r in chat_runs.active_runs('userA', 'agent1')}
    assert running == {'s1', 's2'}

    class JsonRequest:
        async def json(self):
            return {'session_id': 's1'}

    result = await agent_routes.stop_endpoint(
        JsonRequest(), user=types.SimpleNamespace(id='userA'),
    )
    assert result == {'status': 'stopping'}
    stopped = await asyncio.wait_for(anext(responses[0].body_iterator), timeout=1)
    assert json.loads(stopped['data'])['type'] == 'turn_stopped'
    assert {r.session_id for r in chat_runs.active_runs('userA')} == {'s2'}

    gate.set()
    done = await asyncio.wait_for(anext(responses[1].body_iterator), timeout=1)
    assert json.loads(done['data'])['type'] == 'turn_complete'
    for response in responses:
        await response.body_iterator.aclose()
