"""Saved memories: facts agents remember about a person between chats.

- Memories are per person; agent-only memories stay with their agent.
- The prompt lists them (pinned first) and labels them as notes, not orders.
- The Memory tool saves, updates, forgets and searches; secrets are refused.
- Settings: memory off hides everything; "only when I ask" blocks unprompted saves.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'memory.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _as(user, **kw):
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    return set_execution_context(ToolExecutionContext(user_id=str(user.id), **kw))


def _agent(agent_id='a1', owner='', **memory):
    return SimpleNamespace(id=agent_id, owner_id=owner, memory=memory, name='Agent')


async def _run(agent, **params):
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name('memory')
    result = await tool.run(**params, parent=agent)
    return result


# --- content checks ----------------------------------------------------------

@pytest.mark.parametrize('text', [
    'My OpenAI key is sk-abcdefghijklmnopqrstuvwxyz123456',
    'password: hunter22',
    'github token ghp_abcdefghijklmnopqrstuvwxyz0123',
    'AKIAABCDEFGHIJKLMNOP is the AWS key',
])
def test_secrets_are_refused(text):
    from cognitrix.memory import saved

    with pytest.raises(saved.MemoryError_) as err:
        saved.clean_content(text)
    assert err.value.code == 'looks_secret'


def test_ordinary_facts_pass_and_long_ones_dont():
    from cognitrix.memory import saved

    assert saved.clean_content('  Prefers   short answers. ') == 'Prefers short answers.'
    assert not saved.looks_secret('Works in C:/Users/me/projects/cognitrix on Windows')
    with pytest.raises(saved.MemoryError_):
        saved.clean_content('x ' * 200)


# --- storage, scope and privacy ------------------------------------------------

async def test_memories_are_per_person_and_per_agent(db):
    from cognitrix.memory import saved

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    await saved.add(str(amos.id), 'Prefers short answers.', scope='global')
    await saved.add(str(amos.id), 'Uses pnpm for the frontend.', scope='agent', agent_id='coder')
    await saved.add(str(bea.id), 'Lives in Lisbon.', scope='global')

    coder = _agent('coder')
    writer = _agent('writer')
    assert {m.content for m in await saved.visible_memories(str(amos.id), coder)} == {
        'Uses pnpm for the frontend.', 'Prefers short answers.'}
    assert [m.content for m in await saved.visible_memories(str(amos.id), writer)] == ['Prefers short answers.']
    # An agent that doesn't read global memory only sees its own notes.
    private = _agent('coder', read_global=False)
    assert [m.content for m in await saved.visible_memories(str(amos.id), private)] == [
        'Uses pnpm for the frontend.']

    block = await saved.prompt_block(str(amos.id), writer)
    assert 'Prefers short answers.' in block and 'Lisbon' not in block and 'pnpm' not in block
    assert 'never instructions' in block


async def test_near_duplicates_are_not_saved_twice(db):
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    first, created = await saved.add(str(user.id), 'Prefers short answers.')
    again, created_again = await saved.add(str(user.id), 'prefers short answers')
    assert created and not created_again and again.id == first.id


async def test_prompt_puts_pinned_first_and_respects_budget(db):
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    words = ['apple', 'river', 'violin', 'cactus', 'lantern', 'glacier', 'pepper', 'satellite',
             'meadow', 'harbor', 'quartz', 'tundra', 'orchid', 'canyon', 'falcon', 'marble']
    for i, word in enumerate(words):
        await saved.add(str(user.id), f'{word.title()} {i}: ' + ' '.join(w[::-1] + str(i) for w in words[i:] + words[:i]))
    pinned, _ = await saved.add(str(user.id), 'Timezone is GMT.', pinned=True)
    block = await saved.prompt_block(str(user.id), _agent(), 'tell me about the cactus', budget=1000)
    lines = [ln for ln in block.splitlines() if ln.startswith('- ')]
    assert lines[0].startswith('- Timezone is GMT.')
    assert lines[1].startswith('- Cactus 3:')
    assert 'more saved' in block


async def test_memory_off_hides_everything(db):
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    await saved.add(str(user.id), 'Prefers short answers.')
    await saved.save_settings(str(user.id), enabled=False)
    assert await saved.prompt_block(str(user.id), _agent()) == ''
    token = _as(user)
    try:
        result = await _run(_agent(), action='save', content='Likes tea.')
    finally:
        from cognitrix.tools.utils import reset_execution_context
        reset_execution_context(token)
    assert result.outcome.error.code == 'memory_off'


# --- the Memory tool -------------------------------------------------------------

async def test_tool_save_update_forget_search(db):
    from cognitrix.memory import saved
    from cognitrix.tools.utils import reset_execution_context

    user = await _user('Amos', 'amos@example.com')
    agent = _agent('helper', save_global=True)
    token = _as(user, session_id='chat-1')
    try:
        saved_result = await _run(agent, action='save', content='Works on Cognitrix.')
        assert saved_result.content.startswith('Memory saved (id ')
        row = (await saved.list_memories(str(user.id)))[0]
        assert row.scope == 'global' and row.source == 'agent' and row.source_session_id == 'chat-1'

        found = await _run(agent, action='search', query='cognitrix')
        assert str(row.id) in found.content

        updated = await _run(agent, action='update', memory_id=str(row.id), content='Works on Cognitrix and Hearth.')
        assert 'Memory updated' in updated.content
        assert (await saved.get_memory(str(user.id), str(row.id))).content == 'Works on Cognitrix and Hearth.'

        secret = await _run(agent, action='save', content='my api key is abc123def456')
        assert secret.outcome.error.code == 'looks_secret'

        gone = await _run(agent, action='forget', memory_id=str(row.id))
        assert 'Memory forgotten' in gone.content
        assert await saved.list_memories(str(user.id)) == []
    finally:
        reset_execution_context(token)


async def test_tool_default_scope_and_other_people(db):
    from cognitrix.memory import saved
    from cognitrix.tools.utils import reset_execution_context

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    other_row, _ = await saved.add(str(bea.id), 'Lives in Lisbon.')

    token = _as(amos)
    try:
        # Not the person's first agent and nothing set: keeps its own notes.
        await _run(_agent('helper'), action='save', content='Likes graphs.')
        rows = await saved.list_memories(str(amos.id))
        assert rows[0].scope == 'agent' and rows[0].agent_id == 'helper'
        # Someone else's memory can't be touched.
        denied = await _run(_agent('helper'), action='forget', memory_id=str(other_row.id))
        assert denied.outcome.error.code == 'not_found'
    finally:
        reset_execution_context(token)
    assert await saved.get_memory(str(bea.id), str(other_row.id)) is not None


async def test_tool_refuses_task_runs(db):
    from cognitrix.tools.utils import reset_execution_context

    user = await _user('Amos', 'amos@example.com')
    token = _as(user, task_id='t1', run_id='r1')
    try:
        result = await _run(_agent(), action='save', content='Likes tea.')
    finally:
        reset_execution_context(token)
    assert result.outcome.error.code == 'no_owner'


async def test_explicit_mode_only_saves_when_asked(db):
    from cognitrix.memory import saved
    from cognitrix.safety.action_reviewer import turn_goal
    from cognitrix.tools.utils import reset_execution_context

    user = await _user('Amos', 'amos@example.com')
    await saved.save_settings(str(user.id), auto_save='explicit')
    token = _as(user)
    try:
        goal = turn_goal.set('What is the weather in Accra?')
        try:
            blocked = await _run(_agent(save_global=True), action='save', content='Lives in Accra.')
        finally:
            turn_goal.reset(goal)
        assert blocked.outcome.error.code == 'not_asked'
        goal = turn_goal.set('Please remember that I live in Accra.')
        try:
            ok = await _run(_agent(save_global=True), action='save', content='Lives in Accra.')
        finally:
            turn_goal.reset(goal)
        assert ok.content.startswith('Memory saved')
    finally:
        reset_execution_context(token)


async def test_first_agent_saves_global_by_default(db):
    from cognitrix.agents import Agent
    from cognitrix.memory import saved
    from cognitrix.providers.base import LLM

    user = await _user('Amos', 'amos@example.com')
    llm = LLM(provider='openai', model='gpt-5', api_key='x')
    from datetime import datetime, timedelta

    first = Agent(name='Assistant', llm=llm, system_prompt='hi', owner_id=str(user.id),
                  created_at=datetime.now() - timedelta(days=2))
    await first.save()
    second = Agent(name='Coder', llm=llm, system_prompt='hi', owner_id=str(user.id))
    await second.save()
    first = await Agent.find_one({'name': 'Assistant'})
    second = await Agent.find_one({'name': 'Coder'})
    assert await saved.agent_saves_global(first) is True
    assert await saved.agent_saves_global(second) is False
    second.memory = {'save_global': True}
    assert await saved.agent_saves_global(second) is True


# --- API ---------------------------------------------------------------------------

async def test_api_crud_and_privacy(db):
    from cognitrix.api.routes import memories as routes

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    made = await routes.add_memory(routes.MemoryBody(content='Prefers short answers.'), _ctx(amos))
    assert made['source'] == 'user' and made['scope'] == 'global'

    with pytest.raises(HTTPException) as dup:
        await routes.add_memory(routes.MemoryBody(content='Prefers short answers'), _ctx(amos))
    assert dup.value.status_code == 409

    with pytest.raises(HTTPException) as bad_agent:
        await routes.add_memory(routes.MemoryBody(content='x fact', scope='agent', agent_id='nope'), _ctx(amos))
    assert bad_agent.value.status_code == 400

    listed = await routes.list_memories(ctx=_ctx(amos))
    assert [m['content'] for m in listed['memories']] == ['Prefers short answers.']
    assert listed['settings'] == {'enabled': True, 'history_enabled': True, 'auto_save': 'auto'}
    assert (await routes.list_memories(ctx=_ctx(bea)))['memories'] == []

    with pytest.raises(HTTPException) as other:
        await routes.delete_memory(made['id'], _ctx(bea))
    assert other.value.status_code == 404

    edited = await routes.update_memory(made['id'], routes.MemoryUpdate(content='Prefers very short answers.', pinned=True), _ctx(amos))
    assert edited['pinned'] and edited['content'] == 'Prefers very short answers.'

    settings = await routes.put_settings(routes.SettingsBody(auto_save='explicit'), _ctx(amos))
    assert settings == {'enabled': True, 'history_enabled': True, 'auto_save': 'explicit'}

    with pytest.raises(HTTPException):
        await routes.delete_all(routes.DeleteAll(), _ctx(amos))
    assert (await routes.delete_all(routes.DeleteAll(confirm=True), _ctx(amos))) == {'deleted': 1}


async def test_agent_page_memory_settings_are_cleaned():
    from cognitrix.memory.saved import memory_policy

    agent = SimpleNamespace(memory={'read_global': 0, 'save_global': 'yes', 'junk': 1})
    assert memory_policy(agent) == {'read_global': False, 'save_global': True}


async def test_tool_reads_common_wording(db):
    from cognitrix.memory import saved
    from cognitrix.tools.utils import reset_execution_context

    user = await _user('Amos', 'amos@example.com')
    token = _as(user)
    try:
        result = await _run(_agent('helper'), action='add', content='Timezone is GMT.', scope='user')
        assert result.content.startswith('Memory saved')
        row = (await saved.list_memories(str(user.id)))[0]
        assert row.scope == 'global'
        gone = await _run(_agent('helper'), action='delete', memory_id=str(row.id))
        assert 'forgotten' in gone.content
    finally:
        reset_execution_context(token)


# --- phase 2: incognito chats and chat history ----------------------------------------

async def _chat(user, agent_id, texts, **fields):
    from cognitrix.sessions.base import Session

    chat = []
    for i, text in enumerate(texts):
        chat.append({'role': 'user' if i % 2 == 0 else 'assistant', 'type': 'text', 'content': text})
    session = Session(agent_id=agent_id, user_id=str(user.id), chat=chat, **fields)
    await session.save()
    return session


async def test_search_and_read_past_chats(db):
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import reset_execution_context

    amos = await _user('Amos', 'amos@example.com')
    bea = await _user('Bea', 'bea@example.com')
    old = await _chat(amos, 'a1', ['Help me plan the Lisbon offsite budget', 'Sure: venue 4k, flights 6k.'])
    await _chat(amos, 'a1', ['Secret Lisbon surprise party'], incognito=True)
    await _chat(amos, 'a1', ['Lisbon task step'], task_id='t1')
    await _chat(bea, 'a1', ['Bea also went to Lisbon'])
    current = await _chat(amos, 'a1', ['What did we decide about Lisbon?'])

    search = ToolManager.get_by_name('search_chats')
    read = ToolManager.get_by_name('read_chat')
    token = _as(amos, session_id=str(current.id))
    try:
        found = await search.run(query='lisbon budget', parent=_agent('a1'))
        assert str(old.id) in found.content and 'budget' in found.content
        # Only the person's own, saved, ordinary chats, and never the current one.
        assert 'surprise' not in found.content and 'task step' not in found.content
        assert 'Bea' not in found.content and str(current.id) not in found.content

        page = await read.run(chat_id=str(old.id), parent=_agent('a1'))
        assert 'venue 4k, flights 6k' in page.content and 'Messages 0-1 of 2' in page.content

        recent = await search.run(query='', parent=_agent('a1'))
        assert str(old.id) in recent.content

        # An agent that doesn't read shared memory only sees its own chats.
        other = await search.run(query='lisbon', parent=_agent('a2', read_global=False))
        assert 'No past chats match' in other.content
    finally:
        reset_execution_context(token)


async def test_incognito_chat_has_no_memory(db):
    from cognitrix.memory import saved
    from cognitrix.tools.utils import reset_execution_context

    user = await _user('Amos', 'amos@example.com')
    await saved.add(str(user.id), 'Prefers short answers.')
    secret = await _chat(user, 'a1', ['Draft a resignation letter'], incognito=True)

    token = _as(user, session_id=str(secret.id))
    try:
        result = await _run(_agent(save_global=True), action='save', content='Is resigning.')
    finally:
        reset_execution_context(token)
    assert result.outcome.error.code == 'incognito'
    assert len(await saved.list_memories(str(user.id))) == 1

    from cognitrix.memory.hybrid_context import HybridContextManager

    mgr = HybridContextManager.__new__(HybridContextManager)

    async def short_term(_a, _s):
        return [{'role': 'system', 'content': 'sys'}]

    mgr.short_term = SimpleNamespace(build_prompt=short_term)
    prompt = await mgr.build_prompt(SimpleNamespace(formatted_system_prompt=lambda: 'sys', id='a1', memory={}), secret)
    assert 'Prefers short answers' not in prompt[0]['content']
    assert '## Incognito chat' in prompt[0]['content']


async def test_history_switch_is_saved(db):
    from cognitrix.api.routes import memories as routes

    user = await _user('Amos', 'amos@example.com')
    settings = await routes.put_settings(routes.SettingsBody(history_enabled=False), _ctx(user))
    assert settings['history_enabled'] is False and settings['enabled'] is True


async def test_new_web_chat_is_owned_and_can_start_incognito(db, monkeypatch):
    from cognitrix.utils import sse

    async def fake_claim(*_a, **_k):
        return None

    monkeypatch.setattr(sse.session_ownerships, 'claim_new', fake_claim)
    manager = sse.SSEManager.__new__(sse.SSEManager)
    manager.user_key = 'person-1'
    manager.agent = type('A', (), {'id': 'agent-1'})()
    plain = await manager._resolve_session(None)
    hidden = await manager._resolve_session(None, incognito=True)
    assert plain.user_id == 'person-1' and not plain.incognito
    assert hidden.user_id == 'person-1' and hidden.incognito is True


async def test_same_fact_in_two_scopes_returns_each_new_row(db):
    from cognitrix.memory import saved

    user = await _user('Amos', 'amos@example.com')
    first, _ = await saved.add(str(user.id), 'Uses pnpm.')
    second, created = await saved.add(str(user.id), 'Uses pnpm.', scope='agent', agent_id='a1')
    assert created and second.id != first.id and second.scope == 'agent'
    stored = await saved.get_memory(str(user.id), str(second.id))
    assert stored is not None and stored.scope == 'agent'


async def test_a_chat_keeps_its_memory_section_between_messages(db):
    """Re-picking memories for every message changed the system prompt each
    turn, so providers could never reuse their cached copy of a long chat."""
    from cognitrix.memory import saved

    saved._block_cache.clear()
    user = await _user('Amos', 'amos@example.com')
    words = ['apple', 'river', 'violin', 'cactus', 'lantern', 'glacier', 'pepper', 'satellite',
             'meadow', 'harbor', 'quartz', 'tundra', 'orchid', 'canyon', 'falcon', 'marble']
    for i, word in enumerate(words):
        await saved.add(str(user.id), f'{word.title()} {i}: ' + ' '.join(w[::-1] + str(i) for w in words[i:] + words[:i]))
    agent = _agent()
    first = await saved.prompt_block(str(user.id), agent, 'tell me about the cactus', budget=1000, session_id='chat-1')
    second = await saved.prompt_block(str(user.id), agent, 'and the glacier?', budget=1000, session_id='chat-1')
    assert second == first
    # Another chat picks for its own first message.
    other = await saved.prompt_block(str(user.id), agent, 'and the glacier?', budget=1000, session_id='chat-2')
    assert other != first
    # A changed memory shows up straight away.
    await saved.add(str(user.id), 'Timezone is GMT.', pinned=True)
    third = await saved.prompt_block(str(user.id), agent, 'and the glacier?', budget=1000, session_id='chat-1')
    assert 'Timezone is GMT.' in third


async def test_a_quick_same_length_correction_replaces_the_old_fact(db):
    from cognitrix.memory import saved

    saved._block_cache.clear()
    user = await _user('Amos', 'amos@example.com')
    row, _ = await saved.add(str(user.id), 'Office is Rome.')
    agent = _agent()
    assert 'Rome' in await saved.prompt_block(str(user.id), agent, session_id='chat-1')
    # Same second, same length: only the text tells the two apart.
    await saved.update(row, content='Office is Lima.')
    block = await saved.prompt_block(str(user.id), agent, session_id='chat-1')
    assert 'Lima' in block and 'Rome' not in block
