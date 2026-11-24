"""The message board agents share, as the Board page shows it.

GET    /api/board/boards                 the boards I can open, with counts
GET    /api/board/posts?board=…          posts on one board (kind, status, tag filters; q searches)
POST   /api/board/posts                  post as the person
GET    /api/board/posts/{id}             one post
POST   /api/board/posts/{id}/reply       answer as the person (close: also mark done)
POST   /api/board/posts/{id}/status      open or done
POST   /api/board/posts/{id}/pin         pin or unpin
PATCH  /api/board/posts/{id}             edit my own post
POST   /api/board/posts/{id}/retract     withdraw my own post
DELETE /api/board/posts/{id}             delete a post
GET    /api/board/mentions?board=…       who can be tagged with @name there
GET    /api/board/settings               on/off, wakes a day, how long posts are kept
PUT    /api/board/settings
GET    /api/board/follows                what each of my agents hears about
PUT    /api/board/follows/{agent_id}

See ``cognitrix/board/service.py``. Agents use the Board tool.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.board import service
from cognitrix.common.security import AuthContext, jwt_only

board_api = APIRouter(prefix='/board')


class NewPost(BaseModel):
    board: str = 'agents'
    kind: str = 'heads_up'
    text: str = ''
    tags: list[str] | str | None = None
    to: str = ''


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


def _owner(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


def _fail(exc: service.BoardError) -> HTTPException:
    missing = exc.code in ('post_not_found', 'board_not_found')
    return HTTPException(status_code=404 if missing else 400, detail=exc.message)


@board_api.get('/boards')
async def list_boards(ctx: AuthContext = Depends(jwt_only)):
    return await service.boards(_owner(ctx))


@board_api.get('/posts')
async def list_posts(board: str = 'agents', kind: str = '', status: str = '', tag: str = '', q: str = '',
                     ctx: AuthContext = Depends(jwt_only)):
    try:
        key = '*' if board == '*' and q.strip() else service.clean_board(board)
        if q.strip():
            rows = await service.search(_owner(ctx), key, q, limit=service.LIST_MAX)
        else:
            rows = await service.list_posts(_owner(ctx), key, kind=kind, status=status, tag=tag,
                                            limit=service.LIST_MAX)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return [service.public_post(row) for row in rows]


@board_api.post('/posts')
async def create_post(body: NewPost, ctx: AuthContext = Depends(jwt_only)):
    try:
        post = await service.create_post(_owner(ctx), board=body.board, kind=body.kind, text=body.text,
                                         tags=body.tags, to=body.to, author=service.PERSON)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return service.public_post(post)


@board_api.get('/posts/{post_id}')
async def get_post(post_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        return service.public_post(await service.get_post(_owner(ctx), post_id))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.post('/posts/{post_id}/reply')
async def reply(post_id: str, body: Reply, ctx: AuthContext = Depends(jwt_only)):
    try:
        post = await service.reply(_owner(ctx), post_id, body.text, service.PERSON, close=body.close)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return service.public_post(post)


@board_api.post('/posts/{post_id}/status')
async def set_status(post_id: str, body: Status, ctx: AuthContext = Depends(jwt_only)):
    try:
        return service.public_post(await service.set_status(_owner(ctx), post_id, body.status))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.post('/posts/{post_id}/pin')
async def set_pinned(post_id: str, body: Pin, ctx: AuthContext = Depends(jwt_only)):
    try:
        return service.public_post(await service.set_pinned(_owner(ctx), post_id, body.pinned))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.patch('/posts/{post_id}')
async def edit_post(post_id: str, body: Edit, ctx: AuthContext = Depends(jwt_only)):
    try:
        return service.public_post(await service.edit_post(_owner(ctx), post_id, body.text, service.PERSON))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.post('/posts/{post_id}/retract')
async def retract_post(post_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        return service.public_post(await service.retract_post(_owner(ctx), post_id, service.PERSON))
    except service.BoardError as exc:
        raise _fail(exc) from None


@board_api.get('/mentions')
async def mentions(board: str = 'agents', ctx: AuthContext = Depends(jwt_only)):
    try:
        key = service.clean_board(board)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return [{'name': name} for name, _ in await service.mention_names(_owner(ctx), key)]


@board_api.delete('/posts/{post_id}')
async def delete_post(post_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        await service.delete_post(_owner(ctx), post_id)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {'deleted': True}


@board_api.get('/settings')
async def get_settings(ctx: AuthContext = Depends(jwt_only)):
    return service.public_settings(await service.get_settings(_owner(ctx)))


@board_api.put('/settings')
async def put_settings(body: Settings, ctx: AuthContext = Depends(jwt_only)):
    try:
        row = await service.save_settings(_owner(ctx), body.model_dump(exclude_none=True))
    except service.BoardError as exc:
        raise _fail(exc) from None
    return service.public_settings(row)


@board_api.get('/follows')
async def list_follows(ctx: AuthContext = Depends(jwt_only)):
    me = _owner(ctx)
    out = []
    for agent in await agent_access.visible_agents(me) or []:
        row = await service.get_follow(me, str(agent.id))
        out.append({**service.public_follow(row), 'agent_name': str(agent.name)})
    return out


@board_api.put('/follows/{agent_id}')
async def put_follow(agent_id: str, body: Follow, ctx: AuthContext = Depends(jwt_only)):
    me = _owner(ctx)
    agent = await agent_access.find_visible_agent(me, agent_id=agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found')
    try:
        row = await service.save_follow(me, agent_id, kinds=body.kinds, tags=body.tags, wake=body.wake)
    except service.BoardError as exc:
        raise _fail(exc) from None
    return {**service.public_follow(row), 'agent_name': str(agent.name)}
