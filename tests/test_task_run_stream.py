from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sse_starlette.sse import EventSourceResponse

from cognitrix.common.security import AuthContext


class RequestStub:
    def __init__(self, last_event_id=None):
        self.headers = {'last-event-id': last_event_id} if last_event_id else {}
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def jwt_ctx():
    return AuthContext(user=SimpleNamespace(id='user-1'), api_key=None)


def test_event_cursor_uses_greatest_non_negative_value():
    from cognitrix.api.routes.tasks import _event_cursor

    assert _event_cursor(RequestStub('7'), 3) == 7
    assert _event_cursor(RequestStub('bad'), 4) == 4
    assert _event_cursor(RequestStub('-2'), -9) == 0


@pytest.mark.asyncio
async def test_stream_route_hides_task_run_mismatch(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    async def task_get(_id):
        return SimpleNamespace(id='task-1', team_id=None, assigned_agents=[])

    async def run_get(_id):
        return SimpleNamespace(id='run-1', task_id='different-task')

    monkeypatch.setattr(routes.Task, 'get', staticmethod(task_get))
    monkeypatch.setattr(routes.TaskRun, 'get', staticmethod(run_get))

    with pytest.raises(HTTPException) as exc:
        await routes.stream_task_run_events(
            RequestStub(), 'task-1', 'run-1', 0, jwt_ctx()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_replays_ordered_events_then_stops_at_terminal(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    rows = [
        SimpleNamespace(
            id='e2', run_id='run-1', session_id='s', step_index=0,
            sequence=2, kind='text_delta', agent_name='A',
            data={'turn_id': 's:1', 'attempt': 1, 'content': 'two'},
            json=lambda: {'created_at': '2026-07-11 00:00:02'},
        ),
        SimpleNamespace(
            id='e1', run_id='run-1', session_id='s', step_index=0,
            sequence=1, kind='text_delta', agent_name='A',
            data={'turn_id': 's:1', 'attempt': 1, 'content': 'one'},
            json=lambda: {'created_at': '2026-07-11 00:00:01'},
        ),
    ]
    calls = 0

    async def event_rows(_run_id, after):
        nonlocal calls
        calls += 1
        return [row for row in sorted(rows, key=lambda item: item.sequence)
                if row.sequence > after] if calls == 1 else []

    async def run_get(_id):
        return SimpleNamespace(status=routes.TaskRunStatus.COMPLETED)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(routes, 'events_after', event_rows)
    monkeypatch.setattr(routes.TaskRun, 'get', staticmethod(run_get))
    monkeypatch.setattr(routes.asyncio, 'sleep', no_sleep)

    stream = routes._task_run_event_stream(
        RequestStub(), 'run-1', 0, poll_interval=0
    )
    first = await anext(stream)
    second = await anext(stream)
    assert [first['id'], second['id']] == ['1', '2']
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_stream_route_returns_event_source(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = SimpleNamespace(id='task-1', team_id=None, assigned_agents=[])
    run = SimpleNamespace(
        id='run-1',
        task_id='task-1',
        acl_version=1,
        acl_team_id=None,
        acl_agent_ids=[],
    )

    async def task_get(_id):
        return task

    async def run_get(_id):
        return run

    monkeypatch.setattr(routes.Task, 'get', staticmethod(task_get))
    monkeypatch.setattr(routes.TaskRun, 'get', staticmethod(run_get))

    response = await routes.stream_task_run_events(
        RequestStub(), 'task-1', 'run-1', 0, jwt_ctx()
    )
    assert isinstance(response, EventSourceResponse)
    assert response.ping_interval == 15
