"""_patch_odbms_sqlite: persistence must round-trip on odbms sqlite.

Unpatched odbms<=0.5.2 leaves the id column NULL (so re-saves silently update
nothing) and serializes lists as '::'.join(str(v)) (unreadable for lists of
dicts like Session.chat). These tests lock the shims in cognitrix.config.
"""

import pytest


async def _initialize_sqlite(DBMS, path):
    initialize_async = getattr(DBMS, 'initialize_async', None)
    if initialize_async is not None:
        await initialize_async('sqlite', database=str(path))
    else:
        DBMS.initialize('sqlite', database=str(path))


@pytest.mark.parametrize(
    ('model_name', 'factory'),
    [
        ('LLM', lambda: __import__('cognitrix.providers.base', fromlist=['LLM']).LLM(id='provided-id')),
        ('Skill', lambda: __import__('cognitrix.skills.models', fromlist=['Skill']).Skill(id='provided-id')),
        (
            'Tool',
            lambda: __import__('cognitrix.models.tool', fromlist=['Tool']).Tool(
                id='provided-id', name='test', description='test tool'
            ),
        ),
    ],
)
def test_patch_preserves_concrete_non_aliased_ids(model_name, factory):
    from cognitrix.config import _patch_odbms_sqlite

    _patch_odbms_sqlite()

    assert factory().id == 'provided-id', f'{model_name} id was replaced'


@pytest.mark.asyncio
async def test_session_roundtrip_and_update(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    await _initialize_sqlite(DBMS, tmp_path / 't.db')
    _patch_odbms_sqlite()

    from cognitrix.sessions.base import Session

    create = getattr(Session, '_create_table_async', None) or Session.create_table
    await create()

    explicit = Session(id='provided-id', agent_id='agent-0')
    assert explicit.id == 'provided-id'
    explicit.id = 'assigned-id'
    assert explicit.id == 'assigned-id'

    session = Session(agent_id='agent-1')
    session.chat = [
        {'role': 'User', 'type': 'text', 'content': 'hi'},
        {'role': 'assistant', 'type': 'tool_calls', 'content': '',
         'tool_calls': [{'name': 'Read', 'arguments': {'x': 1}, 'tool_call_id': 'id1'}]},
    ]
    await session.save()

    # Insert must stamp a persistent string id (not leave the column NULL).
    assert isinstance(session.id, str) and len(session.id) >= 32

    # A re-save must actually update the row, not silently match nothing.
    session.chat.append({'role': 'assistant', 'type': 'text', 'content': 'done'})
    await session.save()

    loaded = await Session.find_one({'agent_id': 'agent-1'})
    assert loaded is not None
    assert loaded.id == session.id
    assert len(loaded.chat) == 3
    # Nested dicts (tool_calls with typed arguments) must round-trip intact.
    assert loaded.chat[1]['tool_calls'][0]['arguments'] == {'x': 1}


@pytest.mark.asyncio
async def test_text_field_that_looks_like_json_stays_text(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    await _initialize_sqlite(DBMS, tmp_path / 't2.db')
    _patch_odbms_sqlite()

    from cognitrix.sessions.base import Session

    create = getattr(Session, '_create_table_async', None) or Session.create_table
    await create()

    # agent_id is a str field; a value that parses as a JSON list must not be
    # decoded into a list on read.
    session = Session(agent_id='["not", "a", "list", "field"]')
    await session.save()

    loaded = await Session.find_one({'id': session.id})
    assert loaded is not None
    assert loaded.agent_id == '["not", "a", "list", "field"]'
