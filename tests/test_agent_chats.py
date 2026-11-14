"""Agents starting chats: Start Chat, Check Chat and Message Chat."""
import asyncio
from types import SimpleNamespace

import pytest

from cognitrix.tools.utils import ToolExecutionContext, set_execution_context


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'agent_chats.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield
    set_execution_context(ToolExecutionContext())


BOSS = SimpleNamespace(id='a-boss', name='Boss')
HELPER = SimpleNamespace(id='a-helper', name='Helper')
AGENTS = {a.id: a for a in (BOSS, HELPER)}


@pytest.fixture
def world(monkeypatch):
    """Fake agents and a fake model: each turn replies "<agent> got: <text>"."""
    import cognitrix.agents.access as access
    from cognitrix.sessions.base import Session

    turns: list[tuple[str, str, str]] = []

    async def find(user_key, *, agent_id=None, name=None):
        if agent_id in AGENTS:
            return AGENTS[agent_id]
        return next((a for a in AGENTS.values() if a.name == name), None)

    async def fake_call(self, text, agent, *args, **kwargs):
        turns.append((str(self.id), agent.name, text))
        self.chat = [*self.chat, {'role': 'user', 'content': text},
                     {'role': 'assistant', 'content': f'{agent.name} got: {text[-20:]}'}]
        await kwargs['output']({'type': 'generate', 'content': 'ok'})
        await Session.update_one({'id': str(self.id)}, {'chat': self.chat})

    monkeypatch.setattr(access, 'find_visible_agent', find)
    monkeypatch.setattr(Session, '__call__', fake_call)
    monkeypatch.setattr('cognitrix.sessions.agent_chats.REPORT_POLL_SECONDS', 0.01)
    return turns


async def _parent(user='u1', **kw):
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions.base import Session

    session = Session(agent_id=BOSS.id, user_id=user, **kw)
    await session.save()
    await claim_new(str(session.id), user, BOSS.id)
    set_execution_context(ToolExecutionContext(user_id=user, session_id=str(session.id), agent_id=BOSS.id))
    return session


async def _call(tool, **params):
    return (await tool.run(parent=BOSS, **params)).outcome


async def _settle():
    from cognitrix.sessions import agent_chats

    for _ in range(200):
        if not agent_chats._TASKS:
            return
        await asyncio.sleep(0.01)
    raise AssertionError('background chats did not finish')


def _chat_id(outcome) -> str:
    return outcome.entities[0].id


async def test_start_chat_runs_in_background_and_reports_back(db, world):
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.automation import unread_notifications
    from cognitrix.tools.chats import start_chat

    parent = await _parent()
    outcome = await _call(start_chat, message='find the release date', agent='Helper')
    assert outcome.status == 'success', outcome.text
    chat_id = _chat_id(outcome)
    await _settle()

    child = await Session.get(chat_id)
    assert child.user_id == 'u1' and child.agent_id == HELPER.id
    assert child.started_by['agent_id'] == BOSS.id
    assert child.started_by['session_id'] == str(parent.id)
    assert child.started_by['depth'] == 1

    # The helper got the message, then its reply went back to the boss's chat.
    assert world[0][:2] == (chat_id, 'Helper')
    assert world[0][2].startswith('Boss (agent) started this chat:')
    assert world[1][:2] == (str(parent.id), 'Boss')
    assert 'Helper (agent) replied' in world[1][2] and chat_id in world[1][2]

    alerts = await unread_notifications('u1')
    assert [a.session_id for a in alerts] == [str(parent.id)]


async def test_start_chat_with_yourself_without_report_back_alerts_when_done(db, world):
    from cognitrix.tasks.automation import unread_notifications
    from cognitrix.tools.chats import check_chat, start_chat

    await _parent()
    outcome = await _call(start_chat, message='draft the summary', report_back=False)
    chat_id = _chat_id(outcome)
    await _settle()
    assert [t[1] for t in world] == ['Boss']  # nothing sent back
    alerts = await unread_notifications('u1')
    assert [a.session_id for a in alerts] == [chat_id]
    assert 'Boss finished a chat' == alerts[0].title

    checked = await _call(check_chat, chat_id=chat_id)
    assert 'is done' in checked.text and 'Boss got:' in checked.text


async def test_message_chat_sends_more_and_is_limited(db, world, monkeypatch):
    from cognitrix.sessions import agent_chats
    from cognitrix.tools.chats import message_chat, start_chat

    monkeypatch.setattr(agent_chats, 'MAX_EXCHANGES', 2)
    await _parent()
    chat_id = _chat_id(await _call(start_chat, message='hi', agent='Helper', report_back=False))
    await _settle()

    sent = await _call(message_chat, chat_id=chat_id, message='one more thing')
    assert sent.status == 'success', sent.text
    await _settle()
    assert world[-1][0] == chat_id and world[-1][2].endswith('one more thing')

    capped = await _call(message_chat, chat_id=chat_id, message='and another')
    assert capped.error.code == 'too_many_messages'


async def test_chats_are_private_to_the_agent_and_person_that_started_them(db, world):
    from cognitrix.tools.chats import check_chat, message_chat, start_chat

    await _parent()
    chat_id = _chat_id(await _call(start_chat, message='hi', agent='Helper', report_back=False))
    await _settle()

    # Another agent of the same person.
    other = (await message_chat.run(parent=HELPER, chat_id=chat_id, message='x')).outcome
    assert other.error.code == 'chat_not_found'
    # Another person.
    await _parent(user='u2')
    assert (await _call(check_chat, chat_id=chat_id)).error.code == 'chat_not_found'


async def test_refused_without_a_person_in_task_runs_too_deep_and_unknown_agent(db, world):
    from cognitrix.tools.chats import start_chat

    set_execution_context(ToolExecutionContext())
    assert (await _call(start_chat, message='x')).error.code == 'no_owner'
    set_execution_context(ToolExecutionContext(user_id='u1', task_id='t1', run_id='r1'))
    assert (await _call(start_chat, message='x')).error.code == 'not_in_task'
    set_execution_context(ToolExecutionContext(user_id='u1', api_key_id='k1'))
    assert (await _call(start_chat, message='x')).error.code == 'not_allowed'

    await _parent(started_by={'agent_id': BOSS.id, 'depth': 2})
    assert (await _call(start_chat, message='x')).error.code == 'too_deep'

    await _parent()
    assert (await _call(start_chat, message='x', agent='Nobody')).error.code == 'agent_not_found'
    assert (await _call(start_chat, message='  ')).error.code == 'message_required'
    assert world == []


async def test_running_agent_chats_are_capped(db, world, monkeypatch):
    from cognitrix.sessions import agent_chats
    from cognitrix.tools.chats import start_chat

    monkeypatch.setattr(agent_chats, 'running_agent_chats', lambda _u: agent_chats.MAX_RUNNING_PER_USER)
    await _parent()
    assert (await _call(start_chat, message='x')).error.code == 'too_many_chats'


async def test_chat_list_says_who_started_a_chat(db, world):
    from cognitrix.api.routes.sessions import _session_summary
    from cognitrix.sessions.base import Session
    from cognitrix.tools.chats import start_chat

    parent = await _parent()
    chat_id = _chat_id(await _call(start_chat, message='hi', agent='Helper', report_back=False))
    await _settle()
    summary = _session_summary(await Session.get(chat_id))
    assert summary['started_by'] == {'agent_id': BOSS.id, 'agent_name': 'Boss', 'session_id': str(parent.id)}
    assert _session_summary(await Session.get(parent.id))['started_by'] is None


def test_start_chat_does_not_ask_for_approval():
    from cognitrix.safety.destructive_ops import _KNOWN_LOW

    assert {'start_chat', 'check_chat', 'message_chat'} <= _KNOWN_LOW


async def test_replies_to_the_same_chat_are_delivered_one_at_a_time(db, world, monkeypatch):
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    real_get = Session.get.__func__

    async def slow_get(cls, *args, **kwargs):
        # Give the other delivery a chance to interleave.
        await asyncio.sleep(0.02)
        return await real_get(cls, *args, **kwargs)

    monkeypatch.setattr(Session, 'get', classmethod(slow_get))

    parent = await _parent()
    children = []
    for _ in range(2):
        child = await agent_chats.create_chat('u1', HELPER, {
            'agent_id': BOSS.id, 'agent_name': 'Boss', 'session_id': str(parent.id),
            'depth': 1, 'report_back': True, 'exchanges': 1})
        children.append(child)
    await asyncio.gather(*(agent_chats.report_back('u1', HELPER, c, 'complete', 'done') for c in children))
    await _settle()
    saved = await Session.get(parent.id)
    # Both replies made it into the history: the turns did not overwrite each other.
    delivered = [e for e in saved.chat if e['role'] == 'user']
    assert len(delivered) == 2
    assert {str(c.id) for c in children} == {
        next(str(c.id) for c in children if str(c.id) in e['content']) for e in delivered}


async def test_agents_pause_after_many_replies_in_a_row(db, world, monkeypatch):
    from cognitrix.sessions import agent_chats
    from cognitrix.tasks.automation import unread_notifications

    monkeypatch.setattr(agent_chats, 'MAX_EXCHANGES', 2)
    parent = await _parent(chat=[
        {'role': 'User', 'content': 'please research'},
        {'role': 'User', 'content': agent_chats.REPORT_PREFIX + 'one'},
        {'role': 'User', 'content': agent_chats.REPORT_PREFIX + 'two'},
    ])
    assert agent_chats.reports_since_person(parent) == 2
    child = await agent_chats.create_chat('u1', HELPER, {
        'agent_id': BOSS.id, 'agent_name': 'Boss', 'session_id': str(parent.id),
        'depth': 1, 'report_back': True, 'exchanges': 1})
    await agent_chats.report_back('u1', HELPER, child, 'complete', 'more')
    await _settle()
    assert world == []
    assert [a.title for a in await unread_notifications('u1')] == ['Helper replied to Boss']


async def test_report_limit_holds_while_the_parent_is_busy(db, world, monkeypatch):
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.tasks.automation import unread_notifications

    monkeypatch.setattr(agent_chats, 'MAX_EXCHANGES', 2)
    parent = await _parent(chat=[
        {'role': 'User', 'content': 'please research'},
        {'role': 'User', 'content': agent_chats.REPORT_PREFIX + 'one'},
    ])
    # The parent's running turn began with a report and has another waiting.
    run = chat_runs.start_run('u1', BOSS.id, str(parent.id), prompt=agent_chats.REPORT_PREFIX + 'two')
    run.steerable = True
    try:
        child = await agent_chats.create_chat('u1', HELPER, {
            'agent_id': BOSS.id, 'agent_name': 'Boss', 'session_id': str(parent.id),
            'depth': 1, 'report_back': True, 'exchanges': 1})
        await agent_chats.report_back('u1', HELPER, child, 'complete', 'more')
        assert run.inbox == []
        assert [a.title for a in await unread_notifications('u1')] == ['Helper replied to Boss']
    finally:
        run.finish({'type': 'turn_complete', 'content': '', 'session_id': str(parent.id)})
