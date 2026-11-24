"""Project channels, the people on a board, outside agents, and who may post where.

* Each project has a channel, ``project:<id>``. Its owner and the people they
  add (``BoardChannel.members``) read and post there; threads (``task:<id>``)
  keep the posts about one task together.
* Every person on a board has a name (their account name), so agents and
  people can tag them with ``@Name``; a tagged person hears about it in their
  Inbox.
* Agents only post in channels they are granted: the project's own agents,
  plus any added to the channel. Outside agents (the board CLI with a key
  that has the "board" scope) join under a name and must be added to a
  project channel before they post there. Their unread queue is ``inbox``.
"""
from __future__ import annotations

import logging
from typing import Any

from cognitrix.board.models import BoardAgent, BoardChannel, BoardCursor, BoardPost

logger = logging.getLogger('cognitrix.log')

INBOX_MAX = 30


def _service():
    from cognitrix.board import service

    return service


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

async def _user(user_id: str):
    from cognitrix.models.user import User

    try:
        return await User.get(str(user_id)) if str(user_id or '').strip() else None
    except Exception:
        return None


def display_name(user: Any) -> str:
    """What a person is called on the board: their name, else the part of
    their email before the @."""
    name = ' '.join(str(getattr(user, 'name', '') or '').split())
    if name:
        return name[:40]
    email = str(getattr(user, 'email', '') or '')
    return (email.split('@', 1)[0] or 'Someone')[:40]


async def person(user_id: str) -> Any:
    """The Author for a signed-in person."""
    service = _service()
    user = await _user(user_id)
    return service.Author(kind='person', id=str(user_id), name=display_name(user) if user else 'You')


async def people_on(owner: str, board: str) -> list[tuple[str, str]]:
    """(name, user id) of each person on a board: the owner, and on a
    project channel the people added to it."""
    ids = [str(owner)]
    if _service().project_of(board):
        channel = await get_channel(owner, board)
        ids += [m for m in channel.members if m not in ids]
    out = []
    for uid in ids:
        user = await _user(uid)
        if user is not None:
            out.append((display_name(user), uid))
    return out


async def tell_people(owner: str, post: BoardPost, people: list[dict[str, str]], by: str, text: str) -> None:
    """Inbox notes for the people tagged in a post or an answer."""
    from cognitrix.tasks.automation import create_notification

    service = _service()
    for p in people:
        uid = str(p.get('id') or '')
        if not uid:
            continue
        await create_notification(uid, f'{by} tagged you on the board', service._clip(text, 400),
                                  link=service._link(post))


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

async def project_for(owner: str, board: str):
    from cognitrix.projects.models import Project

    pid = _service().project_of(board)
    if not pid:
        return None
    try:
        row = await Project.get(pid)
    except Exception:
        row = None
    return row if row is not None and str(row.user_id) == str(owner) else None


async def get_channel(owner: str, board: str) -> BoardChannel:
    row = await BoardChannel.find_one({'user_id': str(owner), 'key': board})
    return row or BoardChannel(user_id=str(owner), key=board, project_id=_service().project_of(board))


async def save_channel(owner: str, board: str, *, members: list[str] | None = None,
                       agents: list[str] | None = None, externals: list[str] | None = None) -> BoardChannel:
    service = _service()
    if await project_for(owner, board) is None:
        raise service.BoardError('board_not_found', 'No such project.')
    data: dict[str, Any] = {}
    if members is not None:
        data['members'] = [m for m in dict.fromkeys(str(m) for m in members if str(m or '').strip())
                           if m != str(owner)][:50]
    if agents is not None:
        data['agents'] = list(dict.fromkeys(str(a) for a in agents if str(a or '').strip()))[:100]
    if externals is not None:
        data['externals'] = list(dict.fromkeys(' '.join(str(e).split())[:40] for e in externals
                                               if str(e or '').strip()))[:50]
    async with service._lock(f'channel:{owner}:{board}'):
        row = await BoardChannel.find_one({'user_id': str(owner), 'key': board})
        if row is None:
            row = BoardChannel(user_id=str(owner), key=board, project_id=service.project_of(board),
                               created_at=service.now_ms(), **data)
            await row.save()
            return row
        if data:
            await BoardChannel.update_one({'id': row.id}, data)
            for key, value in data.items():
                setattr(row, key, value)
    return row


async def channel_view(owner: str, board: str) -> dict[str, Any]:
    from cognitrix.agents.access import visible_agents

    project = await project_for(owner, board)
    channel = await get_channel(owner, board)
    agents = {str(a.id): str(a.name) for a in await visible_agents(owner) or []}
    members = []
    for uid in channel.members:
        user = await _user(uid)
        if user is not None:
            members.append({'id': uid, 'name': display_name(user), 'email': str(getattr(user, 'email', '') or '')})
    project_agents = [str(a) for a in (getattr(project, 'agents', None) or [])]
    return {
        'board': board, 'project_id': _service().project_of(board),
        'title': str(getattr(project, 'name', '') or 'Project'),
        'members': members,
        'project_agents': [{'id': a, 'name': agents.get(a, a)} for a in project_agents if a in agents],
        'agents': [{'id': a, 'name': agents.get(a, a)} for a in channel.agents if a in agents],
        'externals': list(channel.externals),
        'known_externals': [r.name for r in await BoardAgent.find({'user_id': str(owner)}) or []],
    }


async def member_channels(user_id: str) -> list[BoardChannel]:
    """Project channels other people added this person to."""
    rows = await BoardChannel.find({}) or []
    return [r for r in rows if str(user_id) in r.members]


async def owner_for(me: str, board: str) -> str:
    """Whose board this is, for someone signed in as ``me``: their own, or
    the owner of a project channel they were added to."""
    service = _service()
    if not service.project_of(board):
        return str(me)
    if await project_for(me, board) is not None:
        return str(me)
    for row in await BoardChannel.find({'key': board}) or []:
        if str(me) in row.members:
            return str(row.user_id)
    raise service.BoardError('board_not_found', 'No such channel, or you were not added to it.')


async def post_for(me: str, post_id: str) -> tuple[str, BoardPost]:
    """(owner, post) for a post ``me`` can see."""
    service = _service()
    row = await BoardPost.get(str(post_id or '').strip()) if str(post_id or '').strip() else None
    if row is not None and row.user_id != str(me) and service.project_of(row.board):
        try:
            if await owner_for(me, row.board) == row.user_id:
                return row.user_id, row
        except service.BoardError:
            pass
    return str(me), await service.get_post(me, post_id)


# ---------------------------------------------------------------------------
# Who may post where
# ---------------------------------------------------------------------------

async def check_grant(owner: str, board: str, author: Any) -> None:
    """Agents only post in channels they are granted (people always may)."""
    service = _service()
    if not service.project_of(board) or author.kind == 'person':
        return
    project = await project_for(owner, board)
    if project is None:
        raise service.BoardError('board_not_found', 'That project no longer exists.')
    channel = await get_channel(owner, board)
    if author.kind == 'external':
        if author.name in channel.externals:
            return
    elif author.id and (author.id in [str(a) for a in project.agents or []] or author.id in channel.agents):
        return
    raise service.BoardError('not_granted', f'You are not granted the "{project.name}" channel. '
                             'Ask the person to add you to it, or post on the "agents" board.')


async def granted_agents(owner: str, board: str) -> set[str]:
    project = await project_for(owner, board)
    if project is None:
        return set()
    channel = await get_channel(owner, board)
    return {str(a) for a in project.agents or []} | set(channel.agents)


# ---------------------------------------------------------------------------
# Outside agents
# ---------------------------------------------------------------------------

async def register_external(owner: str, name: str, key_id: str = '') -> Any:
    """Note that an outside agent used the board (so it can be tagged and granted)."""
    service = _service()
    author = service.external(name)
    row = await BoardAgent.find_one({'user_id': str(owner), 'name': author.name})
    stamp = service.now_ms()
    if row is None:
        await BoardAgent(user_id=str(owner), name=author.name, key_id=str(key_id or ''), last_seen=stamp).save()
    else:
        await BoardAgent.update_one({'id': row.id}, {'last_seen': stamp, 'key_id': str(key_id or row.key_id)})
    return author


async def externals_of(owner: str) -> list[tuple[str, str]]:
    return [(r.name, f'ext:{r.name}') for r in await BoardAgent.find({'user_id': str(owner)}) or []]


async def inbox(owner: str, author: Any, *, peek: bool = False) -> dict[str, Any]:
    """An outside agent's unread queue: open questions and requests for it,
    posts tagging it, answers to its posts, and new posts on boards it may
    use, since it last looked. Marks them read unless ``peek``."""
    service = _service()
    key = f'ext:{author.name}'
    cursor = await BoardCursor.find_one({'user_id': str(owner), 'key': key})
    seen = cursor.seen_at if cursor else 0
    boards = ['agents']
    for row in await BoardChannel.find({'user_id': str(owner)}) or []:
        if author.name in row.externals:
            boards.append(row.key)
    posts: list[BoardPost] = []
    for board in boards:
        posts += [p for p in await service.posts_on(owner, board) if not p.retracted]
    posts.sort(key=lambda p: p.posted_at, reverse=True)

    def tagged(p: BoardPost) -> bool:
        return any(isinstance(m, dict) and m.get('id') == author.id for m in p.mentions) or any(
            isinstance(m, dict) and m.get('id') == author.id for r in p.replies for m in r.get('mentions') or [])

    for_me = [p for p in posts if p.status == 'open' and p.to_id == author.id]
    mentions = [p for p in posts if tagged(p) and p.changed_at > seen and p not in for_me]
    answers = [p for p in posts if p.author_id == author.id and p.replies and p.changed_at > seen]
    fresh = [p for p in posts if p.posted_at > seen and p.author_id != author.id
             and p not in for_me and p not in mentions][:INBOX_MAX]
    if not peek:
        stamp = service.now_ms()
        if cursor is None:
            await BoardCursor(user_id=str(owner), key=key, seen_at=stamp).save()
        else:
            await BoardCursor.update_one({'id': cursor.id}, {'seen_at': stamp})
    return {'for_you': for_me[:INBOX_MAX], 'tagged': mentions[:INBOX_MAX], 'answers': answers[:INBOX_MAX],
            'new': fresh, 'boards': boards}


async def boards_for_member(user_id: str) -> list[dict[str, Any]]:
    """Channels of other people's projects that this person was added to."""
    from cognitrix.projects.models import Project

    out = []
    for row in await member_channels(user_id):
        try:
            project = await Project.get(row.project_id)
        except Exception:
            project = None
        if project is None:
            continue
        posts = await _service().posts_on(row.user_id, row.key)
        out.append({'key': row.key, 'kind': 'project', 'title': str(project.name), 'status': '',
                    'project_id': row.project_id, 'shared': True, 'total': len(posts),
                    'open': sum(1 for p in posts if p.status == 'open'),
                    'latest': _service().iso(posts[0].posted_at) if posts else ''})
    return out
