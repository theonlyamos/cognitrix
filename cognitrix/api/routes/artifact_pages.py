"""Artifacts: pages agents publish, plus the images and documents they made.

Browser sessions only (``jwt_only``), except the two public pieces at the end:
a public link's details and the sandboxed frame a page is shown in.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from cognitrix.artifact_pages import comments as page_comments
from cognitrix.artifact_pages import frame, store
from cognitrix.artifact_pages.models import ArtifactFile, ArtifactPage
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.session_ownership import principal_key

artifact_pages_api = APIRouter(prefix='/artifact-pages')
artifact_frame_api = APIRouter()


def _me(ctx: AuthContext) -> str:
    return principal_key(ctx.user)


def _bad(exc: store.PageError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


async def _page(page_id: str, ctx: AuthContext) -> tuple[ArtifactPage, str]:
    try:
        return await store.page_for(page_id, _me(ctx))
    except store.PageError as exc:
        raise _bad(exc)


def _frame_url(page: ArtifactPage, version: int, viewer: str) -> str:
    return f'/api/v1/artifact-frame/{frame.frame_token(str(page.id), version, viewer)}'


def _my_name(ctx: AuthContext) -> str:
    user = ctx.user
    return str(getattr(user, 'name', '') or getattr(user, 'email', '') or 'Someone')


# ------------------------------------------------------------------ list

def _image_row(row: Any, agents: dict[str, str]) -> dict[str, Any]:
    return {
        'id': str(row.id), 'kind': 'image', 'title': (row.prompt or row.filename or 'Generated image')[:120],
        'mime_type': row.mime_type, 'session_id': row.session_id,
        'agent': {'id': row.agent_id, 'name': agents.get(str(row.agent_id or ''), '')} if row.agent_id else None,
        'width': row.width, 'height': row.height, 'size_bytes': row.size_bytes,
        'created': row.created_at, 'updated': row.created_at,
    }


def _file_row(row: ArtifactFile, agents: dict[str, str]) -> dict[str, Any]:
    return {
        'id': str(row.id), 'kind': 'file', 'title': row.name, 'mime_type': row.mime_type,
        'path': row.path, 'session_id': row.session_id, 'task_id': row.task_id,
        'agent': {'id': row.agent_id, 'name': agents.get(row.agent_id, '')} if row.agent_id else None,
        'size_bytes': row.size_bytes, 'created': row.created, 'updated': row.updated,
    }


@artifact_pages_api.get('')
async def list_artifacts(ctx: AuthContext = Depends(jwt_only)):
    """Pages I can open, and the images and documents my agents made."""
    from cognitrix.artifacts import Artifact

    me = _me(ctx)
    people, agents = await store.names()
    counts = await store.open_comment_counts()
    pages = [store.project(page, role, people=people, agents=agents, comments=counts.get(str(page.id), 0))
             for page, role in await store.visible_pages(me)]
    images = []
    try:
        for row in await Artifact.find({'user_id': me}) or []:
            if row.run_id is None and (row.origin or 'generated') == 'generated' and row.mime_type.startswith('image/'):
                images.append(_image_row(row, agents))
    except Exception:
        images = []
    images.sort(key=lambda r: r['created'] or '', reverse=True)
    files = [_file_row(r, agents) for r in await ArtifactFile.find({'owner_id': me}) or []]
    files.sort(key=lambda r: r['updated'] or '', reverse=True)
    return {'pages': pages, 'images': images, 'files': files, 'public_links': store.public_links_allowed()}


@artifact_pages_api.get('/pinned')
async def pinned(ctx: AuthContext = Depends(jwt_only)):
    """Pages pinned to my dashboard, with a frame address for each."""
    me = _me(ctx)
    people, agents = await store.names()
    out = []
    for page, role in await store.visible_pages(me):
        if role == store.ROLE_OWNER and page.pinned:
            out.append({**store.project(page, role, people=people, agents=agents),
                        'frame_url': _frame_url(page, page.version, me),
                        'allowed_powers': await store.allowed_powers(page, me),
                        'task': await store.task_info(page.task_id)})
    return {'pages': out}


# ------------------------------------------------------------------ files

async def _my_file(file_id: str, ctx: AuthContext) -> ArtifactFile:
    row = await ArtifactFile.get(file_id)
    if row is None or row.owner_id != _me(ctx):
        raise HTTPException(status_code=404, detail='File not found')
    return row


@artifact_pages_api.get('/files/{file_id}')
async def get_file(file_id: str, download: bool = False, ctx: AuthContext = Depends(jwt_only)):
    from pathlib import Path

    row = await _my_file(file_id, ctx)
    path = Path(row.path)
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail='That file was moved or deleted')
    if path.stat().st_size > store.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail='That file is too big to open here')
    # Never rendered as a page in the app's own origin.
    mime = row.mime_type if download or not row.mime_type.startswith(('text/html', 'image/svg')) else 'text/plain'
    return FileResponse(path, media_type=mime, filename=row.name if download else None,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "default-src 'none'; sandbox"})


@artifact_pages_api.delete('/files/{file_id}')
async def forget_file(file_id: str, ctx: AuthContext = Depends(jwt_only)):
    """Take a file off the list (the file itself stays where it is)."""
    row = await _my_file(file_id, ctx)
    await ArtifactFile.delete_many({'id': str(row.id)})
    return {'removed': True}


class PublishFile(BaseModel):
    title: str | None = None


@artifact_pages_api.post('/files/{file_id}/publish')
async def publish_file(file_id: str, body: PublishFile | None = None, ctx: AuthContext = Depends(jwt_only)):
    """Turn a Markdown or HTML file into a page, so it can be shared and commented on."""
    from pathlib import Path

    row = await _my_file(file_id, ctx)
    fmt = {'text/markdown': 'markdown', 'text/plain': 'markdown', 'text/html': 'html'}.get(row.mime_type)
    if fmt is None:
        raise HTTPException(status_code=400, detail='Only Markdown, text and HTML files can become pages')
    path = Path(row.path)
    if not path.is_file() or path.stat().st_size > store.MAX_BYTES:
        raise HTTPException(status_code=404, detail='That file was moved, deleted or is too big')
    text = path.read_text(encoding='utf-8', errors='replace')
    if row.mime_type == 'text/plain':
        text = '```\n' + text + '\n```'
    try:
        page, _ = await store.publish(user_key=_me(ctx), title=(body.title if body else None) or row.name,
                                      content=text, fmt=fmt, agent_id=row.agent_id, session_id=row.session_id)
    except store.PageError as exc:
        raise _bad(exc)
    return {'id': str(page.id)}


# ------------------------------------------------------------------ one page

@artifact_pages_api.get('/{page_id}')
async def get_page(page_id: str, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    me = _me(ctx)
    people, agents = await store.names()
    counts = await store.open_comment_counts()
    shown = store.viewer_version(page, role)
    rows = await store.versions(page)
    return {
        **store.project(page, role, people=people, agents=agents, comments=counts.get(str(page.id), 0)),
        'frame_url': _frame_url(page, shown, me),
        'versions': [{
            'number': v.number, 'note': v.note, 'created': v.created, 'size_bytes': v.size_bytes,
            'by_task': bool(v.task_id),
        } for v in rows] if store.can_edit(role) else [{'number': shown, 'note': '', 'created': page.updated,
                                                         'size_bytes': page.size_bytes, 'by_task': False}],
        'task': await store.task_info(page.task_id) if page.task_id else None,
        'allowed_powers': await store.allowed_powers(page, me),
        'consent_asked': await store.consent(page, me) is not None,
        'share_version': page.share_version,
        'public_url': f'/s/{page.public_token}' if page.public and page.public_token and store.public_links_allowed() and role == store.ROLE_OWNER else '',
    }


def _version_for(page: ArtifactPage, role: str, v: int | None) -> int:
    if v is None or v == 0:
        return store.viewer_version(page, role)
    if not store.can_edit(role) and v != store.viewer_version(page, role):
        raise HTTPException(status_code=404, detail='Version not found')
    if v < 1 or v > page.version:
        raise HTTPException(status_code=404, detail='Version not found')
    return v


@artifact_pages_api.get('/{page_id}/frame')
async def frame_url(page_id: str, v: int | None = None, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    number = _version_for(page, role, v)
    return {'url': _frame_url(page, number, _me(ctx)), 'version': number, 'latest': page.version}


@artifact_pages_api.get('/{page_id}/source')
async def page_source(page_id: str, v: int | None = None, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    number = _version_for(page, role, v)
    try:
        return {'version': number, 'format': page.format, 'source': store.read_source(page, number)}
    except store.PageError as exc:
        raise _bad(exc)


class PageUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None


@artifact_pages_api.patch('/{page_id}')
async def update_page(page_id: str, body: PageUpdate, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    changes: dict[str, Any] = {}
    if body.title is not None:
        if not store.can_edit(role):
            raise HTTPException(status_code=403, detail='Only people who can edit it may rename it')
        title = ' '.join(body.title.split())[:store.MAX_TITLE]
        if not title:
            raise HTTPException(status_code=400, detail='A title is needed')
        changes['title'] = title
    if body.pinned is not None:
        if role != store.ROLE_OWNER:
            raise HTTPException(status_code=403, detail='Only its owner can pin it')
        changes['pinned'] = bool(body.pinned)
    if changes:
        await ArtifactPage.update_one({'id': str(page.id)}, changes)
    return {'ok': True, **changes}


class RestoreBody(BaseModel):
    version: int


@artifact_pages_api.post('/{page_id}/restore')
async def restore_version(page_id: str, body: RestoreBody, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    if not store.can_edit(role):
        raise HTTPException(status_code=403, detail='Only people who can edit it may restore a version')
    try:
        page = await store.restore(page, body.version, page.owner_id)
    except store.PageError as exc:
        raise _bad(exc)
    return {'version': page.version}


@artifact_pages_api.delete('/{page_id}')
async def delete_page(page_id: str, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    if role != store.ROLE_OWNER:
        raise HTTPException(status_code=403, detail='Only its owner can delete it')
    await store.delete_page(page)
    return {'deleted': True}


# ------------------------------------------------------------------ sharing

class ShareEntry(BaseModel):
    user_id: str
    role: Literal['view', 'edit'] = 'view'


class SharingUpdate(BaseModel):
    people: list[ShareEntry] | None = None
    everyone: Literal['view', 'edit', ''] | None = None
    public: bool | None = None
    share_version: int | None = None


async def _sharing_view(page: ArtifactPage, me: str) -> dict[str, Any]:
    from cognitrix.api.routes.agents import _people, _person

    directory = {p['id']: p for p in (_person(u) for u in await _people())}
    given = store.shares(page)
    return {
        'people': [{**directory.get(key, {'id': key, 'name': 'Someone who left', 'email': ''}), 'role': role}
                   for key, role in given.items() if key != store.EVERYONE],
        'everyone': given.get(store.EVERYONE, ''),
        'public': bool(page.public) and store.public_links_allowed(),
        'public_url': f'/s/{page.public_token}' if page.public and page.public_token else '',
        'public_links': store.public_links_allowed(),
        'share_version': page.share_version,
        'version': page.version,
        'directory': sorted((p for key, p in directory.items() if key != me), key=lambda p: p['name'].lower()),
    }


@artifact_pages_api.get('/{page_id}/sharing')
async def get_sharing(page_id: str, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    if role != store.ROLE_OWNER:
        raise HTTPException(status_code=403, detail='Only its owner can share it')
    return await _sharing_view(page, _me(ctx))


@artifact_pages_api.put('/{page_id}/sharing')
async def set_sharing(page_id: str, body: SharingUpdate, ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.agents.access import owner_key
    from cognitrix.api.routes.agents import _people

    page, role = await _page(page_id, ctx)
    if role != store.ROLE_OWNER:
        raise HTTPException(status_code=403, detail='Only its owner can share it')
    people = None
    if body.people is not None:
        known = {owner_key(u) for u in await _people()}
        people = {}
        for entry in body.people[:200]:
            if entry.user_id not in known:
                raise HTTPException(status_code=400, detail='That person has no account on this server')
            people[entry.user_id] = entry.role
    try:
        page = await store.set_sharing(page, people=people, everyone=body.everyone, public=body.public,
                                       share_version=body.share_version)
    except store.PageError as exc:
        raise _bad(exc)
    return await _sharing_view(page, _me(ctx))


@artifact_pages_api.get('/{page_id}/channels')
async def chat_apps(page_id: str, ctx: AuthContext = Depends(jwt_only)):
    """My linked chat apps (Slack, Telegram, ...) a link can be sent to."""
    from cognitrix.models.chat_channel import ChatChannel

    await _page(page_id, ctx)
    rows = await ChatChannel.find({'user_id': _me(ctx)}) or []
    return {'channels': [{'id': str(r.id), 'kind': r.kind, 'name': r.bot_name or r.kind.title()}
                         for r in rows if r.enabled and getattr(r, 'chat_id', '')]}


class SendBody(BaseModel):
    channel_id: str
    url: str


@artifact_pages_api.post('/{page_id}/send')
async def send_link(page_id: str, body: SendBody, ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.channels.service import get_service
    from cognitrix.models.chat_channel import ChatChannel

    page, _ = await _page(page_id, ctx)
    channel = await ChatChannel.get(body.channel_id)
    if channel is None or channel.user_id != _me(ctx) or not getattr(channel, 'chat_id', ''):
        raise HTTPException(status_code=404, detail='Chat app not found')
    if not body.url.startswith(('http://', 'https://')) or len(body.url) > 500:
        raise HTTPException(status_code=400, detail='Not a link')
    service = get_service()
    if service is None:
        raise HTTPException(status_code=503, detail='Chat apps are not running')
    sent = await service._push(channel, f'{page.title}\n{body.url}')
    if not sent:
        raise HTTPException(status_code=502, detail="The chat app didn't take the message")
    return {'sent': True}


# ------------------------------------------------------------------ comments

class CommentBody(BaseModel):
    body: str
    thread_id: str = ''
    anchor: dict | None = None
    version: int = 0
    send_to_agent: bool = False


@artifact_pages_api.get('/{page_id}/comments')
async def list_comments(page_id: str, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    rows = await page_comments.thread_rows(str(page.id))
    return {'threads': page_comments.threads(rows), 'can_send': store.can_edit(role) and bool(page.agent_id)}


@artifact_pages_api.post('/{page_id}/comments')
async def add_comment(page_id: str, body: CommentBody, ctx: AuthContext = Depends(jwt_only)):
    page, role = await _page(page_id, ctx)
    me = _me(ctx)
    people, agents = await store.names()
    agent_name = agents.get(page.agent_id, '')
    wants_agent = body.send_to_agent or page_comments.mentions_agent(body.body, agent_name)
    may_send = store.can_edit(role) and bool(page.agent_id)
    try:
        row = await page_comments.add(page, author_id=me, author_name=_my_name(ctx), body=body.body,
                                      thread_id=body.thread_id, anchor=body.anchor,
                                      version=body.version or page.version,
                                      send_to_agent=wants_agent and may_send)
    except store.PageError as exc:
        raise _bad(exc)
    status = ''
    if wants_agent and may_send:
        status = await page_comments.send_to_agent(page, row)
        if status != 'sent':
            from cognitrix.artifact_pages.models import ArtifactComment

            await ArtifactComment.update_one({'id': str(row.id)}, {'sent_to_agent': False})
    elif wants_agent and body.send_to_agent:
        status = 'Only people who can edit this artifact can send comments to its agent.'
    await page_comments.notify(page, row, people=people)
    rows = await page_comments.thread_rows(str(page.id))
    return {'id': str(row.id), 'agent': status, 'threads': page_comments.threads(rows)}


class ResolveBody(BaseModel):
    resolved: bool = True


@artifact_pages_api.post('/{page_id}/comments/{thread_id}/resolve')
async def resolve_thread(page_id: str, thread_id: str, body: ResolveBody, ctx: AuthContext = Depends(jwt_only)):
    page, _ = await _page(page_id, ctx)
    try:
        await page_comments.set_resolved(page, thread_id, body.resolved)
    except store.PageError as exc:
        raise _bad(exc)
    rows = await page_comments.thread_rows(str(page.id))
    return {'threads': page_comments.threads(rows)}


# ------------------------------------------------------------------ page powers

class ConsentBody(BaseModel):
    allowed: list[str] = []


@artifact_pages_api.put('/{page_id}/consent')
async def set_consent(page_id: str, body: ConsentBody, ctx: AuthContext = Depends(jwt_only)):
    page, _ = await _page(page_id, ctx)
    return {'allowed': await store.set_consent(page, _me(ctx), body.allowed)}


class BridgeCall(BaseModel):
    kind: str
    payload: dict = {}


ASK_MAX_PROMPT = 8000


@artifact_pages_api.post('/{page_id}/bridge')
async def bridge(page_id: str, body: BridgeCall, ctx: AuthContext = Depends(jwt_only)):
    """A call a page made through ``window.cognitrix``, relayed by the viewer's window."""
    page, _ = await _page(page_id, ctx)
    me = _me(ctx)
    power = body.kind.split('.', 1)[0]
    if power not in (page.powers or []):
        raise HTTPException(status_code=403, detail=f'This page did not ask to use "{power}"')
    if power not in await store.allowed_powers(page, me):
        raise HTTPException(status_code=403, detail=f'You have not allowed this page to use "{power}"')
    payload = body.payload or {}
    try:
        if body.kind == 'ask':
            return {'value': await _ask(page, me, str(payload.get('prompt') or ''))}
        if body.kind.startswith('data.'):
            scope = 'shared' if payload.get('shared') else 'mine'
            if body.kind == 'data.get':
                return {'value': await store.data_get(page, me, scope, str(payload.get('key') or ''))}
            if body.kind == 'data.list':
                return {'value': await store.data_get(page, me, scope, None)}
            if body.kind == 'data.set':
                value = payload.get('value')
                await store.data_set(page, me, scope, str(payload.get('key') or ''),
                                     value if isinstance(value, str) else json.dumps(value))
                return {'value': True}
            if body.kind == 'data.remove':
                await store.data_set(page, me, scope, str(payload.get('key') or ''), None)
                return {'value': True}
        if body.kind == 'apps.list':
            return {'value': await _apps_list(me)}
        if body.kind == 'apps.call':
            return {'value': await _apps_call(me, str(payload.get('app') or ''), str(payload.get('tool') or ''),
                                              payload.get('arguments') or {})}
    except store.PageError as exc:
        raise _bad(exc)
    raise HTTPException(status_code=400, detail='Unknown call')


async def _ask(page: ArtifactPage, me: str, prompt: str) -> str:
    from cognitrix.channels.formatting import strip_reasoning
    from cognitrix.meetings.notes import MeetingError, _agent_llm

    prompt = prompt.strip()[:ASK_MAX_PROMPT]
    if not prompt:
        raise store.PageError('empty', 'Ask something')
    try:
        # The viewer's own agent answers (and their model key pays for it):
        # the page's agent if they can use it, otherwise their main one.
        agent = await _agent_llm(me, page.agent_id)
    except MeetingError as exc:
        raise store.PageError('no_agent', str(exc))
    system = (f'You answer questions asked from inside an interactive page titled "{page.title}". '
              'Reply with just the answer, briefly, in plain text unless the question asks for a format.')
    response = await agent.llm([
        {'role': 'system', 'type': 'text', 'content': system},
        {'role': 'user', 'type': 'text', 'content': prompt},
    ], stream=False)
    if response is None or getattr(response, 'error', None):
        raise store.PageError('model_error', "The model couldn't be reached", 502)
    return strip_reasoning(str(getattr(response, 'llm_response', '') or '')).strip()


async def _my_apps(me: str) -> list:
    from cognitrix.mcp.registry import config_from_row
    from cognitrix.models.mcp_server import MCPServer

    rows = await MCPServer.find({'owner_id': me}) or []
    return [config_from_row(r) for r in rows if getattr(r, 'enabled', True)]


async def _apps_list(me: str) -> list[dict[str, Any]]:
    from cognitrix.mcp import apps
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.mcp.registry import prepare
    from cognitrix.safety.destructive_ops import mcp_only_reads

    client = await get_dynamic_client()
    out = []
    for config in await _my_apps(me):
        if await prepare(client, config):
            out.append({'app': config.name, 'connected': False, 'tools': []})
            continue
        tools = []
        for info in await apps.list_tools(client, config):
            name = str(info.get('name') or '')
            if mcp_only_reads(name, info.get('annotations')):
                tools.append({'name': name, 'description': str(info.get('description') or '')[:300]})
        out.append({'app': config.name, 'connected': True, 'tools': tools})
    return out


async def _apps_call(me: str, app: str, tool_name: str, arguments: Any) -> Any:
    from cognitrix.mcp import apps
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.mcp.registry import matches, prepare
    from cognitrix.safety.destructive_ops import mcp_only_reads
    from cognitrix.tools.mcp import _content_to_text

    config = next((c for c in await _my_apps(me) if matches(c, app)), None)
    if config is None:
        raise store.PageError('no_app', f'You have no connected app called "{app}"', 404)
    client = await get_dynamic_client()
    problem = await prepare(client, config)
    if problem:
        raise store.PageError('app_offline', problem, 502)
    known = {str(t.get('name')): t for t in await apps.list_tools(client, config)}
    info = known.get(tool_name)
    if info is None:
        raise store.PageError('no_tool', f'"{app}" has no tool called "{tool_name}"', 404)
    # Pages only read: any change, even one that can be undone, stays with agents and their approvals.
    if not mcp_only_reads(tool_name, info.get('annotations')):
        raise store.PageError('not_read_only', f'"{tool_name}" can change things, so pages may not call it', 403)
    result = await apps.call_tool(client, config, tool_name, arguments if isinstance(arguments, dict) else {})
    text = _content_to_text(result)
    try:
        return json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        return text


# ------------------------------------------------------------------ public + frames (no sign-in)

def _frame_response(page: ArtifactPage, number: int) -> HTMLResponse:
    try:
        source = store.read_source(page, number)
    except store.PageError as exc:
        raise _bad(exc)
    return HTMLResponse(
        frame.render(source, page.format, page.title),
        headers={
            'Content-Security-Policy': frame.content_security_policy(),
            'Cache-Control': 'private, max-age=60',
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
        },
    )


@artifact_frame_api.get('/artifact-frame/{token}', include_in_schema=False)
async def page_frame(token: str):
    claims = frame.read_token(token)
    if not claims:
        raise HTTPException(status_code=404, detail='This link expired. Reload the page.')
    page = await store.get_page(str(claims.get('p') or ''))
    if page is None or store.role_of(page, str(claims.get('u') or '')) is None:
        raise HTTPException(status_code=404, detail='Artifact not found')
    return _frame_response(page, int(claims.get('v') or page.version))


@artifact_frame_api.get('/artifact-frame/public/{public_token}/{version}', include_in_schema=False)
async def public_frame(public_token: str, version: int):
    page = await store.page_by_public_token(public_token)
    if page is None or version != store.viewer_version(page, None):
        raise HTTPException(status_code=404, detail='Artifact not found')
    return _frame_response(page, version)


@artifact_frame_api.get('/public/artifacts/{public_token}')
async def public_page(public_token: str):
    page = await store.page_by_public_token(public_token)
    if page is None:
        raise HTTPException(status_code=404, detail='This link was turned off or never existed')
    people, agents = await store.names()
    number = store.viewer_version(page, None)
    return {
        'title': page.title, 'format': page.format, 'version': number, 'updated': page.updated,
        'owner': people.get(page.owner_id, ''), 'agent': agents.get(page.agent_id, ''),
        'frame_url': f'/api/v1/artifact-frame/public/{public_token}/{number}',
        'downloads': 'download' in (page.powers or []),
    }
