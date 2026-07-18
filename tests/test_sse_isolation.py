"""H3.2: per-(user, agent) SSE managers isolate concurrent users.

Previously one shared SSEManager served every client, so one user's messages
and agent could leak into another's stream. get_sse_manager keys by
(user_id, agent_id).
"""

import asyncio
import json
import types

import pytest

from cognitrix.utils.sse import _SSE_MANAGERS, get_sse_manager


def _agent(aid):
    return types.SimpleNamespace(id=aid, name="A")


def test_different_users_get_different_managers():
    _SSE_MANAGERS.clear()
    agent = _agent("agent1")
    m_a = get_sse_manager("userA", "agent1", agent)
    m_b = get_sse_manager("userB", "agent1", agent)
    assert m_a is not m_b
    assert m_a.action_queue is not m_b.action_queue


def test_same_key_returns_same_manager():
    _SSE_MANAGERS.clear()
    agent = _agent("agent1")
    assert get_sse_manager("userA", "agent1", agent) is get_sse_manager("userA", "agent1", agent)


def test_same_user_and_agent_get_different_managers_per_browser_stream():
    _SSE_MANAGERS.clear()
    agent = _agent("agent1")
    m_a = get_sse_manager("userA", "agent1", agent, stream_id="browser-a")
    m_b = get_sse_manager("userA", "agent1", agent, stream_id="browser-b")
    assert m_a is not m_b
    assert m_a.action_queue is not m_b.action_queue


@pytest.mark.asyncio
async def test_queues_are_isolated():
    _SSE_MANAGERS.clear()
    agent = _agent("agent1")
    m_a = get_sse_manager("userA", "agent1", agent)
    m_b = get_sse_manager("userB", "agent1", agent)
    await m_a.action_queue.put({"type": "chat_message", "content": "for A"})
    assert m_a.action_queue.qsize() == 1
    assert m_b.action_queue.qsize() == 0


def test_manager_registry_is_bounded(monkeypatch):
    from cognitrix.utils import sse

    sse._SSE_MANAGERS.clear()
    monkeypatch.setattr(sse, "_MAX_SSE_MANAGERS", 3)
    agent = _agent("agent1")
    for i in range(6):
        sse.get_sse_manager(f"user{i}", "agent1", agent)
    assert len(sse._SSE_MANAGERS) <= 3


def test_registry_pressure_evicts_idle_manager_not_running_turn(monkeypatch):
    from cognitrix.utils import sse

    sse._SSE_MANAGERS.clear()
    monkeypatch.setattr(sse, "_MAX_SSE_MANAGERS", 2)
    agent = _agent("agent1")
    running = sse.get_sse_manager("active", "agent1", agent, stream_id="run")
    assert running.begin_turn() is True
    idle = sse.get_sse_manager("idle", "agent1", agent, stream_id="idle")

    replacement = sse.get_sse_manager("new", "agent1", agent, stream_id="new")

    assert sse._SSE_MANAGERS[("active", "agent1", "run")] is running
    assert ("idle", "agent1", "idle") not in sse._SSE_MANAGERS
    assert sse._SSE_MANAGERS[("new", "agent1", "new")] is replacement
    assert len(sse._SSE_MANAGERS) == 2


def test_registry_refuses_new_manager_when_every_slot_is_active(monkeypatch):
    from cognitrix.utils import sse

    sse._SSE_MANAGERS.clear()
    monkeypatch.setattr(sse, "_MAX_SSE_MANAGERS", 2)
    agent = _agent("agent1")
    first = sse.get_sse_manager("user-a", "agent1", agent, stream_id="one")
    second = sse.get_sse_manager("user-b", "agent1", agent, stream_id="two")
    assert first.begin_turn() is True
    assert second.begin_turn() is True

    with pytest.raises(sse.SSEManagerCapacityError):
        sse.get_sse_manager("user-c", "agent1", agent, stream_id="three")

    assert len(sse._SSE_MANAGERS) == 2


def test_registry_caps_active_streams_per_user(monkeypatch):
    from cognitrix.utils import sse

    sse._SSE_MANAGERS.clear()
    monkeypatch.setattr(sse, "_MAX_SSE_MANAGERS", 10)
    monkeypatch.setattr(sse, "_MAX_SSE_MANAGERS_PER_USER", 2)
    agent = _agent("agent1")
    first = sse.get_sse_manager("user-a", "agent1", agent, stream_id="one")
    second = sse.get_sse_manager("user-a", "agent1", agent, stream_id="two")
    assert first.begin_turn() is True
    assert second.begin_turn() is True

    with pytest.raises(sse.SSEManagerCapacityError):
        sse.get_sse_manager("user-a", "agent1", agent, stream_id="three")

    other = sse.get_sse_manager("user-b", "agent1", agent, stream_id="one")
    assert other is not None
    assert len(sse._SSE_MANAGERS) == 3


@pytest.mark.asyncio
async def test_new_connection_supersedes_old_consumer_for_same_stream(monkeypatch):
    from cognitrix.utils import sse

    _SSE_MANAGERS.clear()

    class Request:
        async def is_disconnected(self):
            return False

    async def list_sessions():
        return []

    async def owned_session_ids(*_args, **_kwargs):
        return set()

    monkeypatch.setattr(sse.Session, "list_sessions", list_sessions)
    monkeypatch.setattr(
        sse.session_ownerships, "owned_session_ids", owned_session_ids
    )
    manager = get_sse_manager(
        "userA", "agent1", _agent("agent1"), stream_id="browser-a"
    )
    old_response = await manager.sse_endpoint(Request())
    old_next = asyncio.create_task(anext(old_response.body_iterator))
    await asyncio.sleep(0)

    new_response = await manager.sse_endpoint(Request())
    new_next = asyncio.create_task(anext(new_response.body_iterator))
    await asyncio.sleep(0)
    await manager.action_queue.put({"type": "sessions", "action": "list"})

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(old_next, timeout=1)
    event = await asyncio.wait_for(new_next, timeout=1)
    assert json.loads(event["data"]) == {
        "type": "sessions",
        "content": [],
        "action": "list",
    }
    await new_response.body_iterator.aclose()


def test_stop_request_is_recorded_before_the_turn_task_starts():
    _SSE_MANAGERS.clear()
    manager = get_sse_manager("userA", "agent1", _agent("agent1"), stream_id="browser-a")

    assert manager.begin_turn() is True
    assert manager.stop_current_turn() is True
    assert manager.stop_requested is True
    assert manager.begin_turn() is False

    manager.finish_turn()
    assert manager.stop_current_turn() is False


@pytest.mark.asyncio
async def test_stop_current_turn_cancels_only_that_streams_active_task():
    _SSE_MANAGERS.clear()
    manager = get_sse_manager("userA", "agent1", _agent("agent1"), stream_id="browser-a")
    other = get_sse_manager("userA", "agent1", _agent("agent1"), stream_id="browser-b")
    manager.begin_turn()
    other.begin_turn()
    manager.active_task = asyncio.create_task(asyncio.Event().wait())
    other.active_task = asyncio.create_task(asyncio.Event().wait())

    assert manager.stop_current_turn() is True
    with pytest.raises(asyncio.CancelledError):
        await manager.active_task
    assert other.active_task.cancelled() is False

    other.active_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await other.active_task


@pytest.mark.asyncio
async def test_active_sse_turn_emits_stopped_without_closing_the_stream(monkeypatch):
    from cognitrix.utils import sse

    _SSE_MANAGERS.clear()

    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    class Session:
        id = "session-1"

        async def __call__(self, *args, output, **kwargs):
            await output({"type": "generate", "content": "partial"})
            await asyncio.Event().wait()

    manager = get_sse_manager("userA", "agent1", _agent("agent1"), stream_id="browser-a")
    manager.begin_turn()
    session = Session()

    async def resolve_session(_session_id):
        return session

    manager._resolve_session = resolve_session
    monkeypatch.setattr(sse, "is_multi_step_task", lambda _prompt: False)
    await manager.action_queue.put({
        "type": "chat_message",
        "content": "hello",
        "session_id": session.id,
    })
    request = Request()
    response = await manager.sse_endpoint(request)
    events = response.body_iterator

    first = await asyncio.wait_for(anext(events), timeout=1)
    assert json.loads(first["data"])["type"] == "generate"
    assert manager.active_task is not None
    assert manager.stop_current_turn() is True

    stopped = await asyncio.wait_for(anext(events), timeout=1)
    assert json.loads(stopped["data"]) == {
        "type": "turn_stopped",
        "content": "",
        "session_id": session.id,
    }
    assert manager.turn_pending is False

    request.disconnected = True
    await events.aclose()


@pytest.mark.asyncio
async def test_reconnect_keeps_active_task_stoppable_after_disconnect(monkeypatch):
    from cognitrix.utils import sse

    _SSE_MANAGERS.clear()

    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    class Session:
        id = "session-1"

        async def __call__(self, *args, output, **kwargs):
            await output({"type": "generate", "content": "partial"})
            await asyncio.Event().wait()

        async def save(self):
            return None

    manager = get_sse_manager(
        "userA", "agent1", _agent("agent1"), stream_id="browser-a"
    )
    assert manager.begin_turn() is True
    session = Session()

    async def resolve_session(_session_id):
        return session

    manager._resolve_session = resolve_session
    monkeypatch.setattr(sse, "is_multi_step_task", lambda _prompt: False)
    await manager.action_queue.put({
        "type": "chat_message",
        "content": "hello",
        "session_id": session.id,
    })
    first_request = Request()
    first_response = await manager.sse_endpoint(first_request)
    first_events = first_response.body_iterator
    partial = await asyncio.wait_for(anext(first_events), timeout=1)
    assert json.loads(partial["data"])["type"] == "generate"
    active_task = manager.active_task
    assert active_task is not None

    first_request.disconnected = True
    await first_events.aclose()

    assert manager.active_task is active_task
    assert active_task.done() is False
    assert manager.stop_current_turn() is True

    second_request = Request()
    second_response = await manager.sse_endpoint(second_request)
    stopped = await asyncio.wait_for(anext(second_response.body_iterator), timeout=1)
    assert json.loads(stopped["data"]) == {
        "type": "turn_stopped",
        "content": "",
        "session_id": session.id,
    }
    await second_response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_late_stop_cannot_wedge_completed_turn_with_full_output_queue(monkeypatch):
    from cognitrix.utils import sse

    _SSE_MANAGERS.clear()
    monkeypatch.setattr(sse, "_SSE_QUEUE_MAXSIZE", 1)
    monkeypatch.setattr(sse, "is_multi_step_task", lambda _prompt: False)

    class Request:
        async def is_disconnected(self):
            return False

    class Session:
        id = "session-1"

        async def __call__(self, *args, output, **kwargs):
            await output({"type": "generate", "content": "partial"})

        async def save(self):
            return None

    manager = get_sse_manager(
        "userA", "agent1", _agent("agent1"), stream_id="browser-a"
    )
    assert manager.begin_turn() is True
    session = Session()

    async def resolve_session(_session_id):
        return session

    manager._resolve_session = resolve_session
    await manager.action_queue.put({
        "type": "chat_message",
        "content": "hello",
        "session_id": session.id,
    })
    response = await manager.sse_endpoint(Request())
    events = response.body_iterator

    partial = await asyncio.wait_for(anext(events), timeout=1)
    assert json.loads(partial["data"])["type"] == "generate"
    await asyncio.sleep(0)

    # The old implementation remained cancellable while blocked on the
    # sentinel put. A late stop then skipped finish_turn() forever.
    manager.stop_current_turn()
    terminal = await asyncio.wait_for(anext(events), timeout=1)

    assert json.loads(terminal["data"])["type"] == "turn_complete"
    assert manager.turn_pending is False
    assert manager.active_task is None
    await events.aclose()


@pytest.mark.asyncio
async def test_superseded_consumer_replays_item_without_queue_capacity():
    from cognitrix.utils import sse

    class Request:
        async def is_disconnected(self):
            return False

    manager = sse.SSEManager(_agent("agent1"))
    queue = asyncio.Queue(maxsize=1)
    superseded = asyncio.Event()
    stale_get = asyncio.create_task(
        manager._next_queue_item(queue, Request(), superseded)
    )

    while not queue._getters:
        await asyncio.sleep(0)
    queue.put_nowait("first")
    while not queue.empty():
        await asyncio.sleep(0)
    queue.put_nowait("second")
    superseded.set()

    assert await stale_get is sse._CONSUMER_GONE
    current = asyncio.Event()
    assert await manager._next_queue_item(queue, Request(), current) == "first"
    assert await manager._next_queue_item(queue, Request(), current) == "second"


@pytest.mark.asyncio
async def test_completed_output_gets_bounded_reconnect_grace(monkeypatch):
    from cognitrix.utils import sse

    _SSE_MANAGERS.clear()
    monkeypatch.setattr(sse, "_MAX_SSE_MANAGERS", 1)
    monkeypatch.setattr(sse, "_SSE_RECONNECT_GRACE_SECONDS", 30.0, raising=False)
    monkeypatch.setattr(sse, "is_multi_step_task", lambda _prompt: False)

    class Request:
        async def is_disconnected(self):
            return False

    started = asyncio.Event()
    release = asyncio.Event()

    class Session:
        id = "session-1"

        async def __call__(self, *args, output, **kwargs):
            started.set()
            await release.wait()

    agent = _agent("agent1")
    manager = get_sse_manager("userA", "agent1", agent, stream_id="browser-a")
    assert manager.begin_turn() is True
    session = Session()

    async def resolve_session(_session_id):
        return session

    manager._resolve_session = resolve_session
    await manager.action_queue.put({
        "type": "chat_message",
        "content": "hello",
        "session_id": session.id,
    })
    response = await manager.sse_endpoint(Request())
    pending_event = asyncio.create_task(anext(response.body_iterator))
    await asyncio.wait_for(started.wait(), timeout=1)
    active_task = manager.active_task
    assert active_task is not None
    pending_event.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_event

    release.set()
    await asyncio.wait_for(active_task, timeout=1)
    assert manager.turn_pending is False

    with pytest.raises(sse.SSEManagerCapacityError):
        get_sse_manager("userB", "agent1", agent, stream_id="browser-b")

    manager.completed_output_at -= sse._SSE_RECONNECT_GRACE_SECONDS + 1
    replacement = get_sse_manager(
        "userB", "agent1", agent, stream_id="browser-b"
    )
    assert replacement is not None
    assert ("userA", "agent1", "browser-a") not in _SSE_MANAGERS


@pytest.mark.asyncio
async def test_cancelled_consumer_waiting_for_lock_releases_claim():
    from cognitrix.utils import sse

    class Request:
        async def is_disconnected(self):
            return False

    manager = sse.SSEManager(_agent("agent1"))
    await manager._consumer_lock.acquire()
    response = await manager.sse_endpoint(Request())
    waiting = asyncio.create_task(anext(response.body_iterator))
    await asyncio.sleep(0)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    manager._consumer_lock.release()

    assert manager._consumer_claimed is False
    assert manager.is_idle is True
