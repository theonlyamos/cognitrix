"""Call Agent saves its helper's work as a chat the person can find."""
from types import SimpleNamespace

from tests.test_session_ownership_routes import _ctx, _initialize_routes_db, _owned_session

from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


def _fake_turn(monkeypatch, seen):
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.sessions.base import Session

    async def turn(self, message, agent, interface, stream, output, wsquery, **kwargs):
        # The helper's chat is listed as running while it works.
        run = chat_runs.get_run('user-1', str(self.id))
        seen['running'] = bool(run and run.running)
        seen['kwargs'] = kwargs
        self.update_history({'role': 'user', 'type': 'text', 'content': message})
        self.update_history({'role': 'assistant', 'type': 'text', 'content': 'Draft ready.'})
        await output({'type': 'chat_reply', 'content': 'Draft ready.', 'action': 'chat_reply'})
        if kwargs.get('persist_history', True):
            await self.save()

    monkeypatch.setattr(Session, '__call__', turn)


async def _patch_target(monkeypatch):
    target = SimpleNamespace(id='agent-2', name='Writer')

    async def find(_user, **_kwargs):
        return target
    monkeypatch.setattr('cognitrix.agents.access.find_visible_agent', find)
    return target


async def test_call_agent_saves_a_chat_listed_under_the_helper(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.tools.misc import call_agent

    await _initialize_routes_db(tmp_path)
    await _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen)
    parent_chat = await _owned_session('user-1', 'agent-1', chat=[
        {'role': 'user', 'type': 'text', 'content': 'Get a draft written'}])
    before = (await routes.chats_version(_ctx('user-1')))['version']

    token = set_execution_context(ToolExecutionContext(
        session_id=str(parent_chat.id), user_id='user-1', agent_id='agent-1'))
    try:
        out = (await call_agent.run(
            name='Writer', task='Write the draft',
            parent=SimpleNamespace(id='agent-1', name='Planner'))).content
    finally:
        reset_execution_context(token)

    assert out == 'Draft ready.'
    assert seen['running'] is True
    assert (await routes.chats_version(_ctx('user-1')))['version'] != before
    listed = {c['id']: c for c in await routes.all_chats(_ctx('user-1'))}
    helper = [c for c in listed.values() if c['agent_id'] == 'agent-2']
    assert len(helper) == 1
    chat = helper[0]
    assert chat['title'] == 'Write the draft'
    assert chat['message_count'] == 2
    assert chat['started_by']['agent_name'] == 'Planner'
    assert chat['started_by']['session_id'] == str(parent_chat.id)
    assert chat['running'] is False
    assert chat_runs.get_run('user-1', chat['id']).status == 'complete'
    # Nobody else sees it.
    assert all(c['agent_id'] != 'agent-2' for c in await routes.all_chats(_ctx('user-2')))


async def test_call_agent_stays_unsaved_for_api_keys_and_incognito(tmp_path, monkeypatch):
    from cognitrix.sessions.base import Session
    from cognitrix.sessions.index import ChatIndex
    from cognitrix.tools.misc import call_agent

    await _initialize_routes_db(tmp_path)
    await _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen)
    secret = await _owned_session('user-1', 'agent-1', incognito=True)

    for context in (
        ToolExecutionContext(user_id='user-1', api_key_id='key-1'),
        ToolExecutionContext(user_id='user-1', session_id=str(secret.id), agent_id='agent-1'),
    ):
        token = set_execution_context(context)
        try:
            await call_agent.run(name='Writer', task='Private work')
        finally:
            reset_execution_context(token)
        assert seen['kwargs']['persist_history'] is False

    rows = await ChatIndex.find({'agent_id': 'agent-2'})
    assert list(rows) == []
    assert all(s.agent_id != 'agent-2' for s in await Session.all())
