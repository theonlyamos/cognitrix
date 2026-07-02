"""H3.2: per-(user, agent) SSE managers isolate concurrent users.

Previously one shared SSEManager served every client, so one user's messages
and agent could leak into another's stream. get_sse_manager keys by
(user_id, agent_id).
"""

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
