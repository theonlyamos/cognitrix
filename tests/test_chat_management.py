"""Rename, pin, archive, search and rewind for a person's own chats."""
import pytest
from fastapi import HTTPException

from tests.test_session_ownership_routes import _ctx, _initialize_routes_db, _owned_session


def _chat(*turns):
    out = []
    for user, reply in turns:
        out.append({'role': 'user', 'type': 'text', 'content': user})
        out.append({'role': 'assistant', 'type': 'text', 'content': reply})
    return out


async def test_rename_pin_archive_and_clearing_the_name(tmp_path):
    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)
    session = await _owned_session('user-1', 'agent-1', chat=_chat(('Plan my trip to Accra', 'Sure.')))
    sid, ctx = str(session.id), _ctx('user-1')

    summary = await routes.update_session(sid, routes.SessionUpdate(title='  Ghana trip  ', pinned=True), ctx)
    assert summary['title'] == 'Ghana trip' and summary['renamed'] and summary['pinned']
    assert not summary['archived']

    summary = await routes.update_session(sid, routes.SessionUpdate(archived=True), ctx)
    assert summary['archived'] and summary['pinned'] and summary['title'] == 'Ghana trip'

    summary = await routes.update_session(sid, routes.SessionUpdate(title=''), ctx)
    assert summary['title'] == 'Plan my trip to Accra' and not summary['renamed']

    listed = await routes.all_chats(ctx)
    assert listed[0]['archived'] is True and listed[0]['pinned'] is True


async def test_someone_elses_chat_cannot_be_changed_or_rewound(tmp_path):
    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)
    session = await _owned_session('user-2', 'agent-1', chat=_chat(('Secret', 'Ok')))
    for call in (
        routes.update_session(str(session.id), routes.SessionUpdate(title='Mine now'), _ctx('user-1')),
        routes.rewind_session(str(session.id), routes.SessionRewind(turn=0), _ctx('user-1')),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 404


async def test_search_matches_titles_and_messages_of_own_chats_only(tmp_path):
    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)
    await _owned_session('user-1', 'agent-1', chat=_chat(('Hello', 'The invoice total is 420 cedis.')))
    named = await _owned_session('user-1', 'agent-1', title='Invoice follow-up', chat=_chat(('Hi', 'Hello')))
    await _owned_session('user-1', 'agent-1', chat=_chat(('Weather?', 'Sunny')))
    await _owned_session('user-2', 'agent-1', chat=_chat(('My invoice', 'Private')))
    await _owned_session('user-1', 'agent-1', incognito=True, chat=_chat(('invoice', 'hidden')))

    hits = await routes.search_chats('invoice', _ctx('user-1'))
    assert len(hits) == 2
    assert hits[0]['id'] == named.id  # a title match ranks first
    assert 'invoice total' in hits[1]['snippet']
    assert await routes.search_chats('  ', _ctx('user-1')) == []


async def test_rewind_drops_the_message_and_everything_after_it(tmp_path):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.sessions.base import Session

    await _initialize_routes_db(tmp_path)
    chat = _chat(('First question', 'First answer'))
    chat.append({'role': 'user', 'type': 'image', 'artifact': {'id': 'a'}})
    chat += _chat(('Second question', 'Second answer'))
    session = await _owned_session('user-1', 'agent-1', chat=chat)
    sid, ctx = str(session.id), _ctx('user-1')

    with pytest.raises(HTTPException) as exc_info:
        await routes.rewind_session(sid, routes.SessionRewind(turn=1, expected='Something else'), ctx)
    assert exc_info.value.status_code == 404
    assert len((await Session.get(sid)).chat) == 5

    result = await routes.rewind_session(
        sid, routes.SessionRewind(turn=1, expected='Second question\n📎 notes.txt'), ctx,
    )
    assert result == {'message': 'Second question', 'message_count': 3}
    stored = await Session.get(sid)
    assert [m.get('content') for m in stored.chat[:2]] == ['First question', 'First answer']

    with pytest.raises(HTTPException) as exc_info:
        await routes.rewind_session(sid, routes.SessionRewind(turn=5), ctx)
    assert exc_info.value.status_code == 404


async def test_a_running_chat_is_left_alone(tmp_path, monkeypatch):
    from cognitrix.api.routes import sessions as routes

    await _initialize_routes_db(tmp_path)
    session = await _owned_session('user-1', 'agent-1', chat=_chat(('Q', 'A')))
    monkeypatch.setattr(routes, '_chat_is_running', lambda ctx, session_id: True)
    for call in (
        routes.update_session(str(session.id), routes.SessionUpdate(title='x'), _ctx('user-1')),
        routes.rewind_session(str(session.id), routes.SessionRewind(turn=0), _ctx('user-1')),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 409


async def test_rewind_skips_upload_notes_and_keeps_turns_with_attachments(tmp_path):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.media.documents import DOCUMENT_HISTORY_MARKER

    await _initialize_routes_db(tmp_path)
    chat = [
        {'role': 'user', 'type': 'text', 'content': 'Summarise this file'},
        {'role': 'User', 'type': 'text', 'content': f'{DOCUMENT_HISTORY_MARKER}\nuploads/u/s/report.pdf'},
        {'role': 'assistant', 'type': 'text', 'content': 'It says sales rose.'},
        {'role': 'user', 'type': 'text', 'content': 'By how much?'},
        {'role': 'assistant', 'type': 'text', 'content': 'Ten percent.'},
    ]
    session = await _owned_session('user-1', 'agent-1', chat=chat)
    sid, ctx = str(session.id), _ctx('user-1')

    # The turn with the upload stays as it is.
    with pytest.raises(HTTPException) as exc_info:
        await routes.rewind_session(sid, routes.SessionRewind(turn=0, expected='Summarise this file'), ctx)
    assert exc_info.value.status_code == 409

    # The upload note is not a turn: the second question is turn 1.
    result = await routes.rewind_session(sid, routes.SessionRewind(turn=1, expected='By how much?'), ctx)
    assert result == {'message': 'By how much?', 'message_count': 3}


async def test_rewind_can_keep_a_turn_and_its_uploads_to_answer_it_again(tmp_path):
    from cognitrix.api.routes import sessions as routes
    from cognitrix.media.documents import DOCUMENT_HISTORY_MARKER

    await _initialize_routes_db(tmp_path)
    note = {'role': 'User', 'type': 'text', 'content': f'{DOCUMENT_HISTORY_MARKER}\nuploads/u/s/report.pdf'}
    session = await _owned_session('user-1', 'agent-1', chat=[
        {'role': 'user', 'type': 'text', 'content': 'Summarise this file'},
        note,
        {'role': 'assistant', 'type': 'text', 'content': 'It says sales rose.'},
    ])
    result = await routes.rewind_session(
        str(session.id),
        routes.SessionRewind(turn=0, expected='Summarise this file', keep=True, text='Summarise it in French'),
        _ctx('user-1'),
    )
    assert result == {'message': 'Summarise it in French', 'message_count': 2}
    from cognitrix.sessions.base import Session
    chat = (await Session.get(str(session.id))).chat
    assert chat[0]['content'] == 'Summarise it in French' and chat[0]['resume'] is True
    assert chat[1]['content'] == note['content']
