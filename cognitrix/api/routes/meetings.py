"""Meeting notes: upload a recording, get a transcript and written notes.

``POST /meetings`` takes the recording (multipart ``file``, plus optional
``title``, ``agent_id`` and ``language``) and returns at once; the page polls
``GET /meetings/{id}`` while it is transcribed and written up.
``POST /meetings/{id}/notes`` writes the notes again from the saved
transcript, for example with another agent's model. A person only ever sees
their own meetings. See ``cognitrix/meetings/notes.py``.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.meetings import notes as meeting_notes
from cognitrix.meetings.notes import MeetingNote

logger = logging.getLogger('cognitrix.log')

meetings_api = APIRouter(prefix='/meetings', dependencies=[Depends(jwt_only)])

MAX_TITLE = 160
MAX_RUNNING_PER_PERSON = 2
MAX_MEETINGS_PER_PERSON = 500


class NotesAgain(BaseModel):
    agent_id: str | None = None


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
    }
    if full:
        data.update({'transcript': row.transcript, 'notes': row.notes})
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
        'ffmpeg': bool(meeting_notes.ffmpeg_path()),
        'max_bytes': meeting_notes.MAX_UPLOAD_BYTES,
        'max_seconds': meeting_notes.MAX_MEETING_SECONDS,
    }


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
    if meeting_notes.running(str(row.id)):
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
    await MeetingNote.delete_many({'id': row.id})
    return {'status': 'deleted'}
