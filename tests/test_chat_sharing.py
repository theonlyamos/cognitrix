"""Sharing a chat with other people on the server.

- Only the chat's owner shares it; incognito and group chats can't be shared.
- "view" reads and follows it; "chat" also writes in it.
- A guest's message runs read-only as a guest, as the owner's run (so the
  owner's page follows it), with the author shown, and waits while a turn runs.
- Unsharing or deleting the chat ends access.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'share.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    yield tmp_path


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


async def _chat(owner, **extra):
    from cognitrix.agents import Agent
    from cognitrix.providers import LLM
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions.base import Session

    agent = Agent(name='Researcher', system_prompt='Help.', owner_id=str(owner.id),
                  llm=LLM(provider='openai', model='gpt-test', api_key='sk-test'))
    await agent.save()
    session = Session(agent_id=agent.id, user_id=str(owner.id), chat=[
        {'role': 'user', 'type': 'text', 'content': 'Plan the launch'},
        {'role': 'assistant', 'type': 'text', 'content': '', 'tool_calls': [{'name': 'Read', 'arguments': {}}]},
        {'role': 'tool', 'type': 'text', 'content': 'SECRET FILE CONTENTS'},
        {'role': 'assistant', 'type': 'text', 'content': 'Here is the plan.'},
    ], **extra)
    await session.save()
    await claim_new(str(session.id), str(owner.id), str(agent.id))
    return agent, session


@pytest.fixture
def fake_turn(monkeypatch):
    from cognitrix.sessions.base import Session

    seen = {}

    async def call(self, message, agent, *args, **kwargs):
        from cognitrix.safety import permissions

        seen['message'] = message
        seen['guest'] = permissions.guest_run.get()
        seen['read_only'] = permissions.read_only_run.get()
        seen['user_id'] = getattr(kwargs.get('tool_context'), 'user_id', None)
        self.update_history({'role': 'user', 'type': 'text', 'content': message})
        await kwargs['output']({'type': 'generate', 'content': 'Sure.'})
        if seen.get('hold'):
            await seen['hold'].wait()
        self.update_history({'role': 'assistant', 'type': 'text', 'content': 'Sure.'})
        await self.save()

    monkeypatch.setattr(Session, '__call__', call)
    return seen


async def test_owner_shares_and_roles_apply(db, fake_turn):
    from cognitrix.api.routes import chat_sharing as routes
    from cognitrix.sessions import runs as chat_runs

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    ama = await _user('Ama', 'ama@example.com')
    _, session = await _chat(amos)
    sid = str(session.id)

    # Nobody else can read or share it yet.
    with pytest.raises(HTTPException):
        await routes.read_shared(sid, _ctx(kofi))
    with pytest.raises(HTTPException):
        await routes.get_people(sid, _ctx(kofi))

    view = await routes.set_people(sid, routes.SharingUpdate(people=[
        routes.SharedPerson(user_id=str(kofi.id), role='chat'),
        routes.SharedPerson(user_id=str(ama.id), role='view'),
    ]), _ctx(amos))
    assert {p['name']: p['role'] for p in view['people']} == {'Kofi': 'chat', 'Ama': 'view'}
    with pytest.raises(HTTPException):
        await routes.set_people(sid, routes.SharingUpdate(people=[routes.SharedPerson(user_id='nobody', role='view')]), _ctx(amos))

    from cognitrix.tasks.automation import AgentNotification
    alerts = await AgentNotification.find({'user_id': str(kofi.id)})
    assert alerts and alerts[0].link == f'/shared/{sid}'

    listed = await routes.shared_with_me(_ctx(kofi))
    assert listed[0]['id'] == sid and listed[0]['owner'] == 'Amos' and listed[0]['title'] == 'Plan the launch'

    read = await routes.read_shared(sid, _ctx(ama))
    assert read['role'] == 'view'
    assert [m['role'] for m in read['messages']] == ['user', 'activity', 'assistant']
    assert 'SECRET' not in str(read['messages'])  # tool results stay out

    # A viewer can't write; a "chat" person can, as a read-only guest.
    with pytest.raises(HTTPException) as err:
        await routes.send_shared(sid, routes.SharedMessage(message='hi'), _ctx(ama))
    assert err.value.status_code == 403
    sent = await routes.send_shared(sid, routes.SharedMessage(message='Add a budget'), _ctx(kofi))
    run = chat_runs.get_run(str(amos.id), sid)
    assert run is not None and run.run_id == sent['run_id'] and not run.steerable
    for _ in range(50):
        if not run.running:
            break
        await asyncio.sleep(0.01)
    assert fake_turn['guest'] and fake_turn['read_only'] and fake_turn['user_id'] == str(amos.id)
    assert fake_turn['message'].startswith('[Message from Kofi in this shared chat.')

    read = await routes.read_shared(sid, _ctx(amos))
    guest = [m for m in read['messages'] if m.get('guest')]
    assert guest == [{'role': 'user', 'author': 'Kofi', 'guest': True, 'content': 'Add a budget'}]
    assert read['role'] == 'owner'

    # Unshared: access ends.
    await routes.set_people(sid, routes.SharingUpdate(people=[routes.SharedPerson(user_id=str(ama.id), role='view')]), _ctx(amos))
    with pytest.raises(HTTPException):
        await routes.read_shared(sid, _ctx(kofi))
    assert await routes.shared_with_me(_ctx(kofi)) == []


async def test_guest_waits_while_a_turn_runs(db, fake_turn):
    from cognitrix.api.routes import chat_sharing as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _, session = await _chat(amos)
    sid = str(session.id)
    await routes.set_people(sid, routes.SharingUpdate(people=[routes.SharedPerson(user_id=str(kofi.id), role='chat')]), _ctx(amos))
    fake_turn['hold'] = asyncio.Event()
    await routes.send_shared(sid, routes.SharedMessage(message='one'), _ctx(kofi))
    await asyncio.sleep(0.01)
    with pytest.raises(HTTPException) as err:
        await routes.send_shared(sid, routes.SharedMessage(message='two'), _ctx(kofi))
    assert err.value.status_code == 409
    fake_turn['hold'].set()
    await asyncio.sleep(0.05)


async def test_incognito_and_others_chats_cannot_be_shared(db):
    from cognitrix.api.routes import chat_sharing as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _, secret = await _chat(amos, incognito=True)
    with pytest.raises(HTTPException) as err:
        await routes.set_people(str(secret.id), routes.SharingUpdate(people=[routes.SharedPerson(user_id=str(kofi.id), role='view')]), _ctx(amos))
    assert err.value.status_code == 400
    _, other = await _chat(kofi)
    with pytest.raises(HTTPException) as err:
        await routes.set_people(str(other.id), routes.SharingUpdate(), _ctx(amos))
    assert err.value.status_code == 404


async def test_deleting_the_chat_drops_shares(db):
    from cognitrix.api.routes import chat_sharing as routes
    from cognitrix.api.routes import sessions as session_routes
    from cognitrix.sessions.sharing import ChatShare

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    _, session = await _chat(amos)
    sid = str(session.id)
    await routes.set_people(sid, routes.SharingUpdate(people=[routes.SharedPerson(user_id=str(kofi.id), role='view')]), _ctx(amos))
    import cognitrix.artifacts as artifacts

    async def cleanup(**kwargs):
        return None

    original = getattr(artifacts, 'delete_owned_session_artifacts', None)
    artifacts.delete_owned_session_artifacts = cleanup
    try:
        await session_routes.delete_session(sid, _ctx(amos))
    finally:
        if original is not None:
            artifacts.delete_owned_session_artifacts = original
    assert await ChatShare.find({'session_id': sid}) == []


def test_author_prefix_round_trip():
    from cognitrix.sessions.sharing import guest_text, split_author

    text = guest_text('Kofi [admin]', 'Amos', 'Hello\n\nthere')
    assert split_author(text) == ('Kofi (admin)', 'Hello\n\nthere')
    assert split_author('[Message from X in a chat]\n\nhi') == (None, '[Message from X in a chat]\n\nhi')


async def test_guest_turn_leaves_out_saved_memories(db):
    from cognitrix.memory.hybrid_context import HybridContextManager
    from cognitrix.safety.permissions import guest_run
    from cognitrix.sessions.base import Session

    import cognitrix.memory.saved as saved

    async def block(*args, **kwargs):
        return 'MEMORY'

    original = saved.prompt_block
    saved.prompt_block = block
    try:
        manager = HybridContextManager.__new__(HybridContextManager)

        async def history(*a):
            return [{}]
        manager.short_term = SimpleNamespace(build_prompt=history)
        agent = SimpleNamespace(formatted_system_prompt=lambda: 'SYSTEM')
        session = Session(agent_id='a', user_id='u1', chat=[])
        token = guest_run.set(True)
        try:
            assert 'MEMORY' not in (await manager.build_prompt(agent, session))[0]['content']
        finally:
            guest_run.reset(token)
        assert 'MEMORY' in (await manager.build_prompt(agent, session))[0]['content']
    finally:
        saved.prompt_block = original
