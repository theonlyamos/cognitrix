"""Meeting notes: upload a recording, get a transcript and written notes.

``POST /meetings`` takes the recording (multipart ``file``, plus optional
``title``, ``agent_id`` and ``language``) and returns at once; the page polls
``GET /meetings/{id}`` while it is transcribed and written up.
``POST /meetings/{id}/notes`` writes the notes again from the saved
transcript, for example with another agent's model. A person only ever sees
their own meetings. See ``cognitrix/meetings/notes.py``.

Live calls (``cognitrix/meetings/live.py``): ``POST /meetings/live`` sends an
agent into a Google Meet, Teams or Zoom call from the browser on its desktop
in the agents' sandboxed computer; ``POST /meetings/{id}/leave`` brings it
back, and ``POST /meetings/{id}/say`` has it say something in the call.
``POST /meetings/{id}/steer`` is private guidance (a note, something to
raise at the next pause, or the answer to a question the agent asked the
person), ``POST /meetings/{id}/pause`` takes it off or back on the record,
and ``GET /meetings/{id}/seen.png`` is what the agent saw when joining
failed. ``POST /meetings/test`` runs a practice call on the agent's own
computer. ``GET/PUT /meetings/settings`` hold the person's meeting settings
(cognitrix/meetings/extras.py) and ``GET /meetings/search?q=`` searches
past meetings.

Calls from Google Calendar (``cognitrix/meetings/calendar_calls.py``):
``GET /meetings/calendar`` lists the upcoming calls found in the connected
calendar, ``PUT /meetings/calendar`` changes how they are suggested,
``POST /meetings/calendar/check`` reads the calendar now, and
``POST /meetings/calendar/calls/{id}`` is the person's answer for one call
(send an agent, skip, or always / never for the series).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.meetings import calendar_calls, extras
from cognitrix.meetings import live as live_meetings
from cognitrix.meetings import notes as meeting_notes
from cognitrix.meetings.notes import MeetingNote

logger = logging.getLogger('cognitrix.log')

meetings_api = APIRouter(prefix='/meetings', dependencies=[Depends(jwt_only)])

MAX_TITLE = 160
MAX_RUNNING_PER_PERSON = 2
MAX_LIVE_PER_PERSON = 2
MAX_MEETINGS_PER_PERSON = 500


class NotesAgain(BaseModel):
    agent_id: str | None = None


class LiveJoin(BaseModel):
    url: str = Field(max_length=2000)
    agent_id: str | None = None
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    name: str | None = Field(default=None, max_length=60)
    """The name the agent joins as; the agent's own name when empty."""
    reply_mode: str = 'chat'
    announce: bool = True
    language: str | None = Field(default=None, max_length=12)
    context: str | None = Field(default=None, max_length=extras.MAX_CONTEXT)
    """Background for the call: agenda, who's in it, what the person wants out of it."""
    watch_words: str | None = Field(default=None, max_length=extras.MAX_WORDS_TEXT)
    share: str | None = None


class TestCall(BaseModel):
    agent_id: str | None = None
    reply_mode: str = 'chat'


class Steer(BaseModel):
    kind: str
    text: str = Field(default='', max_length=live_meetings.MAX_ANSWER_CHARS)


class PauseBody(BaseModel):
    paused: bool


class MeetingSettingsBody(BaseModel):
    share: str | None = None
    watch_words: str | None = Field(default=None, max_length=extras.MAX_WORDS_TEXT)
    vocabulary: str | None = Field(default=None, max_length=extras.MAX_WORDS_TEXT)
    speaker_pass: bool | None = None
    live_notes: bool | None = None


class CalendarSettingsBody(BaseModel):
    enabled: bool | None = None
    agent_id: str | None = None
    auto_join: str | None = None
    never_words: str | None = Field(default=None, max_length=calendar_calls.MAX_WORDS)
    remind_minutes: int | None = Field(default=None, ge=1, le=120)


class CallAnswer(BaseModel):
    action: str


class SayInCall(BaseModel):
    text: str = Field(min_length=1, max_length=live_meetings.MAX_ANSWER_CHARS)
    mode: str | None = None


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    notes: str | None = Field(default=None, max_length=200_000)


def _me(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


def _view(row: MeetingNote, full: bool = False) -> dict[str, Any]:
    data = {
        'id': str(row.id), 'title': row.title, 'filename': row.filename, 'agent_id': row.agent_id,
        'status': row.status, 'progress': row.progress, 'seconds': row.seconds, 'error': row.error,
        'created_at': str(getattr(row, 'created_at', '') or ''),
        'has_transcript': bool(row.transcript),
        'kind': row.kind or 'recording', 'url': row.url, 'platform': row.platform, 'bot_name': row.bot_name,
        'reply_mode': row.reply_mode, 'announce': row.announce, 'answers': row.answers,
        'in_call': live_meetings.running(str(row.id)),
        'share': row.share, 'paused': row.paused, 'owner_question': row.owner_question,
        'rejoins': row.rejoins, 'test': row.test,
        # Sent from a chat, which gets the notes when the call ends.
        'from_chat': bool(row.session_id),
    }
    if full:
        data.update({
            'transcript': row.transcript, 'notes': row.notes, 'context': row.context,
            'watch_words': list(row.watch_words), 'guidance': list(row.guidance),
            'live_notes': row.live_notes, 'speaker_transcript': row.speaker_transcript,
            'action_items': list(row.action_items), 'seen': row.seen,
            'has_seen_image': live_meetings.seen_file(str(row.id)) is not None,
        })
    return data


async def _mine(meeting_id: str, ctx: AuthContext) -> MeetingNote:
    row = await MeetingNote.get(meeting_id)
    if row is None or str(row.user_id) != _me(ctx):
        raise HTTPException(status_code=404, detail='Meeting not found')
    return row


async def _check_agent(user_key: str, agent_id: str) -> str:
    agent_id = (agent_id or '').strip()
    if agent_id and await agent_access.find_visible_agent(user_key, agent_id=agent_id) is None:
        raise HTTPException(status_code=404, detail='Agent not found')
    return agent_id


@meetings_api.get('')
async def list_meetings(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    from cognitrix.api.routes.audio import _effective, _load

    user_key = _me(ctx)
    rows = await MeetingNote.find({'user_id': user_key}) or []
    rows.sort(key=lambda r: str(getattr(r, 'created_at', '') or ''), reverse=True)
    provider, _model, api_key = _effective(await _load(user_key))
    return {
        'meetings': [_view(row) for row in rows],
        'voice_ready': bool(provider and api_key),
        'live': await _live_readiness(user_key),
        'ffmpeg': bool(meeting_notes.ffmpeg_path()),
        'max_bytes': meeting_notes.MAX_UPLOAD_BYTES,
        'max_seconds': meeting_notes.MAX_MEETING_SECONDS,
    }


async def _live_readiness(user_key: str) -> dict[str, Any]:
    """Can an agent join a call for this person? The Meetings page says what's missing."""
    from cognitrix.api.routes.audio import _load, _speech_key
    from cognitrix.computer.sandbox.client import sandbox_mode
    from cognitrix.providers.speech import SPEECH_PROVIDERS

    row = await _load(user_key)
    return {
        'computer': sandbox_mode() is not None,
        'speech': any(_speech_key(row, p) for p in SPEECH_PROVIDERS),
        'platforms': list(live_meetings.PLATFORM_LABELS.values())[:3],
    }


async def start_live(user_key: str, *, url: str, agent_id: str = '', title: str = '', name: str = '',
                     reply_mode: str = 'chat', announce: bool = True, language: str = '', context: str = '',
                     watch_words: list[str] | None = None, share: str = '', session_id: str = '',
                     chat_agent_id: str = '', series_id: str = '', test: bool = False) -> MeetingNote:
    """Send an agent into a call (the page, the Join Meeting tool and calendar
    calls). ``session_id`` is the chat that sent it, which gets the notes back.
    Raises MeetingError with what's wrong."""
    from cognitrix.computer.sandbox.client import sandbox_mode

    if sandbox_mode() is None:
        raise meeting_notes.MeetingError(
            "Agents join calls from their own computer. Turn it on under Settings, Agent access, Agents' computer first.", 409)
    if test:
        url, platform = live_meetings.test_call_url(), 'other'
        title = title or 'Test call'
    else:
        url, platform = live_meetings.check_link(url)
    if share and share not in meeting_notes.SHARE_LEVELS:
        raise meeting_notes.MeetingError('Answers can use what was said in the call ("call"), or also your work ("work").')
    if reply_mode not in live_meetings.REPLY_MODES:
        raise meeting_notes.MeetingError('Answers go in the chat, out loud, or not at all.')
    await meeting_notes._voice(user_key)
    agent = await meeting_notes._agent_llm(user_key, agent_id)
    mine = await MeetingNote.find({'user_id': user_key}) or []
    if len(mine) >= MAX_MEETINGS_PER_PERSON:
        raise meeting_notes.MeetingError('Delete some old meetings first.', 409)
    busy = [row for row in mine if live_meetings.running(str(row.id))]
    if any(str(row.agent_id) == str(agent.id) for row in busy):
        raise meeting_notes.MeetingError(f'{agent.name} is already in a call. Have it leave that one first.', 409)
    if len(busy) >= MAX_LIVE_PER_PERSON:
        raise meeting_notes.MeetingError('Your agents are already in two calls.', 429)
    label = live_meetings.PLATFORM_LABELS.get(platform, 'Call')
    title = (title.strip() or f'{label} call')[:MAX_TITLE]
    settings = await extras.settings_for(user_key)
    background = context.strip()
    if not test:
        before = await extras.previous_meeting(user_key, series_id=series_id, title=title)
        background = extras.join_context(background, extras.previous_context(before))
    row = MeetingNote(
        user_id=user_key, kind='live', url=url, platform=platform, agent_id=str(agent.id),
        title=title, bot_name=(name.strip() or agent.name)[:60],
        reply_mode=reply_mode, announce=bool(announce), language=(language or '').strip().lower()[:12],
        status=live_meetings.JOINING, progress='starting its computer',
        context=background, watch_words=extras.words(watch_words or []), share=share or settings.share,
        session_id=session_id, chat_agent_id=chat_agent_id, series_id=series_id, test=bool(test),
        runner=live_meetings.lease(),
    )
    await row.save()
    live_meetings.start(str(row.id))
    return row


async def _calendar_view(user_key: str) -> dict[str, Any]:
    settings = await calendar_calls.settings_for(user_key)
    connected = await calendar_calls.calendar_connection(user_key) is not None
    calls = await calendar_calls.upcoming(user_key) if connected else []
    return {
        'connected': connected,
        'settings': calendar_calls.settings_view(settings),
        'calls': [calendar_calls.view(c) for c in calls],
    }


@meetings_api.get('/calendar')
async def calendar_list(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return await _calendar_view(_me(ctx))


@meetings_api.put('/calendar')
async def calendar_settings(body: CalendarSettingsBody, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _me(ctx)
    agent_id = await _check_agent(user_key, body.agent_id) if body.agent_id is not None else None
    try:
        await calendar_calls.save_settings(user_key, enabled=body.enabled, agent_id=agent_id,
                                           auto_join=body.auto_join, never_words=body.never_words,
                                           remind_minutes=body.remind_minutes)
    except calendar_calls.CalendarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return await _calendar_view(user_key)


@meetings_api.post('/calendar/check')
async def calendar_check(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _me(ctx)
    try:
        await calendar_calls.check(user_key)
    except calendar_calls.CalendarError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await calendar_calls.act(user_id=user_key)
    return await _calendar_view(user_key)


@meetings_api.post('/calendar/calls/{call_id}')
async def calendar_answer(call_id: str, body: CallAnswer, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """Only the signed-in person answers: agents can list calls but never send one."""
    user_key = _me(ctx)
    try:
        await calendar_calls.answer(user_key, call_id, body.action)
    except calendar_calls.CalendarError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return await _calendar_view(user_key)


@meetings_api.get('/settings')
async def get_settings(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return extras.settings_view(await extras.settings_for(_me(ctx)))


@meetings_api.put('/settings')
async def put_settings(body: MeetingSettingsBody, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    try:
        row = await extras.save_settings(_me(ctx), **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return extras.settings_view(row)


@meetings_api.get('/search')
async def search_meetings(q: str = Query('', max_length=200), ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return {'results': await extras.search(_me(ctx), q, 20)}


@meetings_api.post('/test')
async def test_call(body: TestCall, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """A practice call on the agent's own computer: joins, writes in the chat,
    speaks and checks that it hears itself."""
    user_key = _me(ctx)
    agent_id = await _check_agent(user_key, body.agent_id or '')
    try:
        row = await start_live(user_key, url='', agent_id=agent_id, reply_mode=body.reply_mode, test=True)
    except meeting_notes.MeetingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return _view(await MeetingNote.get(row.id), full=True)


@meetings_api.post('/live')
async def join_live(body: LiveJoin, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _me(ctx)
    agent_id = await _check_agent(user_key, body.agent_id or '')
    try:
        row = await start_live(user_key, url=body.url, agent_id=agent_id, title=body.title or '',
                               name=body.name or '', reply_mode=body.reply_mode, announce=body.announce,
                               language=body.language or '', context=body.context or '',
                               watch_words=extras.words(body.watch_words or ''), share=body.share or '')
    except meeting_notes.MeetingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return _view(await MeetingNote.get(row.id), full=True)


@meetings_api.post('/{meeting_id}/steer')
async def steer_live(meeting_id: str, body: Steer, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """Private guidance for the agent in the call; nobody in the call sees it."""
    row = await _mine(meeting_id, ctx)
    if body.kind not in ('note', 'raise', 'answer'):
        raise HTTPException(status_code=400, detail='Steer with a note, something to raise, or an answer.')
    if not body.text.strip():
        raise HTTPException(status_code=400, detail='Write something to pass on.')
    if not live_meetings.steer(str(row.id), body.kind, body.text):
        raise HTTPException(status_code=409, detail='The agent is not in this call.')
    return {'status': 'queued'}


@meetings_api.post('/{meeting_id}/pause')
async def pause_live(meeting_id: str, body: PauseBody, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """Off the record (or back on). Answers once the call has applied it, so
    the page only says "paused" when it is; 'pending' if that took too long
    (it still counts from the moment it was asked)."""
    row = await _mine(meeting_id, ctx)
    done = live_meetings.steer(str(row.id), 'pause' if body.paused else 'resume')
    if done is None:
        raise HTTPException(status_code=409, detail='The agent is not in this call.')
    try:
        await asyncio.wait_for(asyncio.shield(done), PAUSE_WAIT_S)
    except TimeoutError:
        return {'status': 'pending'}
    return {'status': 'paused' if body.paused else 'resumed'}


PAUSE_WAIT_S = 15


@meetings_api.get('/{meeting_id}/seen.png')
async def seen_image(meeting_id: str, ctx: AuthContext = Depends(get_auth_context)) -> FileResponse:
    row = await _mine(meeting_id, ctx)
    path = live_meetings.seen_file(str(row.id))
    if path is None:
        raise HTTPException(status_code=404, detail='No screenshot for this meeting.')
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})


@meetings_api.post('/{meeting_id}/leave')
async def leave_live(meeting_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    row = await _mine(meeting_id, ctx)
    if not live_meetings.request_leave(str(row.id)):
        raise HTTPException(status_code=409, detail='The agent is not in this call.')
    await MeetingNote.update_many({'id': row.id}, {'progress': 'leaving the call'})
    return _view(await MeetingNote.get(row.id))


@meetings_api.post('/{meeting_id}/say')
async def say_live(meeting_id: str, body: SayInCall, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, str]:
    row = await _mine(meeting_id, ctx)
    mode = body.mode if body.mode in ('chat', 'voice') else None
    if not live_meetings.queue_say(str(row.id), body.text.strip(), mode):
        raise HTTPException(status_code=409, detail='The agent is not in this call.')
    return {'status': 'queued'}


@meetings_api.post('')
async def create_meeting(
    file: UploadFile = File(...),
    title: str = Form(''),
    agent_id: str = Form(''),
    language: str = Form(''),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    user_key = _me(ctx)
    name = Path(file.filename or 'recording').name[:200]
    suffix = Path(name).suffix.lower()
    if suffix not in meeting_notes.MEDIA_SUFFIXES:
        raise HTTPException(status_code=415, detail='Upload an audio or video recording (mp3, m4a, wav, mp4, webm…).')
    agent_id = await _check_agent(user_key, agent_id)
    try:
        await meeting_notes._voice(user_key)
    except meeting_notes.MeetingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    mine = await MeetingNote.find({'user_id': user_key}) or []
    if len(mine) >= MAX_MEETINGS_PER_PERSON:
        raise HTTPException(status_code=409, detail='Delete some old meetings first.')
    if sum(1 for row in mine if meeting_notes.running(str(row.id))) >= MAX_RUNNING_PER_PERSON:
        raise HTTPException(status_code=429, detail='Two recordings are already being written up. Wait for one to finish.')

    # The stored name is ours; the uploaded name is only ever shown.
    target = meeting_notes.storage_dir() / f'{uuid.uuid4().hex}{suffix}'
    size = 0
    try:
        with target.open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > meeting_notes.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail='The recording is too large (1 GB at most).')
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail='The recording was empty.')
        row = MeetingNote(
            user_id=user_key, title=(title.strip() or Path(name).stem)[:MAX_TITLE], filename=name,
            agent_id=agent_id, language=language.strip().lower()[:12],
        )
        await row.save()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    meeting_notes.start(str(row.id), target)
    return _view(await MeetingNote.get(row.id), full=True)


@meetings_api.get('/{meeting_id}')
async def get_meeting(meeting_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return _view(await _mine(meeting_id, ctx), full=True)


@meetings_api.patch('/{meeting_id}')
async def update_meeting(meeting_id: str, body: MeetingUpdate,
                         ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    row = await _mine(meeting_id, ctx)
    data: dict[str, Any] = {}
    if body.title is not None and body.title.strip():
        data['title'] = body.title.strip()
    if body.notes is not None:
        if row.status in meeting_notes.WORKING:
            raise HTTPException(status_code=409, detail='These notes are still being written.')
        data['notes'] = body.notes
    if data:
        await MeetingNote.update_one({'id': row.id}, data)
    return _view(await MeetingNote.get(row.id), full=True)


@meetings_api.post('/{meeting_id}/notes')
async def write_again(meeting_id: str, body: NotesAgain,
                      ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    row = await _mine(meeting_id, ctx)
    if meeting_notes.running(str(row.id)) or live_meetings.running(str(row.id)):
        raise HTTPException(status_code=409, detail='These notes are still being written.')
    if not row.transcript:
        raise HTTPException(status_code=409, detail='There is no transcript to write notes from. Upload the recording again.')
    data: dict[str, Any] = {'status': meeting_notes.WRITING, 'progress': 'writing the notes', 'error': ''}
    if body.agent_id is not None:
        data['agent_id'] = await _check_agent(_me(ctx), body.agent_id)
    await MeetingNote.update_one({'id': row.id}, data)
    meeting_notes.start(str(row.id), None)
    return _view(await MeetingNote.get(row.id), full=True)


@meetings_api.delete('/{meeting_id}')
async def delete_meeting(meeting_id: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, str]:
    row = await _mine(meeting_id, ctx)
    await meeting_notes.cancel(str(row.id))
    await live_meetings.cancel(str(row.id))
    live_meetings.forget_seen(str(row.id))
    await MeetingNote.delete_many({'id': row.id})
    return {'status': 'deleted'}
