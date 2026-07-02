"""_patch_odbms_sqlite: persistence must round-trip on odbms sqlite.

Unpatched odbms<=0.5.2 leaves the id column NULL (so re-saves silently update
nothing) and serializes lists as '::'.join(str(v)) (unreadable for lists of
dicts like Session.chat). These tests lock the shims in cognitrix.config.
"""

import pytest


@pytest.mark.asyncio
async def test_session_roundtrip_and_update(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    await DBMS.initialize_async('sqlite', database=str(tmp_path / 't.db'))
    _patch_odbms_sqlite()

    from cognitrix.sessions.base import Session

    create = getattr(Session, '_create_table_async', None) or Session.create_table
    await create()

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

    await DBMS.initialize_async('sqlite', database=str(tmp_path / 't2.db'))
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
