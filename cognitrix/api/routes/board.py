"""The message board, as the Board page and the board CLI use it.

Signed in (the Board page), or with an API key that has the "board" scope
(the board CLI: an outside agent, named by the ``X-Board-Name`` header or
else the key's name).

GET    /api/board/me                     who I am on the board
GET    /api/board/boards                 the boards I can open, with counts
GET    /api/board/posts?board=…          posts on one board (kind, status, tag, thread filters; q searches;
                                         limit and before page through older posts)
GET    /api/board/events                 live: "changed" whenever a post on my boards changes
POST   /api/board/posts                  post (a "/name …" post runs that command)
GET    /api/board/posts/{id}             one post
POST   /api/board/posts/{id}/reply       answer (close: also mark done)
POST   /api/board/posts/{id}/claim       take on an open request or question
POST   /api/board/posts/{id}/unclaim
GET    /api/board/posts/{id}/wait        wait (up to 300 s) for an answer
POST   /api/board/posts/{id}/status      open or done
POST   /api/board/posts/{id}/pin         pin or unpin
PATCH  /api/board/posts/{id}             edit my own post
POST   /api/board/posts/{id}/retract     withdraw my own post
POST   /api/board/posts/{id}/remember    save a decision to the agents' memory
DELETE /api/board/posts/{id}             delete a post
GET    /api/board/inbox                  an outside agent's unread queue (marks it read)
GET    /api/board/mentions?board=…       who can be tagged with @name there (agents and people)
GET    /api/board/channels/{board}       a project channel's people and grants
PUT    /api/board/channels/{board}
GET    /api/board/schedules              repeating posts (POST, also for outside agents; PUT /{id}, DELETE /{id})
GET    /api/board/commands               /commands (PUT /{name}, DELETE /{name})
GET    /api/board/mirrors                Slack mirrors (POST, DELETE /{id})
GET    /api/board/settings               on/off, wakes a day, how long posts are kept
PUT    /api/board/settings
GET    /api/board/follows                what each of my agents hears about
PUT    /api/board/follows/{agent_id}

See ``cognitrix/board/``. Agents use the Board tool.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from cognitrix.agents import access as agent_access
from cognitrix.board import channels, commands, mirror, schedules, service
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only

board_api = APIRouter(prefix='/board')


class NewPost(BaseModel):
    board: str = 'agents'
    kind: str = 'heads_up'
    text: str = ''
    tags: list[str] | str | None = None
    to: str = ''
    thread: str = ''
    thread_title: str = ''


class Edit(BaseModel):
    text: str = ''


class Reply(BaseModel):
    text: str = ''
    close: bool = False


class Status(BaseModel):
    status: str


class Pin(BaseModel):
    pinned: bool


class Settings(BaseModel):
    enabled: bool | None = None
    wakes_per_day: int | None = None
    keep_days: int | None = None
    nudge_hours: int | None = None


class Follow(BaseModel):
    kinds: list[str] | None = None
    tags: list[str] | str | None = None
    wake: bool | None = None


class Channel(BaseModel):
    members: list[str] | None = None
    """User ids or emails."""
    agents: list[str] | None = None
    externals: list[str] | None = None


class Schedule(BaseModel):
    board: str = 'agents'
    text: str = ''
    to: str = ''
    kind: str = 'request'
    when: str = ''
    days: list[int] | None = None
    time: str = ''
    tz: str = ''
    thread: str = ''
    enabled: bool = True


class Command(BaseModel):
    agent_id: str = ''
    prompt: str = ''
    description: str = ''


class Mirror(BaseModel):
    board: str = 'agents'
    slack_channel: str = ''


@dataclass
class Caller:
    me: str
    """The signed-in person's key (for a key: the key's owner)."""
    author: service.Author
    outside: bool = False
    """An outside agent using an API key with the board scope."""


def _me(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


async def board_caller(request: Request, ctx: AuthContext = Depends(get_auth_context)) -> Caller:
    """Signed in, or an API key with the "board" scope (an outside agent)."""
    me = _me(ctx)
    if ctx.api_key is None:
        return Caller(me, await channels.person(me))
    if not ctx.has_scope('board'):
        raise HTTPException(status_code=403, detail='This key needs the "board" scope.')
    name = request.headers.get('x-board-name') or str(getattr(ctx.api_key, 'name', '') or 'Outside agent')
    author = await channels.register_external(me, name, str(ctx.api_key.id))
    return Caller(me, author, outside=True)


def _fail(exc: service.BoardError) -> HTTPException:
    missing = exc.code in ('post_not_found', 'board_not_found', 'not_found')
    forbidden = exc.code in ('not_granted', 'not_yours')
    return HTTPException(status_code=404 if missing else 403 if forbidden else 400, detail=exc.message)


async def _owner(caller: Caller, board: str) -> str:
    owner = await channels.owner_for(caller.me, board)
    if caller.outside and service.project_of(board):
        channel = await channels.get_channel(owner, board)
        if caller.author.name not in channel.externals:
            raise service.BoardError('not_granted', 'This outside agent was not added to that channel.')
    return owner


async def _post(caller: Caller, post_id: str) -> tuple[str, service.BoardPost]:
    owner, post = await channels.post_for(caller.me, post_id)
    await _owner(caller, post.board)
    return owner, post


def _show(caller: Caller, post: service.BoardPost) -> dict:
    return service.public_post(post, caller.me if not caller.outside else '')


def _host(caller: Caller) -> None:
    """Things only the board's owner, signed in, changes."""
    if caller.outside:
        raise HTTPException(status_code=403, detail='Outside agents cannot change this.')


@board_api.get('/me')
async def who_am_i(caller: Caller = Depends(board_caller)):
    return {'name': caller.author.name, 'kind': caller.author.kind, 'id': caller.author.id}


@board_api.get('/boards')
async def list_boards(caller: Caller = Depends(board_caller)):
    rows = await service.boards(caller.me)
    if caller.outside:
        allowed = set((await channels.inbox(caller.me, caller.author, peek=True))['boards'])
        rows = [r for r in rows if r['key'] in allowed]
    return rows


@board_api.get('/posts')
async def list_posts(board: str = 'agents', kind: str = '', status: str = '', tag: str = '', q: str = '',
                     thread: str = '', limit: int = service.LIST_MAX, before: int = 0,
                     caller: Caller = Depends(board_caller)):
    try:
        if board == '*' and q.strip() and not caller.outside:
            rows = await service.search(caller.me, '*', q, limit=service.LIST_MAX)
        else:
            key = service.clean_board(board)
            owner = await _owner(caller, key)
            if q.strip():
                rows = await service.search(owner, key, q, limit=service.LIST_MAX)
            else:
                rows = await service.list_posts(owner, key, kind=kind, status=status, tag=tag,
                                                limit=limit, before=before)
            if thread.strip():
                wanted = service.clean_thread(thread)
                rows = [r for r in rows if r.thread == wanted]
    except service.BoardError as exc:
        raise _fail(exc) from None
    return [_show(caller, row) for row in rows]


@board_api.post('/posts')
async def create_post(body: NewPost, caller: Caller = Depends(board_caller)):
    try:
        key = service.clean_board(body.board)
        owner = await _owner(caller, key)
        post = await service.create_post(owner, board=key, kind=body.kind, text=body.text, tags=body.tags,
                                         to=body.to, author=caller.author, thread=body.thread,
                                         thread_title=body.thread_title)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return _show(caller, post)


@board_api.get('/posts/{post_id}')
async def get_post(post_id: str, caller: Caller = Depends(board_caller)):
    try:
        _, post = await _post(caller, post_id)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return _show(caller, post)


@board_api.post('/posts/{post_id}/reply')
async def reply(post_id: str, body: Reply, caller: Caller = Depends(board_caller)):
    try:
        owner, _ = await _post(caller, post_id)
        post = await service.reply(owner, post_id, body.text, caller.author, close=body.close)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return _show(caller, post)


@board_api.post('/posts/{post_id}/claim')
async def claim(post_id: str, caller: Caller = Depends(board_caller)):
    try:
        owner, _ = await _post(caller, post_id)
        return _show(caller, await service.claim(owner, post_id, caller.author))
    except service.BoardError as exc:
        raise HTTPException(status_code=409, detail=exc.message) if exc.code == 'claimed' else _fail(exc) from None


@board_api.post('/posts/{post_id}/unclaim')
async def unclaim(post_id: str, caller: Caller = Depends(board_caller)):
    try:
        owner, _ = await _post(caller, post_id)
        return _show(caller, await service.unclaim(owner, post_id, caller.author))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.get('/posts/{post_id}/wait')
async def wait(post_id: str, seconds: float = 120, seen: int | None = None,
               caller: Caller = Depends(board_caller)):
    try:
        owner, _ = await _post(caller, post_id)
        post, new = await service.wait_for(owner, post_id, seconds=seconds, seen=seen)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {'post': _show(caller, post), 'new_replies': new, 'timed_out': not new}


async def _owned_post(caller: Caller, post_id: str) -> str:
    owner, _ = await _post(caller, post_id)
    return owner


@board_api.post('/posts/{post_id}/status')
async def set_status(post_id: str, body: Status, caller: Caller = Depends(board_caller)):
    try:
        owner = await _owned_post(caller, post_id)
        return _show(caller, await service.set_status(owner, post_id, body.status))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.post('/posts/{post_id}/pin')
async def set_pinned(post_id: str, body: Pin, caller: Caller = Depends(board_caller)):
    _host(caller)
    try:
        owner = await _owned_post(caller, post_id)
        return _show(caller, await service.set_pinned(owner, post_id, body.pinned))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.patch('/posts/{post_id}')
async def edit_post(post_id: str, body: Edit, caller: Caller = Depends(board_caller)):
    try:
        owner = await _owned_post(caller, post_id)
        return _show(caller, await service.edit_post(owner, post_id, body.text, caller.author))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.post('/posts/{post_id}/retract')
async def retract_post(post_id: str, caller: Caller = Depends(board_caller)):
    try:
        owner = await _owned_post(caller, post_id)
        return _show(caller, await service.retract_post(owner, post_id, caller.author))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.post('/posts/{post_id}/remember')
async def remember(post_id: str, ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    try:
        post, _ = await service.remember(me, post_id, service.PERSON)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return service.public_post(post, me)


PING_SECONDS = 20


@board_api.get('/events')
async def events(request: Request, ctx: AuthContext = Depends(jwt_only)):
    """Live changes on my boards, for an open Board page. Changes made in
    another process (a task worker) aren't seen here; the page still checks
    now and then."""
    me = _me(ctx)
    queue = service.listen(me)

    async def stream():
        try:
            yield {'event': 'message', 'data': json.dumps({'type': 'ready'})}
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    yield {'event': 'ping', 'data': ''}
                    continue
                yield {'event': 'message', 'data': json.dumps(item)}
        finally:
            service.unlisten(me, queue)

    return EventSourceResponse(stream())


def _lines(rows: list, caller: Caller) -> list[dict]:
    return [_show(caller, p) for p in rows]


@board_api.get('/inbox')
async def inbox(peek: bool = False, caller: Caller = Depends(board_caller)):
    if not caller.outside:
        raise HTTPException(status_code=400, detail='The inbox is for outside agents; agents here get a '
                                                    'note each turn, and you have your Inbox.')
    found = await channels.inbox(caller.me, caller.author, peek=peek)
    return {'name': caller.author.name, 'boards': found['boards'],
            **{k: _lines(found[k], caller) for k in ('for_you', 'tagged', 'answers', 'new')}}


@board_api.get('/mentions')
async def mentions(board: str = 'agents', caller: Caller = Depends(board_caller)):
    try:
        key = service.clean_board(board)
        owner = await _owner(caller, key)
    except service.BoardError as exc:
        raise _fail(exc) from None
    names = [{'name': name, 'kind': 'external' if ident.startswith('ext:') else 'agent'}
             for name, ident in await service.mention_names(owner, key)]
    if not service.swarm_of(key):
        taken = {n['name'].casefold() for n in names}
        names += [{'name': name, 'kind': 'person'} for name, _ in await channels.people_on(owner, key)
                  if name.casefold() not in taken]
    return names


@board_api.delete('/posts/{post_id}')
async def delete_post(post_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        await service.delete_post(_me(ctx), post_id)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {'deleted': True}


# ---------------------------------------------------------------------------
# Project channels
# ---------------------------------------------------------------------------

async def _project_key(me: str, board: str) -> str:
    key = service.clean_board(board)
    if not service.project_of(key) or await channels.project_for(me, key) is None:
        raise service.BoardError('board_not_found', 'No such project channel.')
    return key


@board_api.get('/channels/{board}')
async def get_channel(board: str, ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    try:
        key = service.clean_board(board)
        owner = await channels.owner_for(me, key)
        view = await channels.channel_view(owner, key)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {**view, 'owner': owner == me}


async def _member_ids(values: list[str]) -> list[str]:
    from cognitrix.models.user import User

    out = []
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        user = await User.find_one({'email': text.lower()}) if '@' in text else await User.get(text)
        if user is None and '@' in text:
            user = await User.find_one({'email': text})
        if user is None:
            raise service.BoardError('bad_member', f'No account for {text}. They need to sign up first.')
        out.append(str(user.id))
    return out


@board_api.put('/channels/{board}')
async def put_channel(board: str, body: Channel, ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    try:
        key = await _project_key(me, board)
        members = await _member_ids(body.members) if body.members is not None else None
        await channels.save_channel(me, key, members=members, agents=body.agents, externals=body.externals)
        return {**await channels.channel_view(me, key), 'owner': True}
    except service.BoardError as exc:
        raise _fail(exc) from None


# ---------------------------------------------------------------------------
# Scheduled posts, commands, Slack mirrors
# ---------------------------------------------------------------------------

@board_api.get('/schedules')
async def list_schedules(board: str = '', caller: Caller = Depends(board_caller)):
    _host(caller)
    return [schedules.public(r) for r in await schedules.list_schedules(caller.me, board)]


async def _save_schedule(caller: Caller, body: Schedule, schedule_id: str = '') -> dict:
    try:
        key = service.clean_board(body.board)
        owner = await _owner(caller, key)
        if caller.outside or owner != caller.me:
            await channels.check_grant(owner, key, caller.author)
        row = await schedules.save_schedule(
            owner, board=key, text=body.text, to=body.to, kind=body.kind, when=body.when, days=body.days,
            time=body.time, tz=body.tz, thread=body.thread, enabled=body.enabled,
            created_by=caller.author.name, schedule_id=schedule_id)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return schedules.public(row)


@board_api.post('/schedules')
async def create_schedule(body: Schedule, caller: Caller = Depends(board_caller)):
    """Anyone who may post on a board may make a repeating post there."""
    return await _save_schedule(caller, body)


@board_api.put('/schedules/{schedule_id}')
async def update_schedule(schedule_id: str, body: Schedule, caller: Caller = Depends(board_caller)):
    _host(caller)
    return await _save_schedule(caller, body, schedule_id)


@board_api.delete('/schedules/{schedule_id}')
async def delete_schedule(schedule_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        await schedules.delete_schedule(_me(ctx), schedule_id)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {'deleted': True}


async def _agent_names(me: str) -> dict[str, str]:
    return {str(a.id): str(a.name) for a in await agent_access.visible_agents(me) or []}


@board_api.get('/commands')
async def list_commands(caller: Caller = Depends(board_caller)):
    names = await _agent_names(caller.me)
    rows = [commands.public(r, names) for r in await commands.list_commands(caller.me)]
    taken = {r['name'] for r in rows}
    suggested = [{'name': n, 'description': d, 'prompt': p} for n, (d, p) in commands.SUGGESTED.items()
                 if n not in taken]
    return {'commands': rows, 'suggested': suggested}


@board_api.put('/commands/{name}')
async def put_command(name: str, body: Command, ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    try:
        row = await commands.save_command(me, name, body.agent_id, body.prompt, body.description)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return commands.public(row, await _agent_names(me))


@board_api.delete('/commands/{name}')
async def delete_command(name: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        await commands.delete_command(_me(ctx), name)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {'deleted': True}


@board_api.get('/mirrors')
async def list_mirrors(board: str = '', ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.models.chat_channel import ChatChannel

    me = _me(ctx)
    slack = await ChatChannel.find_one({'user_id': me, 'kind': 'slack'})
    return {'slack_connected': slack is not None,
            'mirrors': [mirror.public(r) for r in await mirror.list_mirrors(me, board)]}


@board_api.post('/mirrors')
async def add_mirror(body: Mirror, ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    try:
        key = await _project_key(me, body.board) if service.project_of(body.board) else body.board
        return mirror.public(await mirror.add_mirror(me, key, body.slack_channel))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.delete('/mirrors/{mirror_id}')
async def remove_mirror(mirror_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        await mirror.remove_mirror(_me(ctx), mirror_id)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {'deleted': True}


# ---------------------------------------------------------------------------
# Settings and follows
# ---------------------------------------------------------------------------

@board_api.get('/settings')
async def get_settings(ctx: AuthContext = Depends(jwt_only)):
    return service.public_settings(await service.get_settings(_me(ctx)))


@board_api.put('/settings')
async def put_settings(body: Settings, ctx: AuthContext = Depends(jwt_only)):
    try:
        row = await service.save_settings(_me(ctx), body.model_dump(exclude_none=True))
    except service.BoardError as exc:
        raise _fail(exc) from None
    return service.public_settings(row)


@board_api.get('/follows')
async def list_follows(ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    out = []
    for agent in await agent_access.visible_agents(me) or []:
        row = await service.get_follow(me, str(agent.id))
        out.append({**service.public_follow(row), 'agent_name': str(agent.name)})
    return out


@board_api.put('/follows/{agent_id}')
async def put_follow(agent_id: str, body: Follow, ctx: AuthContext = Depends(jwt_only)):
    me = _me(ctx)
    agent = await agent_access.find_visible_agent(me, agent_id=agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found')
    try:
        row = await service.save_follow(me, agent_id, kinds=body.kinds, tags=body.tags, wake=body.wake)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {**service.public_follow(row), 'agent_name': str(agent.name)}
