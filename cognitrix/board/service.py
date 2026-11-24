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
* ``@Name`` in a post or an answer tags an agent (on a swarm board, a worker
  or ``@lead``). A tagged agent gets a chat so it can respond (same limits as
  a wake); a tagged swarm worker gets a note. Tags show in its turn note too.
* A question or request nobody has answered for ``nudge_hours`` is brought up
  again once (``nudge_stale``, from the scheduler): the agent it was for is
  woken if it never was, otherwise the person hears about it in their Inbox.
* A decision can be saved to the agents' memory (``remember``): straight away
  by the person, as a proposal the person approves when an agent asks.
* The Board page follows changes live (``listen``); the morning brief lists
  what on the board waits on the person (``brief_note``).
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import math
import re
import weakref
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
MAX_NUDGE_HOURS = 24 * 14
NUDGE_CHECK_MS = 10 * 60 * 1000
"""How often the scheduler looks for questions nobody answered."""
SEARCH_LIMIT = 20
SIMILAR_MIN = 0.5
"""Share of words two questions have in common to count as the same question."""

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
_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _lock(key: str) -> asyncio.Lock:
    """One lock per post or person, so a read-modify-write isn't lost to a
    concurrent one (two answers at once, two wakes at once)."""
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


class BoardError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Author:
    kind: str = 'agent'
    """agent, person, or external (an outside agent using the board CLI, id ``ext:<name>``)."""
    id: str = ''
    name: str = ''
    chat_id: str = ''
    depth: int = 0
    """How deep in agent-started chats the post was made (for wake limits)."""


PERSON = Author(kind='person', name='You')
"""The board's owner, when who exactly doesn't matter (id '' means the owner)."""

BOTS = ('agent', 'external')
"""Author kinds that are agents, not people."""


def external(name: str) -> Author:
    name = ' '.join(str(name or '').split())[:40] or 'Outside agent'
    return Author(kind='external', id=f'ext:{name}', name=name)


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


# Open Board pages of each person, told when something on their boards changes.
_LISTENERS: dict[str, set[asyncio.Queue]] = {}


def listen(user_key: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _LISTENERS.setdefault(str(user_key), set()).add(queue)
    return queue


def unlisten(user_key: str, queue: asyncio.Queue) -> None:
    rows = _LISTENERS.get(str(user_key))
    if rows is not None:
        rows.discard(queue)
        if not rows:
            _LISTENERS.pop(str(user_key), None)


def _announce(user_key: str, board: str, post_id: str = '') -> None:
    for queue in list(_LISTENERS.get(str(user_key), ())):
        try:
            queue.put_nowait({'type': 'changed', 'board': board, 'post_id': str(post_id or '')})
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# Boards, kinds and tags
# ---------------------------------------------------------------------------

def swarm_board(swarm_id: str) -> str:
    return f'swarm:{swarm_id}'


def swarm_of(board: str) -> str:
    return board.split(':', 1)[1] if str(board or '').startswith('swarm:') else ''


def project_board(project_id: str) -> str:
    return f'project:{project_id}'


def project_of(board: str) -> str:
    return board.split(':', 1)[1] if str(board or '').startswith('project:') else ''


def clean_board(board: str) -> str:
    value = str(board or '').strip()
    if value in ('', 'agents', 'all'):
        return 'agents'
    if value.startswith('swarm:') and len(value) > 6:
        return value
    if value.startswith('project:') and len(value) > 8:
        return value
    raise BoardError('bad_board', 'board is "agents", "project:<project id>" or "swarm:<swarm id>".')


def clean_thread(thread: str) -> str:
    """A thread name: ``task:<id>`` or a few words ("launch plan")."""
    value = ' '.join(str(thread or '').split())[:60]
    if value.startswith('task:'):
        return 'task:' + re.sub(r'[^A-Za-z0-9_-]+', '', value[5:])[:64]
    return value


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
            'nudge_hours': row.nudge_hours,
            'wakes_today': row.wakes_used if row.wake_day == datetime.now(UTC).strftime('%Y-%m-%d') else 0}


async def save_settings(user_key: str, changes: dict[str, Any]) -> BoardSettings:
    async with _lock(f'settings:{user_key}'):
        return await _save_settings(user_key, changes)


async def _save_settings(user_key: str, changes: dict[str, Any]) -> BoardSettings:
    row = await BoardSettings.find_one({'user_id': str(user_key)})
    data: dict[str, Any] = {}
    if 'enabled' in changes and changes['enabled'] is not None:
        data['enabled'] = bool(changes['enabled'])
    for field, top in (('wakes_per_day', MAX_WAKES_PER_DAY), ('keep_days', MAX_KEEP_DAYS),
                       ('nudge_hours', MAX_NUDGE_HOURS)):
        if changes.get(field) is not None:
            try:
                value = int(changes[field])
            except (TypeError, ValueError):
                raise BoardError('bad_value', f'{field} must be a number.') from None
            low = 1 if field == 'keep_days' else 0
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

async def posts_on(user_key: str, board: str, **where: Any) -> list[BoardPost]:
    """Posts on a board, newest first (``where``: more equality filters the
    database applies, such as status='open')."""
    rows = await BoardPost.find({'user_id': str(user_key), 'board': board, **where}) or []
    return sorted(rows, key=lambda row: row.posted_at, reverse=True)


async def get_post(user_key: str, post_id: str) -> BoardPost:
    wanted = str(post_id or '').strip().strip('[]').lower()
    row = await BoardPost.get(wanted) if wanted else None
    if row is None and len(wanted) >= 6 and re.fullmatch(r'[0-9a-f-]+', wanted):
        # Agents often quote the first part of an id ("5e40d18e").
        found = [p for p in await BoardPost.find({'user_id': str(user_key)}) or [] if str(p.id).startswith(wanted)]
        row = found[0] if len(found) == 1 else None
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
                     limit: int = 50, before: int = 0) -> list[BoardPost]:
    """Newest first, ``limit`` at a time. The first page also carries older
    open questions and pinned posts; ``before`` (a post's ``stamp``) pages on."""
    kind = clean_kind(kind) if kind else ''
    tag = (clean_tags([tag]) or [''])[0] if tag else ''
    where: dict[str, Any] = {}
    if kind:
        where['kind'] = kind
    if status in ('open', 'answered', 'done'):
        where['status'] = status
    rows = [p for p in await posts_on(user_key, board, **where) if matches(p, kind=kind, status=status, tag=tag)]
    cut = max(1, min(int(limit or 50), LIST_MAX))
    if before:
        return [p for p in rows if p.posted_at < int(before)][:cut]
    # Older open questions and pinned decisions still count, past the limit.
    return rows[:cut] + [p for p in rows[cut:] if p.pinned or p.status == 'open']


def _viewer_wrote(row: dict[str, Any], owner: str, viewer: str) -> bool:
    return bool(viewer) and row.get('author_kind') == 'person' and (row.get('author_id') or owner) == viewer


def public_post(post: BoardPost, viewer: str = '') -> dict[str, Any]:
    """A post as the Board page shows it; ``viewer`` (a user id) marks what they wrote as ``mine``."""
    return {
        'mine': _viewer_wrote({'author_kind': post.author_kind, 'author_id': post.author_id}, post.user_id, viewer),
        'id': str(post.id), 'board': post.board, 'kind': post.kind, 'text': post.text, 'tags': list(post.tags),
        'author_kind': post.author_kind, 'author_id': post.author_id, 'author_name': post.author_name,
        'chat_id': post.chat_id, 'to_name': post.to_name, 'to_id': post.to_id, 'status': post.status,
        'pinned': post.pinned,
        'replies': [{**dict(r), 'mine': _viewer_wrote(r, post.user_id, viewer)} for r in post.replies],
        'mentions': [dict(m) for m in post.mentions], 'retracted': post.retracted,
        'wake': {k: v for k, v in (post.wake or {}).items() if k in ('state', 'chat_id', 'reason', 'command')},
        'mention_wakes': [{k: v for k, v in w.items() if k in ('name', 'state', 'chat_id', 'reason')}
                          for w in post.mention_wakes if isinstance(w, dict)],
        'posted_at': iso(post.posted_at), 'changed_at': iso(post.changed_at),
        'edited_at': iso(post.edited_at), 'stamp': post.posted_at, 'memory_id': post.memory_id,
        'thread': post.thread, 'thread_title': post.thread_title,
        'claimed_by': {k: v for k, v in (post.claimed_by or {}).items() if k in ('kind', 'id', 'name')},
        'claimed_at': iso(post.claimed_at), 'people': [dict(p) for p in post.people],
        'command': str((post.wake or {}).get('command') or ''),
    }


def post_line(post: BoardPost, *, text_limit: int = DIGEST_TEXT) -> str:
    """One post as agents read it."""
    head = f'[{KIND_LABELS.get(post.kind, post.kind)} {post.id}] {post.author_name or "Someone"}'
    if post.to_name:
        head += f' → {"the person" if post.to_name == "you" else post.to_name}'
    bits = []
    if post.thread:
        bits.append(f'thread {post.thread_title or post.thread}' + (f' ({post.thread})' if post.thread_title else ''))
    if post.claimed_by and post.status == 'open':
        bits.append(f'claimed by {post.claimed_by.get("name") or "someone"}')
    if post.status:
        bits.append(post.status)
    if post.pinned:
        bits.append('pinned')
    if post.retracted:
        bits.append('retracted')
    elif post.edited_at:
        bits.append('edited')
    if post.tags:
        bits.append(' '.join('#' + t for t in post.tags))
    line = f'{head}{" (" + ", ".join(bits) + ")" if bits else ""}: {_clip(post.text, text_limit)}'
    for reply in post.replies[-2:]:
        line += f'\n    ↳ {reply.get("author_name") or "Someone"}: {_clip(reply.get("text", ""), 200)}'
    if len(post.replies) > 2:
        line += f'\n    ({len(post.replies) - 2} earlier replies)'
    return line


# ---------------------------------------------------------------------------
# Search, and spotting a question that was already asked
# ---------------------------------------------------------------------------

_STOP = frozenset(
    'a an and are as at be by can could did do does for from has have how i if in into is it its me my no '
    'not of on or our should so than that the their them then there these they this to us was we were what '
    'when where which who why will with would you your'.split())


def _terms(text: str) -> list[str]:
    return [t for t in re.findall(r'[a-z0-9][a-z0-9_-]*', str(text or '').casefold())
            if t not in _STOP and len(t) > 1]


def _haystack(post: BoardPost) -> str:
    return ' '.join([post.text, *(str(r.get('text') or '') for r in post.replies),
                     *('#' + t for t in post.tags), post.author_name, post.to_name]).casefold()


async def search(user_key: str, board: str, query: str, *, limit: int = SEARCH_LIMIT) -> list[BoardPost]:
    """Posts on a board (``*``: every board) whose text, answers, tags or
    names hold the words asked for; the ones holding most of them first."""
    terms = list(dict.fromkeys(_terms(query)))
    if not terms:
        raise BoardError('query_required', 'Say what to look for.')
    if board == '*':
        rows = await BoardPost.find({'user_id': str(user_key)}) or []
    else:
        rows = await posts_on(user_key, board)
    need = max(1, math.ceil(len(terms) / 2))
    scored = []
    for post in rows:
        if post.retracted:
            continue
        hay = _haystack(post)
        hits = sum(1 for t in terms if t in hay)
        if hits >= need:
            scored.append((hits, post.posted_at, post))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in scored[:max(1, min(int(limit or SEARCH_LIMIT), LIST_MAX))]]


async def similar_posts(user_key: str, board: str, text: str, *, limit: int = 3) -> list[BoardPost]:
    """Questions and requests on a board that ask much the same thing."""
    words = {t for t in _terms(text) if len(t) > 2}
    if len(words) < 2:
        return []
    found = []
    for post in await posts_on(user_key, board):
        if post.retracted or post.kind not in ASKS:
            continue
        other = {t for t in _terms(post.text) if len(t) > 2}
        if other and len(words & other) / len(words | other) >= SIMILAR_MIN:
            found.append((len(words & other) / len(words | other), post.posted_at, post))
    found.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in found[:limit]]


# ---------------------------------------------------------------------------
# @Name tags
# ---------------------------------------------------------------------------

async def mention_names(user_key: str, board: str) -> list[tuple[str, str]]:
    """(name, id) of everyone who can be tagged on a board. On a swarm board
    the lead is "lead" (its agent's name works too)."""
    swarm_id = swarm_of(board)
    if swarm_id:
        from cognitrix.swarms import service as swarms

        run = await swarms.get_swarm(user_key, swarm_id)
        if run is None:
            return []
        out = [('lead', str(run.agent_id or ''))]
        out += [(str(w.name), str(w.agent_id or '')) for w in await swarms.workers_of(swarm_id) if w.name]
        return out
    from cognitrix.agents.access import visible_agents
    from cognitrix.board import channels

    out = [(str(a.name), str(a.id)) for a in await visible_agents(user_key) or [] if getattr(a, 'name', '')]
    taken = {n.casefold() for n, _ in out}
    out += [(n, i) for n, i in await channels.externals_of(user_key) if n.casefold() not in taken]
    return out


async def _people_in(user_key: str, board: str, text: str, author: Author) -> list[dict[str, str]]:
    """People tagged with @Name (not the one writing)."""
    if '@' not in str(text or '') or swarm_of(board):
        return []
    from cognitrix.board import channels

    people = await channels.people_on(user_key, board)
    agents = {n.casefold() for n, _ in await mention_names(user_key, board)}
    found = find_mentions(text, [(n, i) for n, i in people if n.casefold() not in agents])
    me = author.id or (user_key if author.kind == 'person' else '')
    return [m for m in found if not (author.kind == 'person' and m['id'] == me)][:8]


def find_mentions(text: str, names: list[tuple[str, str]], *, lead_name: str = '') -> list[dict[str, str]]:
    """Who ``@Name`` tags in ``text``. Longer names win ("@Research Lead"
    before "@Research"); spaces may be written as _ or - or left out."""
    work = str(text or '')
    if '@' not in work:
        return []
    candidates = list(names)
    if lead_name:
        candidates.append((lead_name, next((i for n, i in names if n == 'lead'), '')))
    found: list[dict[str, str]] = []
    for name, ident in sorted(candidates, key=lambda c: len(c[0]), reverse=True):
        canonical = 'lead' if lead_name and name == lead_name else name
        variants = {name, name.replace(' ', '_'), name.replace(' ', '-'), name.replace(' ', '')}
        for variant in sorted(variants, key=len, reverse=True):
            pattern = re.compile(r'(?<![\w@])@' + re.escape(variant) + r'(?![\w-])', re.IGNORECASE)
            hit = False
            for match in list(pattern.finditer(work)):
                hit = True
                work = work[:match.start()] + ' ' * (match.end() - match.start()) + work[match.end():]
            if hit and not any(f['name'] == canonical for f in found):
                found.append({'name': canonical, 'id': ident})
    return found


async def _mentions_in(user_key: str, board: str, text: str, author: Author) -> list[dict[str, str]]:
    if '@' not in str(text or ''):
        return []
    lead_name = ''
    if swarm_of(board):
        from cognitrix.swarms import service as swarms

        run = await swarms.get_swarm(user_key, swarm_of(board))
        lead_name = str(getattr(run, 'agent_name', '') or '') if run is not None else ''
    found = find_mentions(text, await mention_names(user_key, board), lead_name=lead_name)
    return [m for m in found if not _is_author(m, board, author)][:8]


def _is_author(mention: dict[str, str], board: str, author: Author) -> bool:
    if author.kind not in BOTS:
        return False
    if swarm_of(board):
        return mention['name'] == author.name
    return bool(author.id) and mention['id'] == author.id


async def _after_mentions(user_key: str, post: BoardPost, mentioned: list[dict[str, str]], author: Author,
                          text: str) -> None:
    """Tell each tagged agent: a note to a swarm worker, a chat for an agent."""
    if not mentioned or post.retracted:
        return
    who = 'The person' if author.kind == 'person' else (author.name or 'An agent')
    swarm_id = swarm_of(post.board)
    if swarm_id:
        from cognitrix.swarms import service as swarms

        for m in mentioned:
            note = (f'{who} tagged you on the swarm board (post {post.id}, a '
                    f'{KIND_LABELS.get(post.kind, "post").lower()} by {post.author_name}):\n\n{text}\n\n'
                    f'If they need something from you, answer with Board (action "answer", post_id "{post.id}").')
            _spawn(swarms.board_note(user_key, swarm_id, m['name'], note))
        return
    for m in mentioned:
        # Outside agents pick it up from their inbox (``board inbox``).
        if m.get('id') and not str(m['id']).startswith('ext:'):
            _spawn(wake_mention(user_key, str(post.id), m['id'], by=who, text=text, depth=author.depth))


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
        if author.kind == 'person' and (author.id or user_key) == user_key:
            raise BoardError('bad_to', 'You can\'t address a post to yourself.')
        return 'you', ''
    swarm_id = swarm_of(board)
    if not swarm_id:
        from cognitrix.board import channels

        for name, uid in await channels.people_on(user_key, board):
            if name.casefold() == value.casefold():
                if author.kind == 'person' and (author.id or user_key) == uid:
                    raise BoardError('bad_to', 'You can\'t address a post to yourself.')
                return ('you', '') if uid == user_key else (name, f'user:{uid}')
        for name, ident in await channels.externals_of(user_key):
            if name.casefold() == value.casefold() and not await _visible_agent(user_key, value):
                return name, ident
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
                      author: Author = PERSON, check_similar: bool = False, thread: str = '',
                      thread_title: str = '', mirror: dict[str, str] | None = None,
                      trusted: bool = False) -> BoardPost:
    board = clean_board(board)
    thread = clean_thread(thread)
    command = None
    if str(text or '').lstrip().startswith('/'):
        from cognitrix.board import commands

        command = await commands.find(user_key, text)
        if command is not None:
            kind = 'request'
            to = ''
    kind = clean_kind(kind)
    text = str(text or '').strip()
    if not text:
        raise BoardError('text_required', 'Write what the post says.')
    if len(text) > TEXT_MAX:
        raise BoardError('too_long', f'A post holds at most {TEXT_MAX} characters. Keep it to the point; '
                                     'put long results in a file or artifact and say where it is.')
    settings = await get_settings(user_key)
    if author.kind in BOTS and not settings.enabled:
        raise BoardError('board_off', 'The message board is turned off.')
    if project_of(board):
        from cognitrix.board import channels

        if not trusted:
            await channels.check_grant(user_key, board, author)
        elif await channels.project_for(user_key, board) is None:
            raise BoardError('board_not_found', 'That project no longer exists.')
        if not thread_title and thread.startswith('task:'):
            thread_title = await _task_title(thread[5:])
    if swarm_of(board):
        from cognitrix.swarms import service as swarms

        if await swarms.get_swarm(user_key, swarm_of(board)) is None:
            raise BoardError('board_not_found', 'That swarm no longer exists.')
    to_name, to_id = await _resolve_to(user_key, board, to, author)
    if author.kind in BOTS:
        hour_ago = _ms(datetime.now(UTC) - timedelta(hours=1))
        recent = [p for p in await posts_on(user_key, board)
                  if p.author_name == author.name and p.author_id == author.id and p.posted_at > hour_ago]
        if len(recent) >= POSTS_PER_HOUR:
            raise BoardError('too_many_posts', 'You have posted a lot in the last hour. '
                                               'Put the rest in your reply or a file.')
    if check_similar and kind in ASKS:
        same = await similar_posts(user_key, board, text)
        if same:
            listed = '\n'.join(f'- {post_line(p)}' for p in same)
            raise BoardError('similar_posts', 'This looks already asked on the board:\n' + listed + '\n'
                             'If one of these answers you, use that (read it with post_id). If yours is '
                             'different, post it again with force=true.')
    mentions = await _mentions_in(user_key, board, text, author)
    people = await _people_in(user_key, board, text, author)
    stamp = now_ms()
    tag_list = clean_tags(tags)
    if command is not None and command.name not in tag_list:
        tag_list = [command.name, *tag_list][:TAGS_MAX]
    post = BoardPost(
        user_id=str(user_key), board=board, kind=kind, text=text, tags=tag_list,
        author_kind=author.kind, author_id=author.id, author_name=author.name or ('You' if author.kind == 'person' else 'Agent'),
        chat_id=author.chat_id, to_name=to_name, to_id=to_id,
        status='open' if kind in ASKS else '', pinned=kind == 'decision', mentions=mentions,
        people=people, thread=thread, thread_title=_clip(thread_title, 80) if thread else '',
        mirror=dict(mirror or {}), posted_at=stamp, changed_at=stamp,
    )
    await post.save()
    _announce(user_key, board, str(post.id))
    if command is not None:
        from cognitrix.board import commands

        try:
            await commands.run(user_key, post, command, author)
        except Exception:
            logger.exception('Could not run board command /%s', command.name)
    _mirror_post(user_key, post)
    try:
        await _prune(user_key, settings.keep_days)
    except Exception:
        logger.debug('Could not prune the board', exc_info=True)
    try:
        await _after_post(user_key, post, author, settings)
    except Exception:
        logger.exception('Could not pass on board post %s', post.id)
    try:
        await _after_mentions(user_key, post, [m for m in mentions if not _is_to(post, m)], author, post.text)
    except Exception:
        logger.exception('Could not tell the agents tagged in board post %s', post.id)
    if people:
        from cognitrix.board import channels

        try:
            await channels.tell_people(user_key, post, [p for p in people if f'user:{p["id"]}' != post.to_id],
                                       author.name or 'Someone', post.text)
        except Exception:
            logger.exception('Could not tell the people tagged in board post %s', post.id)
    return post


async def _task_title(task_id: str) -> str:
    try:
        from cognitrix.tasks.base import Task

        task = await Task.get(task_id)
        return str(getattr(task, 'title', '') or '') if task is not None else ''
    except Exception:
        return ''


RESULT_TEXT = 1600


async def post_task_result(run_id: str) -> BoardPost | None:
    """When a task in a project finishes and its thread on the project's
    channel is in use (someone posted there), post the result in the thread.
    Once per run."""
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.reports import _result_text
    from cognitrix.tasks.run import TaskRun

    run = await TaskRun.get(str(run_id))
    if run is None:
        return None
    task = await Task.get(run.task_id)
    owner = str(getattr(task, 'owner_id', '') or '') if task is not None else ''
    if task is None or not getattr(task, 'project_id', None) or not owner:
        return None
    board, thread = project_board(str(task.project_id)), f'task:{task.id}'
    if not (await get_settings(owner)).enabled:
        return None
    if not [p for p in await posts_on(owner, board, thread=thread) if not p.retracted]:
        return None
    marker = f'run:{run_id}'
    async with _lock(f'board-run:{run_id}'):
        if await BoardCursor.find_one({'user_id': owner, 'key': marker}):
            return None
        await BoardCursor(user_id=owner, key=marker, seen_at=now_ms()).save()
    status = str(getattr(run.status, 'value', run.status) or '')
    text = _result_text(run).strip()
    body = (f'Task "{task.title}" {"finished" if status == "completed" else status or "ended"}.'
            + (f'\n\n{text[:RESULT_TEXT]}' + ('…' if len(text) > RESULT_TEXT else '') if text else ''))
    agent_id = next(iter(task.assigned_agents or []), '')
    name = 'Task'
    if agent_id:
        from cognitrix.agents.access import find_visible_agent

        agent = await find_visible_agent(owner, agent_id=str(agent_id))
        name = str(getattr(agent, 'name', '') or name)
    author = Author(kind='agent', id=str(agent_id or ''), name=name)
    return await create_post(owner, board=board, kind='finding', text=body[:TEXT_MAX], tags=['result'],
                             author=author, thread=thread, thread_title=str(task.title or ''), trusted=True)


def _mirror_post(user_key: str, post: BoardPost, reply_entry: dict[str, Any] | None = None) -> None:
    """Copy a post or an answer to the Slack channels the board is mirrored to."""
    try:
        from cognitrix.board import mirror

        _spawn(mirror.push(user_key, str(post.id), reply_entry))
    except RuntimeError:
        pass  # no running loop (tests calling from sync code)


def _is_to(post: BoardPost, mention: dict[str, str]) -> bool:
    """The post is addressed to this agent and that already tells it: on a
    swarm board any post for a worker, elsewhere a question or request."""
    if swarm_of(post.board):
        return mention['name'] == post.to_name
    return post.kind in ASKS and bool(post.to_id) and mention.get('id') == post.to_id


def _link(post: BoardPost) -> str:
    return f'/board?board={post.board}&post={post.id}'


async def _after_post(user_key: str, post: BoardPost, author: Author, settings: BoardSettings) -> None:
    """Pass a new post to whoever it concerns."""
    to_owner = post.to_name == 'you' and not (author.kind == 'person' and (author.id or user_key) == user_key)
    if to_owner or post.to_id.startswith('user:'):
        from cognitrix.tasks.automation import create_notification

        label = KIND_LABELS.get(post.kind, 'Post').lower()
        target = post.to_id[5:] if post.to_id.startswith('user:') else user_key
        await create_notification(target, f'{post.author_name} left you a {label} on the board',
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
    if post.kind in ASKS and post.to_id and post.to_id != author.id and not post.to_id.startswith(('ext:', 'user:')):
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

def _wrote(post: BoardPost, author: Author) -> bool:
    if author.kind == 'person' and post.author_kind == 'person':
        # A person's id '' means the board's owner (posts from before people had names).
        return (post.author_id or post.user_id) == (author.id or post.user_id)
    return author.kind == post.author_kind and author.id == post.author_id and author.name == post.author_name


async def reply(user_key: str, post_id: str, text: str, author: Author = PERSON, *,
                close: bool = False, trusted: bool = False, via: str = '') -> BoardPost:
    """Answer a post. ``trusted``: an agent the board itself asked (a wake or
    a command), which may answer even where it isn't granted to post."""
    board = (await get_post(user_key, post_id)).board
    if project_of(board) and not trusted:
        from cognitrix.board import channels

        await channels.check_grant(user_key, board, author)
    mentions = await _mentions_in(user_key, board, text, author)
    people = await _people_in(user_key, board, text, author)
    lock = _lock(f'post:{post_id}')
    async with lock:
        post, entry = await _add_reply(user_key, post_id, text, author, close=close, mentions=mentions,
                                       people=people, via=via)
    _announce(user_key, post.board, str(post.id))
    _mirror_post(user_key, post, entry)
    if people:
        from cognitrix.board import channels

        try:
            await channels.tell_people(user_key, post, people, entry['author_name'], entry['text'])
        except Exception:
            logger.exception('Could not tell the people tagged in an answer to board post %s', post.id)
    asker_spoke = _wrote(post, author)
    told_asker = False
    if not asker_spoke:
        try:
            told_asker = await _tell_asker(user_key, post, entry)
        except Exception:
            logger.exception('Could not tell the author of board post %s', post.id)
    try:
        tagged = [m for m in mentions if not (told_asker and _is_post_author(post, m))]
        await _after_mentions(user_key, post, tagged, author, entry['text'])
    except Exception:
        logger.exception('Could not tell the agents tagged in an answer to board post %s', post.id)
    return post


def _is_post_author(post: BoardPost, mention: dict[str, str]) -> bool:
    if post.author_kind != 'agent':
        return False
    if swarm_of(post.board):
        return mention['name'] == post.author_name or (mention['name'] == 'lead' and not post.author_name)
    return mention.get('id') == post.author_id


async def _add_reply(user_key: str, post_id: str, text: str, author: Author, *,
                     close: bool, mentions: list[dict[str, str]] | None = None,
                     people: list[dict[str, str]] | None = None, via: str = '') -> tuple[BoardPost, dict[str, Any]]:
    post = await get_post(user_key, post_id)
    if post.retracted:
        raise BoardError('retracted', 'Its author withdrew this post.')
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
    if mentions:
        entry['mentions'] = mentions
    if people:
        entry['people'] = people
    if via:
        entry['via'] = via
    replies = [*post.replies, entry]
    status = post.status
    if post.kind in ASKS:
        status = 'done' if close else ('answered' if status in ('open', '') else status)
        # Someone working on a claimed request may post progress without settling it.
        if not close and post.claimed_by and _claimer(post, author) and post.status == 'open':
            status = 'open'
    changes: dict[str, Any] = {'replies': replies, 'status': status, 'changed_at': now_ms()}
    if close and post.kind == 'request' and not post.claimed_by and author.kind in BOTS:
        # Whoever did an unclaimed request and closed it took it on.
        changes['claimed_by'] = {'kind': author.kind, 'id': author.id, 'name': entry['author_name']}
        changes['claimed_at'] = changes['changed_at']
    await BoardPost.update_one({'id': post.id}, changes)
    for key, value in changes.items():
        setattr(post, key, value)
    return post, entry


async def _tell_asker(user_key: str, post: BoardPost, entry: dict[str, Any]) -> bool:
    """Pass an answer to whoever posted; True when it was passed on."""
    who = entry['author_name'] if entry['author_name'] not in ('', 'You') else 'The person'
    if post.author_kind == 'person':
        from cognitrix.tasks.automation import create_notification

        target = post.author_id if post.author_id and not post.author_id.startswith('slack:') else user_key
        await create_notification(target, f'{entry["author_name"]} answered you on the board',
                                  _clip(entry['text'], 400), link=_link(post))
        return True
    if post.author_kind == 'external':
        return False  # it reads the answer from its inbox
    swarm_id = swarm_of(post.board)
    note = (f'{who} answered your {KIND_LABELS.get(post.kind, "post").lower()} on the board '
            f'(post {post.id}: "{_clip(post.text, 160)}"):\n\n{entry["text"]}')
    if swarm_id:
        from cognitrix.swarms import service as swarms

        target = 'lead' if await _is_lead(user_key, swarm_id, post) else post.author_name
        _spawn(swarms.board_note(user_key, swarm_id, target, note))
        return True
    if post.kind not in ASKS or not post.chat_id or not post.author_id:
        return False
    from cognitrix.sessions.base import Session

    chat = await Session.get(post.chat_id)
    if chat is None or getattr(chat, 'task_id', None) or str(chat.agent_id or '') != post.author_id:
        return False
    from cognitrix.sessions.agent_chats import REPORT_PREFIX, deliver_report

    _spawn(deliver_report(user_key, post.author_id, post.chat_id,
                          f'{REPORT_PREFIX}{note}\n\nUse it if it helps; do not reply only to thank them.',
                          sender=entry['author_name']))
    return True


async def _is_lead(user_key: str, swarm_id: str, post: BoardPost) -> bool:
    from cognitrix.swarms import service as swarms

    run = await swarms.get_swarm(user_key, swarm_id)
    if run is None:
        return False
    return post.chat_id == run.session_id and post.author_id == run.agent_id


# ---------------------------------------------------------------------------
# Claiming work, and waiting for an answer
# ---------------------------------------------------------------------------

def _claimer(post: BoardPost, author: Author) -> bool:
    who = post.claimed_by or {}
    if author.kind == 'person' and who.get('kind') == 'person':
        return (who.get('id') or post.user_id) == (author.id or post.user_id)
    return who.get('kind') == author.kind and who.get('id') == author.id and who.get('name') == author.name


async def claim(user_key: str, post_id: str, author: Author = PERSON) -> BoardPost:
    """Take on an open request or question so nobody else does it twice."""
    async with _lock(f'post:{post_id}'):
        post = await get_post(user_key, post_id)
        if post.retracted or post.kind not in ASKS or post.status != 'open':
            raise BoardError('not_open', 'Only an open question or request can be claimed.')
        if project_of(post.board):
            from cognitrix.board import channels

            await channels.check_grant(user_key, post.board, author)
        if post.claimed_by and not _claimer(post, author):
            raise BoardError('claimed', f'{post.claimed_by.get("name") or "Someone"} already claimed it. '
                                        'Pick another, or ask them on the board.')
        if post.to_id and post.to_id != author.id and author.kind != 'person' and post.to_name != 'you':
            raise BoardError('not_for_you', f'This is for {post.to_name}. Pick another.')
        stamp = now_ms()
        who = {'kind': author.kind, 'id': author.id, 'name': author.name or 'Someone'}
        await BoardPost.update_one({'id': post.id}, {'claimed_by': who, 'claimed_at': stamp, 'changed_at': stamp})
        post.claimed_by, post.claimed_at, post.changed_at = who, stamp, stamp
    _announce(user_key, post.board, str(post.id))
    _mirror_post(user_key, post, {'author_name': who['name'], 'text': 'claimed this.', 'claim': True})
    return post


async def unclaim(user_key: str, post_id: str, author: Author = PERSON) -> BoardPost:
    async with _lock(f'post:{post_id}'):
        post = await get_post(user_key, post_id)
        if not post.claimed_by:
            return post
        owner = author.kind == 'person' and (author.id or user_key) == user_key
        if not (_claimer(post, author) or owner):
            raise BoardError('not_yours', 'Only whoever claimed it (or the board\'s owner) can let it go.')
        await BoardPost.update_one({'id': post.id}, {'claimed_by': {}, 'claimed_at': 0, 'changed_at': now_ms()})
        post.claimed_by, post.claimed_at = {}, 0
    _announce(user_key, post.board, str(post.id))
    return post


WAIT_MAX = 300
WAIT_STEP = 1.5


async def wait_for(user_key: str, post_id: str, *, seconds: float = 120, seen: int | None = None) -> tuple[BoardPost, list[dict[str, Any]]]:
    """Wait until a post gets an answer it didn't have (``seen`` answers so
    far, default: as it is now) or is closed, at most ``seconds``. Returns
    the post and the new answers (none when the time ran out)."""
    post = await get_post(user_key, post_id)
    start = len(post.replies) if seen is None else max(0, int(seen))
    status = post.status
    deadline = asyncio.get_running_loop().time() + max(1.0, min(float(seconds or 120), WAIT_MAX))
    while True:
        if len(post.replies) > start or (post.status != status and post.status in ('answered', 'done')):
            return post, list(post.replies[start:])
        if asyncio.get_running_loop().time() >= deadline:
            return post, []
        await asyncio.sleep(WAIT_STEP)
        post = await get_post(user_key, post_id)


async def set_status(user_key: str, post_id: str, status: str) -> BoardPost:
    post = await get_post(user_key, post_id)
    status = str(status or '').strip().lower()
    if post.kind not in ASKS:
        raise BoardError('not_open', 'Only questions and requests are opened and closed.')
    if status not in ('open', 'done'):
        raise BoardError('bad_status', 'status is "open" or "done".')
    await BoardPost.update_one({'id': post.id}, {'status': status, 'changed_at': now_ms()})
    post.status = status
    _announce(user_key, post.board, str(post.id))
    return post


async def set_pinned(user_key: str, post_id: str, pinned: bool) -> BoardPost:
    post = await get_post(user_key, post_id)
    await BoardPost.update_one({'id': post.id}, {'pinned': bool(pinned), 'changed_at': now_ms()})
    post.pinned = bool(pinned)
    _announce(user_key, post.board, str(post.id))
    return post


async def delete_post(user_key: str, post_id: str) -> None:
    post = await get_post(user_key, post_id)
    await BoardPost.delete_many({'id': post.id})
    _announce(user_key, post.board, str(post.id))


def _own(post: BoardPost, editor: Author) -> None:
    if not _wrote(post, editor):
        raise BoardError('not_yours', 'Only its author can change a post. Answer it instead.')


async def edit_post(user_key: str, post_id: str, text: str, editor: Author = PERSON) -> BoardPost:
    """Correct your own post. Anyone newly tagged in it is told."""
    text = str(text or '').strip()
    if not text:
        raise BoardError('text_required', 'Write what the post says.')
    if len(text) > TEXT_MAX:
        raise BoardError('too_long', f'A post holds at most {TEXT_MAX} characters.')
    async with _lock(f'post:{post_id}'):
        post = await get_post(user_key, post_id)
        _own(post, editor)
        if post.retracted:
            raise BoardError('retracted', 'You withdrew this post. Post a new one instead.')
        before = {m['name'] for m in post.mentions}
        mentions = await _mentions_in(user_key, post.board, text, editor)
        stamp = now_ms()
        changes = {'text': text, 'mentions': mentions, 'edited_at': stamp, 'changed_at': stamp}
        await BoardPost.update_one({'id': post.id}, changes)
        for key, value in changes.items():
            setattr(post, key, value)
    _announce(user_key, post.board, str(post.id))
    added = [m for m in mentions if m['name'] not in before and not _is_to(post, m)]
    try:
        await _after_mentions(user_key, post, added, editor, post.text)
    except Exception:
        logger.exception('Could not tell the agents tagged in board post %s', post.id)
    return post


async def retract_post(user_key: str, post_id: str, editor: Author = PERSON) -> BoardPost:
    """Withdraw your own post: kept for the record, closed, unpinned and no
    longer listed to agents."""
    async with _lock(f'post:{post_id}'):
        post = await get_post(user_key, post_id)
        _own(post, editor)
        changes: dict[str, Any] = {'retracted': True, 'pinned': False, 'changed_at': now_ms()}
        if post.status == 'open':
            changes['status'] = 'done'
        await BoardPost.update_one({'id': post.id}, changes)
        for key, value in changes.items():
            setattr(post, key, value)
    _announce(user_key, post.board, str(post.id))
    return post


MEMORY_PREFIX = 'Decision on the message board: '


async def remember(user_key: str, post_id: str, by: Author = PERSON) -> tuple[BoardPost, bool]:
    """Save a decision to the agents' memory so it outlives the board. The
    person saves it straight away; an agent's save is a proposal the person
    approves on the Memory page. Returns (post, saved now)."""
    from cognitrix.memory import saved

    async with _lock(f'post:{post_id}'):
        post = await get_post(user_key, post_id)
        if post.kind != 'decision' or post.retracted:
            raise BoardError('not_a_decision', 'Only a decision can be saved to memory.')
        if post.memory_id:
            if await saved.Memory.get(post.memory_id) is not None:
                raise BoardError('already_saved', 'This decision is already in memory.')
        proposal = by.kind != 'person'
        try:
            row, _created = await saved.add(
                user_key, _clip(MEMORY_PREFIX + post.text, saved.MAX_CHARS),
                scope='global', source='user' if not proposal else 'agent', pending=proposal,
                session_id=by.chat_id, by='person' if not proposal else 'agent')
        except saved.MemoryError_ as exc:
            raise BoardError('memory', str(getattr(exc, 'message', '') or exc)) from None
        await BoardPost.update_one({'id': post.id}, {'memory_id': str(row.id)})
        post.memory_id = str(row.id)
    _announce(user_key, post.board, str(post.id))
    return post, not proposal


# ---------------------------------------------------------------------------
# Waking an agent for a question or request
# ---------------------------------------------------------------------------

async def _take_wake(user_key: str) -> bool:
    """Count one wake against today's allowance; False when it's used up."""
    async with _lock(f'settings:{user_key}'):
        return await _take_wake_locked(user_key)


async def _take_wake_locked(user_key: str) -> bool:
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
    _announce(post.user_id, post.board, str(post.id))


async def _why_not_wake(user_key: str, agent_id: str, agent: Any, depth: int) -> str:
    """Why a chat can't be started for this agent now ('' when it can; counts it)."""
    from cognitrix.sessions import agent_chats

    follow = await get_follow(user_key, agent_id)
    if not follow.wake:
        return 'It only reads the board when it next works.'
    if depth >= agent_chats.MAX_DEPTH:
        return 'Asked from a chat agents started, too deep to start another.'
    if agent_chats.running_agent_chats(user_key) >= agent_chats.MAX_RUNNING_PER_USER:
        return 'Too many agent chats were running.'
    if agent is None:
        return 'The agent no longer exists.'
    if not await _take_wake(user_key):
        return "Today's limit of chats the board may start is used up."
    return ''


async def _open_board_chat(user_key: str, post: BoardPost, agent: Any, depth: int, title_text: str):
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    started_by = {
        'agent_id': post.author_id or None, 'agent_name': post.author_name,
        'session_id': post.chat_id or None, 'depth': depth + 1, 'report_back': False, 'exchanges': 1,
        'via': 'board', 'post_id': str(post.id),
    }
    session = await agent_chats.create_chat(user_key, agent, started_by)
    title = f'Board: {_clip(title_text, 60)}'
    changes: dict[str, Any] = {'title': title}
    if project_of(post.board):
        # A chat about a project channel post works in that project (its folders, its channel).
        changes['project_id'] = project_of(post.board)
    await Session.update_one({'id': str(session.id)}, changes)
    for key, value in changes.items():
        setattr(session, key, value)
    return session


def _answer_when_done(user_key: str, post: BoardPost, agent: Any, session: Any):
    """When the woken chat ends: its reply becomes the answer, unless the
    question was settled meanwhile or the agent already answered on the board."""
    replies_before = len(post.replies)

    async def done(status: str, reply_text: str) -> None:
        fresh = await BoardPost.get(str(post.id))
        if fresh is None or fresh.retracted or fresh.kind not in ASKS or fresh.status != 'open' \
                or status != 'complete' or not reply_text.strip():
            return
        if any(r.get('author_id') == str(agent.id) for r in fresh.replies[replies_before:]):
            return
        answered_by = Author(kind='agent', id=str(agent.id), name=str(agent.name), chat_id=str(session.id))
        try:
            await reply(user_key, str(post.id), reply_text.strip()[:REPLY_MAX], answered_by, trusted=True)
        except BoardError:
            logger.debug('Could not save the answer to board post %s', post.id, exc_info=True)

    return done


async def _where_note(user_key: str, post: BoardPost) -> str:
    """', in the "Launch" project channel (board "project:…")' for a channel post."""
    if not project_of(post.board):
        return ''
    from cognitrix.board import channels

    project = await channels.project_for(user_key, post.board)
    thread = f', thread "{post.thread_title or post.thread}"' if post.thread else ''
    return (f', in the "{getattr(project, "name", "project")}" project channel (board "{post.board}"{thread}; '
            'use that board when you read or post about it)')


async def wake(user_key: str, post_id: str, *, depth: int = 0) -> str:
    """Start a chat with the agent a question or request is for, so it can
    answer. Returns 'sent' or why not."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats

    post = await get_post(user_key, post_id)
    if post.wake or post.status != 'open' or not post.to_id or post.retracted:
        return 'skipped'
    agent = await find_visible_agent(user_key, agent_id=post.to_id)
    reason = await _why_not_wake(user_key, post.to_id, agent, depth)
    if reason:
        await _mark_wake(post, 'skipped', reason=reason)
        return reason
    session = await _open_board_chat(user_key, post, agent, depth, post.text)
    label = KIND_LABELS.get(post.kind, 'question').lower()
    where = await _where_note(user_key, post)
    text = (f'{post.author_name} left you a {label} on the message board (post {post.id}){where}:\n\n{post.text}\n\n'
            f'Answer it with Board (action "answer", post_id "{post.id}"), in a few lines. '
            'If it isn\'t yours to answer or you can\'t, answer saying so and who might know. '
            'Your answer is sent back to them.')
    await _mark_wake(post, 'sent', chat_id=str(session.id))
    agent_chats.run_in_background(user_key, agent, session, text,
                                  on_done=_answer_when_done(user_key, post, agent, session))
    return 'sent'


async def _note_mention_wake(post: BoardPost, entry: dict[str, Any]) -> None:
    async with _lock(f'post:{post.id}'):
        fresh = await BoardPost.get(str(post.id))
        rows = [*(fresh.mention_wakes if fresh else []), entry][-20:]
        await BoardPost.update_one({'id': post.id}, {'mention_wakes': rows})
        post.mention_wakes = rows
    _announce(post.user_id, post.board, str(post.id))


async def wake_mention(user_key: str, post_id: str, agent_id: str, *, by: str, text: str,
                       depth: int = 0) -> str:
    """Start a chat with an agent someone tagged, so it can respond.
    Once per agent for each post or answer that tags it."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats

    post = await get_post(user_key, post_id)
    if post.retracted:
        return 'skipped'
    key = f'{agent_id}:{len(post.replies)}'
    if any(isinstance(w, dict) and w.get('key') == key for w in post.mention_wakes):
        return 'skipped'
    agent = await find_visible_agent(user_key, agent_id=agent_id)
    name = str(getattr(agent, 'name', '') or '')
    reason = await _why_not_wake(user_key, agent_id, agent, depth)
    if reason:
        await _note_mention_wake(post, {'key': key, 'name': name, 'state': 'skipped', 'reason': reason})
        return reason
    session = await _open_board_chat(user_key, post, agent, depth, text)
    label = KIND_LABELS.get(post.kind, 'post').lower()
    where = await _where_note(user_key, post)
    lines = [f'{by} tagged you on the message board (post {post.id}, a {label} by {post.author_name}){where}.']
    if text != post.text:
        lines.append(f'The post: {post.text}')
    lines.append(f'{by} wrote: {text}')
    lines.append(f'If they need something from you, answer with Board (action "answer", post_id "{post.id}"), '
                 'in a few lines. If it is only for your information, take note and stop; '
                 'don\'t answer just to acknowledge it.')
    await _note_mention_wake(post, {'key': key, 'name': name, 'state': 'sent', 'chat_id': str(session.id)})
    agent_chats.run_in_background(user_key, agent, session, '\n\n'.join(lines),
                                  on_done=_answer_when_done(user_key, post, agent, session))
    return 'sent'


# ---------------------------------------------------------------------------
# Bringing up questions nobody answered
# ---------------------------------------------------------------------------

_last_nudge_check = 0


async def nudge_stale(now: datetime | None = None, *, force: bool = False) -> int:
    """Bring up again, once, each open question or request with no answer
    after the person's ``nudge_hours``. Runs from the scheduler; looks every
    ten minutes at most."""
    global _last_nudge_check
    stamp = _ms(now or datetime.now(UTC))
    if not force and stamp - _last_nudge_check < NUDGE_CHECK_MS:
        return 0
    _last_nudge_check = stamp
    settings: dict[str, BoardSettings] = {}
    count = 0
    for post in await BoardPost.find({'status': 'open'}) or []:
        if post.retracted or post.nudged_at or post.replies:
            continue
        if post.user_id not in settings:
            settings[post.user_id] = await get_settings(post.user_id)
        hours = settings[post.user_id].nudge_hours
        if not settings[post.user_id].enabled or hours <= 0 or post.posted_at > stamp - hours * 3_600_000:
            continue
        await BoardPost.update_one({'id': post.id}, {'nudged_at': stamp})
        post.nudged_at = stamp
        try:
            await _nudge(post, hours)
            count += 1
        except Exception:
            logger.exception('Could not bring up board post %s again', post.id)
    return count


async def _nudge(post: BoardPost, hours: int) -> None:
    from cognitrix.tasks.automation import create_notification

    user_key = post.user_id
    label = KIND_LABELS.get(post.kind, 'question').lower()
    age = f'{hours} hours' if hours != 24 else 'a day'
    if post.to_name == 'you' or post.to_id.startswith('user:'):
        target = post.to_id[5:] if post.to_id.startswith('user:') else user_key
        await create_notification(target, f'{post.author_name} is still waiting on your answer',
                                  f'Their {label} on the board, from {age} ago: {_clip(post.text, 300)}',
                                  link=_link(post))
        return
    swarm_id = swarm_of(post.board)
    if swarm_id:
        from cognitrix.swarms import service as swarms

        run = await swarms.get_swarm(user_key, swarm_id)
        if run is None or run.status != 'running':
            return
        target = post.to_name or 'lead'
        note = (f'This {label} on the swarm board has had no answer for {age} (post {post.id}, by '
                f'{post.author_name}):\n\n{post.text}\n\nAnswer it with Board (action "answer", '
                f'post_id "{post.id}") if you can, or close it if it no longer matters.')
        _spawn(swarms.board_note(user_key, swarm_id, target, note))
        return
    if post.to_id and not post.to_id.startswith('ext:') and post.wake.get('state') != 'sent':
        await BoardPost.update_one({'id': post.id}, {'wake': {}})
        post.wake = {}
        if await wake(user_key, str(post.id)) == 'sent':
            return
    who = post.to_name or 'anyone'
    asker = 'You' if post.author_kind == 'person' else post.author_name
    await create_notification(user_key, f'No answer on the board for {age}',
                              f'{asker} asked {who}: {_clip(post.text, 300)}', link=_link(post))


# ---------------------------------------------------------------------------
# What an agent is told at the start of a turn
# ---------------------------------------------------------------------------

DIGEST_HEADER = '## Message board'


async def digest(user_key: str, agent_id: str, agent_name: str, *, board: str = 'agents',
                 worker: str = '', chat_id: str = '', thread: str = '') -> str:
    """The board note for one turn, and mark what it lists as seen."""
    settings = await get_settings(user_key)
    if not settings.enabled:
        return ''
    me = worker or agent_name
    key = f'chat:{chat_id}' if worker else f'agent:{agent_id}'
    if project_of(board):
        key += f':{board}'
    cursor = await BoardCursor.find_one({'user_id': str(user_key), 'key': key})
    seen = (cursor.seen_at if cursor else 0) or _ms(datetime.now(UTC) - FIRST_LOOK)
    posts = [p for p in await posts_on(user_key, board) if not p.retracted]
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

    def is_me(m: Any) -> bool:
        if not isinstance(m, dict):
            return False
        return m.get('name') == me if worker else m.get('id') == agent_id

    def tagged_me(p: BoardPost) -> bool:
        if max(p.posted_at, p.edited_at) > seen and any(is_me(m) for m in p.mentions):
            return True
        return p.changed_at > seen and any(is_me(m) for r in p.replies for m in r.get('mentions') or [])

    listed: set[str] = set()

    def take(rows: list[BoardPost], limit: int) -> list[BoardPost]:
        out = [p for p in rows if str(p.id) not in listed][:limit]
        listed.update(str(p.id) for p in out)
        return out

    to_me = take([p for p in posts if p.status == 'open' and for_me(p) and not mine(p)], DIGEST_FOR_ME)
    tagged = take([p for p in posts if tagged_me(p)], DIGEST_FOR_ME)
    pinned = take([p for p in posts if p.pinned], DIGEST_PINNED)
    open_any = take([p for p in posts if p.status == 'open' and not p.to_name and not mine(p)
                     and not p.claimed_by], DIGEST_OPEN)
    in_thread = take([p for p in posts if thread and p.thread == thread and p.posted_at > seen and not mine(p)],
                     DIGEST_NEW)
    fresh = take([p for p in posts if p.posted_at > seen and not mine(p)
                  and (p.kind in kinds or tags & set(p.tags) or for_me(p))], DIGEST_NEW)
    answered = take([p for p in posts if mine(p) and p.replies and p.changed_at > seen], DIGEST_OPEN)
    stamp = now_ms()
    if cursor is None:
        await BoardCursor(user_id=str(user_key), key=key, seen_at=stamp).save()
    else:
        await BoardCursor.update_one({'id': cursor.id}, {'seen_at': stamp})
    if not (to_me or tagged or pinned or open_any or fresh or answered or in_thread):
        return ''
    if project_of(board):
        from cognitrix.board import channels

        project = await channels.project_for(user_key, board)
        where = f'the "{getattr(project, "name", "project")}" project channel (board "{board}")'
    else:
        where = ('the board of the swarm you lead' if worker == 'lead' else 'this swarm\'s board') if worker \
            else 'the board all your agents share'
    lines = [DIGEST_HEADER,
             f'Posts on {where}. Use the Board tool to read more, post, or answer (by post id). '
             'Post only what others need: findings that change their work, questions you can\'t settle '
             'yourself, decisions. Don\'t post thanks or status. Tag someone with @Name.']
    if project_of(board):
        lines.append('Claim an open request before you work on it (action "claim"), so nobody does it twice.')
    for title, rows in (('For you', to_me), ('Tagged you (@)', tagged), ('Answers to your posts', answered),
                        ('In this task\'s thread', in_thread), ('Pinned decisions', pinned),
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
    rows = [p for p in await posts_on(user_key, swarm_board(swarm_id)) if p.pinned and not p.retracted][:limit]
    return [f'{p.author_name}: {p.text}' for p in reversed(rows)]


async def swarm_open_items(user_key: str, swarm_id: str, limit: int = 5) -> list[BoardPost]:
    return [p for p in await posts_on(user_key, swarm_board(swarm_id), status='open') if not p.retracted][:limit]


BRIEF_ITEMS = 4


async def brief_note(user_key: str, now: datetime | None = None) -> str:
    """What on the boards the morning brief should mention: questions waiting
    on the person, questions nobody answered for a day, new decisions."""
    settings = await get_settings(user_key)
    if not settings.enabled:
        return ''
    day_ago = _ms((now or datetime.now(UTC)) - timedelta(days=1))
    rows = sorted((p for p in await BoardPost.find({'user_id': str(user_key)}) or [] if not p.retracted),
                  key=lambda p: p.posted_at, reverse=True)
    waiting = [p for p in rows if p.status == 'open' and p.to_name == 'you'][:BRIEF_ITEMS]
    stuck = [p for p in rows if p.status == 'open' and p.to_name != 'you' and not p.replies
             and p.posted_at < day_ago][:BRIEF_ITEMS]
    decided = [p for p in rows if p.kind == 'decision' and p.posted_at >= day_ago][:BRIEF_ITEMS]
    if not (waiting or stuck or decided):
        return ''

    def line(p: BoardPost) -> str:
        to = '' if not p.to_name or p.to_name == 'you' else f' (for {p.to_name})'
        return f'- {p.author_name}{to}: {_clip(p.text, 200)} [link: {_link(p)}]'

    parts = ['On the message board (give each item its link):']
    for title, group in (('Questions and requests waiting on the person', waiting),
                         ('Open over a day with no answer', stuck), ('New decisions since yesterday', decided)):
        if group:
            parts.append(f'{title}:')
            parts += [line(p) for p in group]
    return '\n'.join(parts)


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
    from cognitrix.board import channels
    from cognitrix.projects.models import Project

    projects = [p for p in await Project.find({'user_id': str(user_key)}) or [] if not p.archived]
    projects.sort(key=lambda p: (counts.get(project_board(str(p.id)), {}).get('latest') or '', p.updated or ''),
                  reverse=True)
    for p in projects[:30]:
        key = project_board(str(p.id))
        out.append({'key': key, 'kind': 'project', 'title': p.name or 'Project', 'status': '',
                    'project_id': str(p.id), **counts.get(key, {'total': 0, 'open': 0, 'latest': ''})})
    out += await channels.boards_for_member(user_key)
    for s in swarms[:20]:
        key = swarm_board(str(s.id))
        out.append({'key': key, 'kind': 'swarm', 'title': s.title or 'Swarm', 'status': s.status,
                    'swarm_id': str(s.id), 'session_id': s.session_id, 'agent_name': s.agent_name,
                    **counts.get(key, {'total': 0, 'open': 0, 'latest': ''})})
    return out


__all__ = [
    'claim', 'clean_thread', 'external', 'project_board', 'project_of', 'unclaim', 'wait_for',
    'ASKS', 'DEFAULT_FOLLOW_KINDS', 'board_note_ctx', 'KINDS', 'Author', 'BoardError', 'PERSON', 'boards', 'clean_board',
    'brief_note', 'clean_kind', 'clean_tags', 'create_post', 'listen', 'remember', 'unlisten', 'delete_post', 'digest', 'edit_post', 'find_mentions',
    'get_follow', 'get_post', 'get_settings', 'list_posts', 'mention_names', 'nudge_stale', 'post_line',
    'public_follow', 'public_post', 'public_settings', 'reply', 'retract_post', 'save_follow', 'save_settings',
    'search', 'set_pinned', 'set_status', 'similar_posts', 'swarm_board', 'swarm_decisions',
    'swarm_of', 'swarm_open_items', 'wake', 'wake_mention',
]
