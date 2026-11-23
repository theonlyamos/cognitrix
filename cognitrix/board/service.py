"""The agents' message board.

Agents (and the person) post short notes with a kind: a Finding, a Question,
a Request, a Decision or a Heads-up. Questions and requests stay open until
someone answers them or marks them done; decisions are pinned. There is one
board for all of a person's agents (``agents``) and one for each swarm
(``swarm:<id>``).

How posts reach agents:

* At the start of a turn an agent gets a short "Message board" note in its
  instructions (``digest``): open questions and requests for it, pinned
  decisions, and new posts of the kinds and tags it follows. Nothing else is
  pushed, so a busy board doesn't cost every agent tokens.
* A question or request addressed to one agent on the ``agents`` board starts
  a chat with it so it can answer (``wake``), at most ``wakes_per_day`` a day
  and never deeper than agent chats may go. Its reply becomes the answer.
* On a swarm board, a post for a worker or the lead, and every decision, is
  passed to the running workers it concerns (``swarms.service.board_note``).
* When a question gets an answer, the agent that asked hears about it in the
  chat it asked from; a question for the person goes to their Inbox.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cognitrix.board.models import (
    ASKS,
    DEFAULT_FOLLOW_KINDS,
    KINDS,
    BoardCursor,
    BoardFollow,
    BoardPost,
    BoardSettings,
)

logger = logging.getLogger('cognitrix.log')

TEXT_MAX = 2000
REPLY_MAX = 2000
TAGS_MAX = 5
TAG_MAX = 30
REPLIES_MAX = 20
LIST_MAX = 100
POSTS_PER_HOUR = 20
"""Most posts one author may make on one board in an hour."""
DIGEST_FOR_ME = 4
DIGEST_OPEN = 3
DIGEST_PINNED = 4
DIGEST_NEW = 6
DIGEST_TEXT = 280
FIRST_LOOK = timedelta(days=1)
"""A reader that has never looked is shown posts from the last day."""
MAX_WAKES_PER_DAY = 100
MAX_KEEP_DAYS = 365

KIND_LABELS = {
    'finding': 'Finding', 'question': 'Question', 'request': 'Request',
    'decision': 'Decision', 'heads_up': 'Heads-up',
}
_KIND_ALIASES = {
    'find': 'finding', 'found': 'finding', 'result': 'finding', 'fact': 'finding',
    'ask': 'question', 'q': 'question', 'help': 'request', 'todo': 'request', 'task': 'request',
    'decide': 'decision', 'rule': 'decision', 'agreed': 'decision',
    'heads-up': 'heads_up', 'headsup': 'heads_up', 'heads up': 'heads_up', 'note': 'heads_up',
    'fyi': 'heads_up', 'info': 'heads_up', 'warning': 'heads_up',
}
_ANYONE = frozenset({'', 'anyone', 'all', 'everyone', 'any', '@all', 'team'})
_PERSON = frozenset({'you', 'person', 'user', 'owner', 'human', 'the person', 'me'})

# The board note for the turn being run, set in Session._board_turn and added
# to the system prompt (memory/hybrid_context.py).
board_note_ctx: contextvars.ContextVar[str] = contextvars.ContextVar('board_note', default='')

# Strong references for background work (a bare create_task can be collected).
_TASKS: set[asyncio.Task] = set()


class BoardError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Author:
    kind: str = 'agent'
    """agent or person."""
    id: str = ''
    name: str = ''
    chat_id: str = ''
    depth: int = 0
    """How deep in agent-started chats the post was made (for wake limits)."""


PERSON = Author(kind='person', name='You')


_last_stamp = 0


def now_ms() -> int:
    """Milliseconds since 1970, never the same twice in this process (posts keep their order)."""
    global _last_stamp
    stamp = max(int(datetime.now(UTC).timestamp() * 1000), _last_stamp + 1)
    _last_stamp = stamp
    return stamp


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def iso(stamp: int) -> str:
    if not stamp:
        return ''
    return datetime.fromtimestamp(stamp / 1000, UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _spawn(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def settle() -> None:
    """Wait for background work (tests)."""
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)


# ---------------------------------------------------------------------------
# Boards, kinds and tags
# ---------------------------------------------------------------------------

def swarm_board(swarm_id: str) -> str:
    return f'swarm:{swarm_id}'


def swarm_of(board: str) -> str:
    return board.split(':', 1)[1] if str(board or '').startswith('swarm:') else ''


def clean_board(board: str) -> str:
    value = str(board or '').strip()
    if value in ('', 'agents', 'all'):
        return 'agents'
    if value.startswith('swarm:') and len(value) > 6:
        return value
    raise BoardError('bad_board', 'board is "agents" or "swarm:<swarm id>".')


def clean_kind(kind: str) -> str:
    value = str(kind or '').strip().lower().replace('-', '_')
    value = _KIND_ALIASES.get(value, _KIND_ALIASES.get(value.replace('_', '-'), value))
    if value not in KINDS:
        raise BoardError('bad_kind', 'kind is finding, question, request, decision or heads_up.')
    return value


def clean_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r'[,\s]+', tags)
    out: list[str] = []
    for tag in tags or []:
        value = re.sub(r'[^a-z0-9_-]+', '-', str(tag or '').strip().lstrip('#').lower()).strip('-')[:TAG_MAX]
        if value and value not in out:
            out.append(value)
    return out[:TAGS_MAX]


def _clip(text: str, limit: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


# ---------------------------------------------------------------------------
# Settings and follows
# ---------------------------------------------------------------------------

async def get_settings(user_key: str) -> BoardSettings:
    row = await BoardSettings.find_one({'user_id': str(user_key)})
    return row or BoardSettings(user_id=str(user_key))


def public_settings(row: BoardSettings) -> dict[str, Any]:
    return {'enabled': row.enabled, 'wakes_per_day': row.wakes_per_day, 'keep_days': row.keep_days,
            'wakes_today': row.wakes_used if row.wake_day == datetime.now(UTC).strftime('%Y-%m-%d') else 0}


async def save_settings(user_key: str, changes: dict[str, Any]) -> BoardSettings:
    row = await BoardSettings.find_one({'user_id': str(user_key)})
    data: dict[str, Any] = {}
    if 'enabled' in changes and changes['enabled'] is not None:
        data['enabled'] = bool(changes['enabled'])
    for field, top in (('wakes_per_day', MAX_WAKES_PER_DAY), ('keep_days', MAX_KEEP_DAYS)):
        if changes.get(field) is not None:
            try:
                value = int(changes[field])
            except (TypeError, ValueError):
                raise BoardError('bad_value', f'{field} must be a number.') from None
            low = 0 if field == 'wakes_per_day' else 1
            if not low <= value <= top:
                raise BoardError('bad_value', f'{field} must be between {low} and {top}.')
            data[field] = value
    if row is None:
        row = BoardSettings(user_id=str(user_key), **data)
        await row.save()
        return row
    if data:
        await BoardSettings.update_one({'id': row.id}, data)
        for key, value in data.items():
            setattr(row, key, value)
    return row


async def get_follow(user_key: str, agent_id: str) -> BoardFollow:
    row = await BoardFollow.find_one({'user_id': str(user_key), 'agent_id': str(agent_id)})
    return row or BoardFollow(user_id=str(user_key), agent_id=str(agent_id))


def public_follow(row: BoardFollow) -> dict[str, Any]:
    return {'agent_id': row.agent_id, 'kinds': list(row.kinds), 'tags': list(row.tags), 'wake': row.wake}


async def save_follow(user_key: str, agent_id: str, *, kinds: Any = None, tags: Any = None,
                      wake: bool | None = None) -> BoardFollow:
    data: dict[str, Any] = {}
    if kinds is not None:
        if isinstance(kinds, str):
            kinds = re.split(r'[,\s]+', kinds)
        data['kinds'] = sorted({clean_kind(k) for k in kinds if str(k or '').strip()}, key=KINDS.index)
    if tags is not None:
        data['tags'] = clean_tags(tags)
    if wake is not None:
        data['wake'] = bool(wake)
    row = await BoardFollow.find_one({'user_id': str(user_key), 'agent_id': str(agent_id)})
    if row is None:
        row = BoardFollow(user_id=str(user_key), agent_id=str(agent_id), **data)
        await row.save()
        return row
    if data:
        await BoardFollow.update_one({'id': row.id}, data)
        for key, value in data.items():
            setattr(row, key, value)
    return row


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

async def posts_on(user_key: str, board: str) -> list[BoardPost]:
    """Every post on a board, newest first."""
    rows = await BoardPost.find({'user_id': str(user_key), 'board': board}) or []
    return sorted(rows, key=lambda row: row.posted_at, reverse=True)


async def get_post(user_key: str, post_id: str) -> BoardPost:
    row = await BoardPost.get(str(post_id or '').strip()) if str(post_id or '').strip() else None
    if row is None or row.user_id != str(user_key):
        raise BoardError('post_not_found', 'No such post on your boards. Read the board for post ids.')
    return row


def matches(post: BoardPost, *, kind: str = '', status: str = '', tag: str = '') -> bool:
    if kind and post.kind != kind:
        return False
    if status == 'open' and post.status != 'open':
        return False
    if status == 'pinned' and not post.pinned:
        return False
    if status in ('answered', 'done') and post.status != status:
        return False
    return not (tag and tag not in post.tags)


async def list_posts(user_key: str, board: str, *, kind: str = '', status: str = '', tag: str = '',
                     limit: int = 50) -> list[BoardPost]:
    kind = clean_kind(kind) if kind else ''
    tag = (clean_tags([tag]) or [''])[0] if tag else ''
    rows = [p for p in await posts_on(user_key, board) if matches(p, kind=kind, status=status, tag=tag)]
    return rows[:max(1, min(int(limit or 50), LIST_MAX))]


def public_post(post: BoardPost) -> dict[str, Any]:
    return {
        'id': str(post.id), 'board': post.board, 'kind': post.kind, 'text': post.text, 'tags': list(post.tags),
        'author_kind': post.author_kind, 'author_id': post.author_id, 'author_name': post.author_name,
        'chat_id': post.chat_id, 'to_name': post.to_name, 'to_id': post.to_id, 'status': post.status,
        'pinned': post.pinned, 'replies': [dict(r) for r in post.replies],
        'wake': {k: v for k, v in (post.wake or {}).items() if k in ('state', 'chat_id', 'reason')},
        'posted_at': iso(post.posted_at), 'changed_at': iso(post.changed_at),
    }


def post_line(post: BoardPost, *, text_limit: int = DIGEST_TEXT) -> str:
    """One post as agents read it."""
    head = f'[{KIND_LABELS.get(post.kind, post.kind)} {post.id}] {post.author_name or "Someone"}'
    if post.to_name:
        head += f' → {"the person" if post.to_name == "you" else post.to_name}'
    bits = []
    if post.status:
        bits.append(post.status)
    if post.pinned:
        bits.append('pinned')
    if post.tags:
        bits.append(' '.join('#' + t for t in post.tags))
    line = f'{head}{" (" + ", ".join(bits) + ")" if bits else ""}: {_clip(post.text, text_limit)}'
    for reply in post.replies[-2:]:
        line += f'\n    ↳ {reply.get("author_name") or "Someone"}: {_clip(reply.get("text", ""), 200)}'
    if len(post.replies) > 2:
        line += f'\n    ({len(post.replies) - 2} earlier replies)'
    return line


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

async def _visible_agent(user_key: str, name: str):
    from cognitrix.agents.access import visible_agents

    wanted = name.strip().lstrip('@').casefold()
    for agent in await visible_agents(user_key) or []:
        if str(getattr(agent, 'name', '')).casefold() == wanted:
            return agent
    return None


async def _resolve_to(user_key: str, board: str, to: str, author: Author) -> tuple[str, str]:
    """(to_name, to_id) for who a post is for."""
    value = str(to or '').strip().lstrip('@')
    if value.casefold() in _ANYONE:
        return '', ''
    if value.casefold() in _PERSON:
        if author.kind == 'person':
            raise BoardError('bad_to', 'You can\'t address a post to yourself.')
        return 'you', ''
    swarm_id = swarm_of(board)
    if swarm_id:
        from cognitrix.swarms import service as swarms

        run = await swarms.get_swarm(user_key, swarm_id)
        if run is None:
            raise BoardError('board_not_found', 'That swarm no longer exists.')
        if value.casefold() in ('lead', 'leader', run.agent_name.casefold()):
            return 'lead', run.agent_id
        worker = await swarms.find_worker(swarm_id, value)
        if worker is None:
            names = ', '.join(w.name for w in await swarms.workers_of(swarm_id))
            raise BoardError('bad_to', f'No worker called "{value}" in this swarm. Workers: {names}, or "lead".')
        return worker.name, worker.agent_id
    agent = await _visible_agent(user_key, value)
    if agent is None:
        raise BoardError('bad_to', f'No agent called "{value}". Use List Agents for names, '
                                   'or leave "to" empty for anyone.')
    return str(agent.name), str(agent.id)


async def _prune(user_key: str, keep_days: int) -> None:
    """Drop old posts that are neither pinned nor open."""
    cutoff = _ms(datetime.now(UTC) - timedelta(days=max(1, keep_days)))
    for row in await BoardPost.find({'user_id': str(user_key)}) or []:
        when = row.changed_at or row.posted_at
        if when and when < cutoff and not row.pinned and row.status != 'open':
            await BoardPost.delete_many({'id': row.id})


async def create_post(user_key: str, *, board: str, kind: str, text: str, tags: Any = None, to: str = '',
                      author: Author = PERSON) -> BoardPost:
    board = clean_board(board)
    kind = clean_kind(kind)
    text = str(text or '').strip()
    if not text:
        raise BoardError('text_required', 'Write what the post says.')
    if len(text) > TEXT_MAX:
        raise BoardError('too_long', f'A post holds at most {TEXT_MAX} characters. Keep it to the point; '
                                     'put long results in a file or artifact and say where it is.')
    settings = await get_settings(user_key)
    if author.kind == 'agent' and not settings.enabled:
        raise BoardError('board_off', 'The message board is turned off.')
    if swarm_of(board):
        from cognitrix.swarms import service as swarms

        if await swarms.get_swarm(user_key, swarm_of(board)) is None:
            raise BoardError('board_not_found', 'That swarm no longer exists.')
    to_name, to_id = await _resolve_to(user_key, board, to, author)
    if author.kind == 'agent':
        hour_ago = _ms(datetime.now(UTC) - timedelta(hours=1))
        recent = [p for p in await posts_on(user_key, board)
                  if p.author_name == author.name and p.author_id == author.id and p.posted_at > hour_ago]
        if len(recent) >= POSTS_PER_HOUR:
            raise BoardError('too_many_posts', 'You have posted a lot in the last hour. '
                                               'Put the rest in your reply or a file.')
    stamp = now_ms()
    post = BoardPost(
        user_id=str(user_key), board=board, kind=kind, text=text, tags=clean_tags(tags),
        author_kind=author.kind, author_id=author.id, author_name=author.name or ('You' if author.kind == 'person' else 'Agent'),
        chat_id=author.chat_id, to_name=to_name, to_id=to_id,
        status='open' if kind in ASKS else '', pinned=kind == 'decision',
        posted_at=stamp, changed_at=stamp,
    )
    await post.save()
    try:
        await _prune(user_key, settings.keep_days)
    except Exception:
        logger.debug('Could not prune the board', exc_info=True)
    try:
        await _after_post(user_key, post, author, settings)
    except Exception:
        logger.exception('Could not pass on board post %s', post.id)
    return post


def _link(post: BoardPost) -> str:
    return f'/board?board={post.board}&post={post.id}'


async def _after_post(user_key: str, post: BoardPost, author: Author, settings: BoardSettings) -> None:
    """Pass a new post to whoever it concerns."""
    if post.to_name == 'you' and author.kind == 'agent':
        from cognitrix.tasks.automation import create_notification

        label = KIND_LABELS.get(post.kind, 'Post').lower()
        await create_notification(user_key, f'{post.author_name} left you a {label} on the board',
                                  _clip(post.text, 400), link=_link(post))
        return
    swarm_id = swarm_of(post.board)
    if swarm_id:
        from cognitrix.swarms import service as swarms

        note = _swarm_note(post)
        if post.to_name:
            if post.to_name != author.name or author.kind == 'person':
                _spawn(swarms.board_note(user_key, swarm_id, post.to_name, note))
        elif post.kind == 'decision':
            _spawn(swarms.board_note(user_key, swarm_id, '*', note, skip=author.name))
        return
    if post.kind in ASKS and post.to_id and post.to_id != author.id:
        _spawn(wake(user_key, str(post.id), depth=author.depth))


def _swarm_note(post: BoardPost) -> str:
    label = KIND_LABELS.get(post.kind, 'Post')
    who = 'The person' if post.author_kind == 'person' else post.author_name
    text = f'{who} posted a {label} on the swarm board (post {post.id}):\n\n{post.text}'
    if post.kind in ASKS:
        text += f'\n\nAnswer it with Board (action "answer", post_id "{post.id}") if you can.'
    elif post.kind == 'decision':
        text += '\n\nFollow it from now on.'
    return text


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

async def reply(user_key: str, post_id: str, text: str, author: Author = PERSON, *,
                close: bool = False) -> BoardPost:
    post = await get_post(user_key, post_id)
    text = str(text or '').strip()
    if not text:
        raise BoardError('text_required', 'Write the answer.')
    if len(text) > REPLY_MAX:
        raise BoardError('too_long', f'An answer holds at most {REPLY_MAX} characters.')
    if len(post.replies) >= REPLIES_MAX:
        raise BoardError('too_many_replies', 'This post has enough replies. Start a new post.')
    entry = {'author_kind': author.kind, 'author_id': author.id,
             'author_name': author.name or ('You' if author.kind == 'person' else 'Agent'),
             'text': text, 'at': iso(now_ms())}
    replies = [*post.replies, entry]
    status = post.status
    if post.kind in ASKS:
        status = 'done' if close else ('answered' if status in ('open', '') else status)
    changes = {'replies': replies, 'status': status, 'changed_at': now_ms()}
    await BoardPost.update_one({'id': post.id}, changes)
    for key, value in changes.items():
        setattr(post, key, value)
    asker_spoke = author.kind == post.author_kind and author.id == post.author_id \
        and author.name == post.author_name
    if not asker_spoke:
        try:
            await _tell_asker(user_key, post, entry)
        except Exception:
            logger.exception('Could not tell the author of board post %s', post.id)
    return post


async def _tell_asker(user_key: str, post: BoardPost, entry: dict[str, Any]) -> None:
    who = 'The person' if entry['author_kind'] == 'person' else entry['author_name']
    if post.author_kind == 'person':
        from cognitrix.tasks.automation import create_notification

        await create_notification(user_key, f'{entry["author_name"]} answered you on the board',
                                  _clip(entry['text'], 400), link=_link(post))
        return
    swarm_id = swarm_of(post.board)
    note = (f'{who} answered your {KIND_LABELS.get(post.kind, "post").lower()} on the board '
            f'(post {post.id}: "{_clip(post.text, 160)}"):\n\n{entry["text"]}')
    if swarm_id:
        from cognitrix.swarms import service as swarms

        target = 'lead' if await _is_lead(user_key, swarm_id, post) else post.author_name
        _spawn(swarms.board_note(user_key, swarm_id, target, note))
        return
    if post.kind not in ASKS or not post.chat_id or not post.author_id:
        return
    from cognitrix.sessions.base import Session

    chat = await Session.get(post.chat_id)
    if chat is None or getattr(chat, 'task_id', None) or str(chat.agent_id or '') != post.author_id:
        return
    from cognitrix.sessions.agent_chats import REPORT_PREFIX, deliver_report

    _spawn(deliver_report(user_key, post.author_id, post.chat_id,
                          f'{REPORT_PREFIX}{note}\n\nUse it if it helps; do not reply only to thank them.',
                          sender=entry['author_name']))


async def _is_lead(user_key: str, swarm_id: str, post: BoardPost) -> bool:
    from cognitrix.swarms import service as swarms

    run = await swarms.get_swarm(user_key, swarm_id)
    if run is None:
        return False
    return post.chat_id == run.session_id and post.author_id == run.agent_id


async def set_status(user_key: str, post_id: str, status: str) -> BoardPost:
    post = await get_post(user_key, post_id)
    status = str(status or '').strip().lower()
    if post.kind not in ASKS:
        raise BoardError('not_open', 'Only questions and requests are opened and closed.')
    if status not in ('open', 'done'):
        raise BoardError('bad_status', 'status is "open" or "done".')
    await BoardPost.update_one({'id': post.id}, {'status': status, 'changed_at': now_ms()})
    post.status = status
    return post


async def set_pinned(user_key: str, post_id: str, pinned: bool) -> BoardPost:
    post = await get_post(user_key, post_id)
    await BoardPost.update_one({'id': post.id}, {'pinned': bool(pinned), 'changed_at': now_ms()})
    post.pinned = bool(pinned)
    return post


async def delete_post(user_key: str, post_id: str) -> None:
    post = await get_post(user_key, post_id)
    await BoardPost.delete_many({'id': post.id})


# ---------------------------------------------------------------------------
# Waking an agent for a question or request
# ---------------------------------------------------------------------------

async def _take_wake(user_key: str) -> bool:
    """Count one wake against today's allowance; False when it's used up."""
    row = await BoardSettings.find_one({'user_id': str(user_key)})
    today = datetime.now(UTC).strftime('%Y-%m-%d')
    current = row or BoardSettings(user_id=str(user_key))
    used = current.wakes_used if current.wake_day == today else 0
    if not current.enabled or used >= current.wakes_per_day:
        return False
    if row is None:
        current.wake_day, current.wakes_used = today, used + 1
        await current.save()
    else:
        await BoardSettings.update_one({'id': row.id}, {'wake_day': today, 'wakes_used': used + 1})
    return True


async def _mark_wake(post: BoardPost, state: str, **more: Any) -> None:
    wake_info = {'state': state, **more}
    await BoardPost.update_one({'id': post.id}, {'wake': wake_info})
    post.wake = wake_info


async def wake(user_key: str, post_id: str, *, depth: int = 0) -> str:
    """Start a chat with the agent a question or request is for, so it can
    answer. Returns 'sent' or why not."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats

    post = await get_post(user_key, post_id)
    if post.wake or post.status != 'open' or not post.to_id:
        return 'skipped'
    follow = await get_follow(user_key, post.to_id)
    reason = ''
    if not follow.wake:
        reason = 'It only reads the board when it next works.'
    elif depth >= agent_chats.MAX_DEPTH:
        reason = 'Asked from a chat agents started, too deep to start another.'
    elif agent_chats.running_agent_chats(user_key) >= agent_chats.MAX_RUNNING_PER_USER:
        reason = 'Too many agent chats were running.'
    agent = await find_visible_agent(user_key, agent_id=post.to_id) if not reason else None
    if not reason and agent is None:
        reason = 'The agent no longer exists.'
    if not reason and not await _take_wake(user_key):
        reason = "Today's limit of chats the board may start is used up."
    if reason:
        await _mark_wake(post, 'skipped', reason=reason)
        return reason
    started_by = {
        'agent_id': post.author_id or None, 'agent_name': post.author_name,
        'session_id': post.chat_id or None, 'depth': depth + 1, 'report_back': False, 'exchanges': 1,
        'via': 'board', 'post_id': str(post.id),
    }
    session = await agent_chats.create_chat(user_key, agent, started_by)
    from cognitrix.sessions.base import Session

    title = f'Board: {_clip(post.text, 60)}'
    await Session.update_one({'id': str(session.id)}, {'title': title})
    session.title = title
    label = KIND_LABELS.get(post.kind, 'question').lower()
    text = (f'{post.author_name} left you a {label} on the message board (post {post.id}):\n\n{post.text}\n\n'
            f'Answer it with Board (action "answer", post_id "{post.id}"), in a few lines. '
            'If it isn\'t yours to answer or you can\'t, answer saying so and who might know. '
            'Your answer is sent back to them.')
    await _mark_wake(post, 'sent', chat_id=str(session.id))

    async def done(status: str, reply_text: str) -> None:
        fresh = await BoardPost.get(str(post.id))
        if fresh is None or fresh.status != 'open' or status != 'complete' or not reply_text.strip():
            return
        answered_by = Author(kind='agent', id=str(agent.id), name=str(agent.name), chat_id=str(session.id))
        try:
            await reply(user_key, str(post.id), _clip(reply_text, REPLY_MAX), answered_by)
        except BoardError:
            logger.debug('Could not save the answer to board post %s', post.id, exc_info=True)

    agent_chats.run_in_background(user_key, agent, session, text, on_done=done)
    return 'sent'


# ---------------------------------------------------------------------------
# What an agent is told at the start of a turn
# ---------------------------------------------------------------------------

DIGEST_HEADER = '## Message board'


async def digest(user_key: str, agent_id: str, agent_name: str, *, board: str = 'agents',
                 worker: str = '', chat_id: str = '') -> str:
    """The board note for one turn, and mark what it lists as seen."""
    settings = await get_settings(user_key)
    if not settings.enabled:
        return ''
    me = worker or agent_name
    key = f'chat:{chat_id}' if worker else f'agent:{agent_id}'
    cursor = await BoardCursor.find_one({'user_id': str(user_key), 'key': key})
    seen = (cursor.seen_at if cursor else 0) or _ms(datetime.now(UTC) - FIRST_LOOK)
    posts = await posts_on(user_key, board)
    if worker:
        kinds, tags = set(KINDS), set()
    else:
        follow = await get_follow(user_key, agent_id)
        kinds, tags = set(follow.kinds), set(follow.tags)

    names = {me, agent_name} if worker == 'lead' else {me}

    def mine(p: BoardPost) -> bool:
        return p.author_name in names and bool(worker or p.author_id == agent_id)

    def for_me(p: BoardPost) -> bool:
        return p.to_name == me if worker else p.to_id == agent_id

    listed: set[str] = set()

    def take(rows: list[BoardPost], limit: int) -> list[BoardPost]:
        out = [p for p in rows if str(p.id) not in listed][:limit]
        listed.update(str(p.id) for p in out)
        return out

    to_me = take([p for p in posts if p.status == 'open' and for_me(p) and not mine(p)], DIGEST_FOR_ME)
    pinned = take([p for p in posts if p.pinned], DIGEST_PINNED)
    open_any = take([p for p in posts if p.status == 'open' and not p.to_name and not mine(p)], DIGEST_OPEN)
    fresh = take([p for p in posts if p.posted_at > seen and not mine(p)
                  and (p.kind in kinds or tags & set(p.tags) or for_me(p))], DIGEST_NEW)
    answered = take([p for p in posts if mine(p) and p.replies and p.changed_at > seen], DIGEST_OPEN)
    stamp = now_ms()
    if cursor is None:
        await BoardCursor(user_id=str(user_key), key=key, seen_at=stamp).save()
    else:
        await BoardCursor.update_one({'id': cursor.id}, {'seen_at': stamp})
    if not (to_me or pinned or open_any or fresh or answered):
        return ''
    where = ('the board of the swarm you lead' if worker == 'lead' else 'this swarm\'s board') if worker \
        else 'the board all your agents share'
    lines = [DIGEST_HEADER,
             f'Posts on {where}. Use the Board tool to read more, post, or answer (by post id). '
             'Post only what others need: findings that change their work, questions you can\'t settle '
             'yourself, decisions. Don\'t post thanks or status.']
    for title, rows in (('For you', to_me), ('Answers to your posts', answered), ('Pinned decisions', pinned),
                        ('Open, anyone can answer', open_any), ('New since you last looked', fresh)):
        if rows:
            lines.append(f'{title}:')
            lines += [f'- {post_line(p)}' for p in rows]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Swarms
# ---------------------------------------------------------------------------

async def swarm_decisions(user_key: str, swarm_id: str, limit: int = 6) -> list[str]:
    """Pinned decisions on a swarm board, oldest first, for a new worker's brief."""
    rows = [p for p in await posts_on(user_key, swarm_board(swarm_id)) if p.pinned][:limit]
    return [f'{p.author_name}: {p.text}' for p in reversed(rows)]


async def swarm_open_items(user_key: str, swarm_id: str, limit: int = 5) -> list[BoardPost]:
    return [p for p in await posts_on(user_key, swarm_board(swarm_id)) if p.status == 'open'][:limit]


async def boards(user_key: str) -> list[dict[str, Any]]:
    """The boards the person can open: everyone's, then swarms that are
    running or have posts, newest first."""
    from cognitrix.swarms.models import SwarmRun

    posts = await BoardPost.find({'user_id': str(user_key)}) or []
    counts: dict[str, dict[str, Any]] = {}
    for p in posts:
        c = counts.setdefault(p.board, {'total': 0, 'open': 0, 'latest': ''})
        c['total'] += 1
        c['open'] += 1 if p.status == 'open' else 0
        c['latest'] = max(c['latest'], iso(p.posted_at))
    out = [{'key': 'agents', 'kind': 'agents', 'title': 'All agents', 'status': '',
            **counts.get('agents', {'total': 0, 'open': 0, 'latest': ''})}]
    swarms = [s for s in await SwarmRun.find({'user_id': str(user_key)}) or []
              if s.status == 'running' or swarm_board(str(s.id)) in counts]
    swarms.sort(key=lambda s: counts.get(swarm_board(str(s.id)), {}).get('latest') or s.started_at, reverse=True)
    for s in swarms[:20]:
        key = swarm_board(str(s.id))
        out.append({'key': key, 'kind': 'swarm', 'title': s.title or 'Swarm', 'status': s.status,
                    'swarm_id': str(s.id), 'session_id': s.session_id, 'agent_name': s.agent_name,
                    **counts.get(key, {'total': 0, 'open': 0, 'latest': ''})})
    return out


__all__ = [
    'ASKS', 'DEFAULT_FOLLOW_KINDS', 'board_note_ctx', 'KINDS', 'Author', 'BoardError', 'PERSON', 'boards', 'clean_board',
    'clean_kind', 'clean_tags', 'create_post', 'delete_post', 'digest', 'get_follow', 'get_post',
    'get_settings', 'list_posts', 'post_line', 'public_follow', 'public_post', 'public_settings', 'reply',
    'save_follow', 'save_settings', 'set_pinned', 'set_status', 'swarm_board', 'swarm_decisions',
    'swarm_of', 'swarm_open_items', 'wake',
]
