"""The chat list reads small index rows, not whole sessions."""
from tests.test_session_ownership_routes import _ctx, _initialize_routes_db, _owned_session


def _chat(text):
    return [{'role': 'user', 'type': 'text', 'content': text},
            {'role': 'assistant', 'type': 'text', 'content': 'Sure.'}]


async def test_list_comes_from_the_index_without_loading_sessions(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    first = await _owned_session('user-1', 'agent-1', chat=_chat('Plan the launch'))
    await _owned_session('user-1', 'agent-1', chat=_chat('Secret'), incognito=True)
    await _owned_session('user-2', 'agent-1', chat=_chat('Not yours'))
    ctx = _ctx('user-1')
    await routes.all_chats(ctx)  # indexes anything missing

    async def boom(*args, **kwargs):
        raise AssertionError('the chat list loaded a session')
    monkeypatch.setattr(Session, 'find', boom)
    monkeypatch.setattr(Session, 'get', boom)
    monkeypatch.setattr(Session, 'all', boom)

    listed = await routes.all_chats(ctx)
    assert [(c['id'], c['title'], c['message_count']) for c in listed] == [(str(first.id), 'Plan the launch', 2)]


async def test_index_follows_saves_renames_and_deletes(tmp_path):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions.base import Session
    from cognitrix.sessions.index import ChatIndex

    await _initialize_routes_db(tmp_path)
    session = await _owned_session('user-1', 'agent-1', chat=_chat('First question'))
    ctx = _ctx('user-1')
    assert (await routes.all_chats(ctx))[0]['title'] == 'First question'

    session.update_history({'role': 'user', 'type': 'text', 'content': 'More'})
    await session.save()
    await routes.update_session(str(session.id), routes.SessionUpdate(title='Launch', pinned=True), ctx)
    row = (await routes.all_chats(ctx))[0]
    assert (row['title'], row['renamed'], row['pinned'], row['message_count']) == ('Launch', True, True, 3)

    await Session.delete_many({'id': str(session.id)})
    assert await ChatIndex.find_one({'session_id': str(session.id)}) is None


async def test_chats_from_before_the_index_are_picked_up_once(tmp_path):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions.index import ChatIndex

    await _initialize_routes_db(tmp_path)
    old = await _owned_session('user-1', 'agent-1', chat=_chat('An older chat'))
    step = await _owned_session('user-1', 'agent-1', chat=_chat('A task step'), task_id='task-1')
    await ChatIndex.delete_many({'session_id': str(old.id)})

    listed = await routes.all_chats(_ctx('user-1'))
    assert [c['title'] for c in listed] == ['An older chat']
    # The task step is remembered as "not a chat" so it is not loaded again.
    assert (await ChatIndex.find_one({'session_id': str(step.id)})).kind == 'skip'


async def test_version_changes_when_an_agent_starts_a_chat(tmp_path):
    """The sidebar polls this token and re-reads the list only when it moves,
    so a chat an agent starts with another agent shows up without a reload."""
    from types import SimpleNamespace

    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions import agent_chats

    await _initialize_routes_db(tmp_path)
    parent = await _owned_session('user-1', 'agent-1', chat=_chat('Get the Writer on it'))
    mine, theirs = _ctx('user-1'), _ctx('user-2')
    await routes.all_chats(mine)
    await routes.all_chats(theirs)
    before = (await routes.chats_version(mine))['version']
    assert (await routes.chats_version(mine))['version'] == before
    other_before = (await routes.chats_version(theirs))['version']

    child = await agent_chats.create_chat(
        'user-1', SimpleNamespace(id='agent-2'),
        {'agent_id': 'agent-1', 'agent_name': 'Helper', 'session_id': str(parent.id), 'depth': 1},
    )

    assert (await routes.chats_version(mine))['version'] != before
    assert (await routes.chats_version(theirs))['version'] == other_before
    listed = await routes.all_chats(mine)
    assert str(child.id) in {c['id'] for c in listed}
