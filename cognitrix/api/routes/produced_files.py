"""Files agents made (cognitrix/produced_files): details, previews, downloads.

Browser sessions only (``jwt_only``), except the content and thumbnail
routes at the end: those take a short-lived signed token in the address, so
a <video>, <audio>, <img> or PDF frame can load (and seek in) the file
without the sign-in header.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from cognitrix.common.constants import JWT_ALGORITHM, JWT_SECRET_KEY
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.produced_files import previews, service, storage
from cognitrix.produced_files.models import SERVER, ProducedFile
from cognitrix.produced_files.sources import SourceError
from cognitrix.session_ownership import principal_key

logger = logging.getLogger('cognitrix.log')

produced_files_api = APIRouter(prefix='/produced-files')
produced_file_content_api = APIRouter()

_AUDIENCE = 'cognitrix-produced-file'
TOKEN_HOURS = 6
MAX_ZIP_FILES = 100
# Opened in place: everything else is only ever downloaded.
_INLINE = ('image/', 'video/', 'audio/', 'application/pdf')


def _key() -> str:
    return f'{JWT_SECRET_KEY}:produced-file'


def _token(row: ProducedFile, what: str) -> str:
    payload = {'f': str(row.id), 'u': row.owner_id, 'w': what, 'aud': _AUDIENCE,
               'exp': datetime.now(UTC) + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(payload, _key(), algorithm=JWT_ALGORITHM)


def _read_token(token: str, what: str) -> dict | None:
    try:
        data = jwt.decode(token, _key(), algorithms=[JWT_ALGORITHM], audience=_AUDIENCE)
    except jwt.PyJWTError:
        return None
    return data if data.get('w') == what else None


def _me(ctx: AuthContext) -> str:
    return principal_key(ctx.user)


async def _mine(file_id: str, ctx: AuthContext) -> ProducedFile:
    row = await ProducedFile.get(file_id)
    if row is None or row.owner_id != _me(ctx):
        raise HTTPException(status_code=404, detail='File not found')
    return row


def _available(row: ProducedFile) -> bool:
    if row.stored and row.storage_key:
        return True
    return row.source == SERVER and os.path.isfile(row.path)


def _details(row: ProducedFile, agents: dict[str, str] | None = None) -> dict[str, Any]:
    out = service.ref(row)
    out.update({
        'info': row.info, 'created': row.created, 'session_id': row.session_id, 'task_id': row.task_id,
        'agent': {'id': row.agent_id, 'name': (agents or {}).get(row.agent_id, '')} if row.agent_id else None,
        'path': row.path, 'device_id': row.device_id, 'available': _available(row),
    })
    if out['available']:
        out['url'] = f'/api/v1/produced-file-content/{_token(row, "content")}'
    if row.thumb_key:
        out['thumb_url'] = f'/api/v1/produced-file-thumb/{_token(row, "thumb")}'
    return out


async def _agent_names(rows: list[ProducedFile]) -> dict[str, str]:
    ids = {r.agent_id for r in rows if r.agent_id}
    if not ids:
        return {}
    from cognitrix.models.agent import Agent
    names: dict[str, str] = {}
    for agent_id in ids:
        try:
            agent = await Agent.get(agent_id)
        except Exception:
            agent = None
        if agent is not None:
            names[agent_id] = str(getattr(agent, 'name', '') or '')
    return names


@produced_files_api.get('')
async def list_files(session_id: str = '', task_id: str = '', ctx: AuthContext = Depends(jwt_only)):
    rows = await service.for_chat(_me(ctx), session_id=session_id, task_id=task_id)
    rows = rows[:500]
    agents = await _agent_names(rows)
    return {'files': [_details(r, agents) for r in rows]}


@produced_files_api.get('/{file_id}')
async def get_file(file_id: str, fetch: bool = False, ctx: AuthContext = Depends(jwt_only)):
    """A file's details and signed links. With fetch, a file still in the
    sandbox or on a PC is copied in first (it may take a moment)."""
    row = await _mine(file_id, ctx)
    if fetch or (row.source == SERVER and not row.info_json):
        try:
            await service.local_path(row)
        except SourceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            logger.info('Could not copy in a produced file', exc_info=True)
            raise HTTPException(status_code=409, detail=f"Couldn't get {row.name}: {exc}")
        row = await ProducedFile.get(file_id) or row
    return _details(row, await _agent_names([row]))


@produced_files_api.get('/{file_id}/preview')
async def preview(file_id: str, ctx: AuthContext = Depends(jwt_only)):
    """What the side panel shows for a sheet, an archive or a text file."""
    row = await _mine(file_id, ctx)
    try:
        path = await service.local_path(row)
    except SourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        if row.kind == 'sheet':
            sheets = await asyncio.to_thread(previews.read_sheets, path)
            return {'kind': 'sheet', 'sheets': sheets}
        if row.kind == 'archive':
            return {'kind': 'archive', **(await asyncio.to_thread(previews.list_archive, path))}
        if row.kind in ('text', 'data', 'html', 'code') or row.mime_type.startswith('text/'):
            return {'kind': 'text', 'text': await asyncio.to_thread(previews.read_text, path)}
        if row.kind in ('doc', 'slides'):
            info, _ = await asyncio.to_thread(previews.make, path, row.kind)
            return {'kind': row.kind, **info}
    except Exception as exc:
        logger.info('Could not read a produced file for its preview', exc_info=True)
        raise HTTPException(status_code=422, detail=f"Couldn't read {row.name}: {exc}")
    return {'kind': row.kind}


@produced_files_api.delete('/{file_id}')
async def forget(file_id: str, ctx: AuthContext = Depends(jwt_only)):
    """Take a file off the lists and drop Cognitrix's copy (the original stays where it is)."""
    row = await _mine(file_id, ctx)
    await service.forget(row)
    return {'removed': True}


class ZipBody(BaseModel):
    ids: list[str]
    name: str = 'files'


@produced_files_api.post('/zip')
async def zip_files(body: ZipBody, ctx: AuthContext = Depends(jwt_only)):
    """Several files in one zip (the shelf's Download all)."""
    me = _me(ctx)
    rows = []
    for file_id in body.ids[:MAX_ZIP_FILES]:
        row = await ProducedFile.get(file_id)
        if row is not None and row.owner_id == me:
            rows.append(row)
    if not rows:
        raise HTTPException(status_code=404, detail='No files to download')
    paths: list[tuple[str, Any]] = []
    skipped: list[str] = []
    total = 0
    for row in rows:
        try:
            path = await service.local_path(row)
        except Exception as exc:
            skipped.append(f'{row.name}: {exc}')
            continue
        total += path.stat().st_size
        if total > storage.max_file_bytes() * 2:
            skipped.append(f'{row.name}: the zip would be too big')
            break
        paths.append((row.name, path))

    def build() -> bytes:
        buf = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, path in paths:
                base, ext = os.path.splitext(name)
                unique, n = name, 2
                while unique in used:
                    unique, n = f'{base} ({n}){ext}', n + 1
                used.add(unique)
                z.write(path, unique)
            if skipped:
                z.writestr('NOT INCLUDED.txt', '\n'.join(skipped))
        return buf.getvalue()

    data = await asyncio.to_thread(build)
    filename = storage.safe_name(body.name or 'files')
    if not filename.lower().endswith('.zip'):
        filename += '.zip'
    return Response(data, media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"',
                             'Cache-Control': 'private, no-store'})


# ------------------------------------------------------------------ signed links

_SAFE_HEADERS = {'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff',
                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
                 'Cross-Origin-Resource-Policy': 'cross-origin'}


@produced_file_content_api.get('/produced-file-content/{token}')
async def content(token: str, download: bool = False):
    data = _read_token(token, 'content')
    if data is None:
        raise HTTPException(status_code=404, detail='This link has expired. Open the file again.')
    row = await ProducedFile.get(str(data.get('f')))
    if row is None or row.owner_id != data.get('u'):
        raise HTTPException(status_code=404, detail='File not found')
    try:
        path = await service.local_path(row)
    except SourceError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    inline = not download and row.mime_type.startswith(_INLINE)
    media = row.mime_type if inline else 'application/octet-stream'
    headers = dict(_SAFE_HEADERS)
    from cognitrix.artifact_pages.frame import _ancestors
    if inline and media == 'application/pdf':
        # Browsers won't draw a PDF in a sandboxed document; it is shown in
        # a frame beside the chat, so only the app may frame it.
        headers['Content-Security-Policy'] = f"frame-ancestors {_ancestors()}"
    else:
        headers['Content-Security-Policy'] += f"; frame-ancestors {_ancestors()}"
    return FileResponse(path, media_type=media, filename=row.name,
                        content_disposition_type='inline' if inline else 'attachment', headers=headers)


@produced_file_content_api.get('/produced-file-thumb/{token}')
async def thumb(token: str):
    data = _read_token(token, 'thumb')
    if data is None:
        raise HTTPException(status_code=404, detail='This link has expired.')
    row = await ProducedFile.get(str(data.get('f')))
    if row is None or row.owner_id != data.get('u') or not row.thumb_key:
        raise HTTPException(status_code=404, detail='No thumbnail')
    try:
        path = storage.resolve(row.thumb_key)
    except ValueError:
        raise HTTPException(status_code=404, detail='No thumbnail')
    if not path.is_file():
        raise HTTPException(status_code=404, detail='No thumbnail')
    return FileResponse(path, media_type='image/jpeg', headers=_SAFE_HEADERS)
