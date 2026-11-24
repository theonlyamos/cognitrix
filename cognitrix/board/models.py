"""Posts on the message board, who follows what, and each person's board settings.

A board is ``agents`` (every agent of one person) or ``swarm:<id>`` (the
workers and lead of one swarm). Persistence rule (as elsewhere): instance
``save()`` once at creation, ``update_one`` after.
"""
from __future__ import annotations

from typing import Any

from odbms import Model
from pydantic import Field, field_validator

KINDS = ('finding', 'question', 'request', 'decision', 'heads_up')
ASKS = frozenset({'question', 'request'})
"""Kinds that stay open until someone answers them or marks them done."""
STATUSES = ('', 'open', 'answered', 'done')

DEFAULT_FOLLOW_KINDS = ['decision', 'question', 'request']
DEFAULT_WAKES_PER_DAY = 10
DEFAULT_KEEP_DAYS = 30
DEFAULT_NUDGE_HOURS = 24


def _int(value: Any, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return '' if value is None else str(value)


def _json(value: Any, kind: type) -> Any:
    """A list or dict column; a new column on an old database comes back as text."""
    if isinstance(value, str) and value.strip():
        import json

        try:
            value = json.loads(value)
        except ValueError:
            value = None
    return value if isinstance(value, kind) else kind()


class BoardPost(Model):
    user_id: str
    """Principal key of the person whose agents share the board."""
    board: str = 'agents'
    kind: str = 'finding'
    text: str = ''
    tags: list = Field(default_factory=list)
    author_kind: str = 'agent'
    """agent or person."""
    author_id: str = ''
    """The agent's id (empty for the person)."""
    author_name: str = ''
    """The agent's name, or a swarm worker's name on a swarm board."""
    chat_id: str = ''
    """The chat it was posted from, so an answer can be sent back there."""
    to_name: str = ''
    """Who a question or request is for: '' for anyone, 'you' for the person, or an agent or worker name."""
    to_id: str = ''
    status: str = ''
    pinned: bool = False
    replies: list = Field(default_factory=list)
    """[{author_kind, author_id, author_name, text, at, mentions}]"""
    mentions: list = Field(default_factory=list)
    """[{name, id}]: agents (or swarm workers) tagged with @name in the text."""
    wake: dict = Field(default_factory=dict)
    """{'state': 'sent' | 'skipped', 'chat_id', 'reason'} for a post that tried to wake its agent."""
    mention_wakes: list = Field(default_factory=list)
    """[{key, name, state, chat_id, reason}]: chats started because someone was tagged."""
    retracted: bool = False
    """Withdrawn by its author: kept for the record, listed to nobody."""
    posted_at: int = 0
    """Milliseconds since 1970 (odbms turns date-like text into whole-second dates)."""
    changed_at: int = 0
    edited_at: int = 0
    nudged_at: int = 0
    """When an unanswered question or request was last brought up again."""
    memory_id: str = ''
    """A decision saved to (or proposed for) the agents' memory."""
    thread: str = ''
    """A thread in a channel, such as ``task:<id>`` for the posts about one task ('' = none)."""
    thread_title: str = ''
    claimed_by: dict = Field(default_factory=dict)
    """{kind, id, name}: who took on a request or question, so nobody else does it twice."""
    claimed_at: int = 0
    people: list = Field(default_factory=list)
    """[{name, id}]: people tagged with @Name (they hear about it in their Inbox)."""
    mirror: dict = Field(default_factory=dict)
    """{mirror id: Slack message ts}: where the post was copied to Slack."""

    @field_validator('pinned', 'retracted', mode='before')
    @classmethod
    def _bools(cls, value):
        return bool(value) and str(value).lower() not in ('0', 'false')

    @field_validator('posted_at', 'changed_at', 'edited_at', 'nudged_at', 'claimed_at', mode='before')
    @classmethod
    def _stamps(cls, value):
        return _int(value, 0)

    @field_validator('tags', 'replies', 'mentions', 'mention_wakes', 'people', mode='before')
    @classmethod
    def _lists(cls, value):
        return _json(value, list)

    @field_validator('wake', 'claimed_by', 'mirror', mode='before')
    @classmethod
    def _dicts(cls, value):
        return _json(value, dict)

    @field_validator('board', 'kind', 'text', 'author_kind', 'author_id', 'author_name', 'chat_id',
                     'to_name', 'to_id', 'status', 'memory_id', 'thread', 'thread_title', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardFollow(Model):
    """What one agent hears about from the board. Missing row = the defaults."""
    user_id: str
    agent_id: str
    kinds: list = Field(default_factory=lambda: list(DEFAULT_FOLLOW_KINDS))
    """Kinds of new posts listed for it at the start of a turn."""
    tags: list = Field(default_factory=list)
    """Posts with any of these tags are listed too, whatever their kind."""
    wake: bool = True
    """A question or request addressed to it starts a chat so it can answer."""

    @field_validator('kinds', 'tags', mode='before')
    @classmethod
    def _lists(cls, value):
        return value if isinstance(value, list) else []

    @field_validator('wake', mode='before')
    @classmethod
    def _wake(cls, value):
        return True if value is None else bool(value)

    @field_validator('user_id', 'agent_id', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardCursor(Model):
    """Where one reader got to on a board: ``agent:<id>`` or ``chat:<id>`` (a swarm worker)."""
    user_id: str
    key: str
    seen_at: int = 0
    """Milliseconds since 1970."""

    @field_validator('seen_at', mode='before')
    @classmethod
    def _stamp(cls, value):
        return _int(value, 0)

    @field_validator('user_id', 'key', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardSettings(Model):
    """One row per person. Missing row = the defaults."""
    user_id: str
    enabled: bool = True
    """Off: agents are not given the Board tool and nothing is listed for them."""
    wakes_per_day: int = DEFAULT_WAKES_PER_DAY
    """Chats the board may start in a day to answer questions and requests."""
    keep_days: int = DEFAULT_KEEP_DAYS
    """Posts older than this drop off, unless pinned or still open."""
    nudge_hours: int = DEFAULT_NUDGE_HOURS
    """An unanswered question or request is brought up again after this many hours (0: never)."""
    wake_day: str = ''
    wakes_used: int = 0

    @field_validator('enabled', mode='before')
    @classmethod
    def _enabled(cls, value):
        return True if value is None else bool(value)

    @field_validator('wakes_per_day', 'keep_days', 'nudge_hours', 'wakes_used', mode='before')
    @classmethod
    def _ints(cls, value, info):
        defaults = {'wakes_per_day': DEFAULT_WAKES_PER_DAY, 'keep_days': DEFAULT_KEEP_DAYS,
                    'nudge_hours': DEFAULT_NUDGE_HOURS}
        return _int(value, defaults.get(info.field_name, 0))

    @field_validator('user_id', 'wake_day', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardChannel(Model):
    """A project's channel (``project:<id>``): who besides its owner may use
    it, and which agents may post there."""
    user_id: str
    key: str
    project_id: str = ''
    members: list = Field(default_factory=list)
    """User ids of other people who may read and post in the channel."""
    agents: list = Field(default_factory=list)
    """Agent ids allowed to post besides the project's own agents."""
    externals: list = Field(default_factory=list)
    """Names of outside agents (board CLI, scoped key) allowed to post."""
    created_at: int = 0

    @field_validator('members', 'agents', 'externals', mode='before')
    @classmethod
    def _lists(cls, value):
        return [str(v) for v in _json(value, list) if str(v or '').strip()]

    @field_validator('created_at', mode='before')
    @classmethod
    def _stamp(cls, value):
        return _int(value, 0)

    @field_validator('user_id', 'key', 'project_id', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardAgent(Model):
    """An outside agent that joined the board through the board CLI with a
    key that has the "board" scope. Its unread queue is ``inbox``."""
    user_id: str
    name: str
    key_id: str = ''
    """The API key it last used."""
    last_seen: int = 0

    @field_validator('last_seen', mode='before')
    @classmethod
    def _stamp(cls, value):
        return _int(value, 0)

    @field_validator('user_id', 'name', 'key_id', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardSchedule(Model):
    """A repeating post: "every weekday at 9:00, ask reviewer to check open PRs"."""
    user_id: str
    board: str = 'agents'
    thread: str = ''
    to_name: str = ''
    """Who it is for: an agent's name (it is woken to do it), or '' for anyone."""
    kind: str = 'request'
    text: str = ''
    days: list = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    """Weekdays it runs on, 0 = Monday."""
    at_time: str = '09:00'
    """HH:MM in ``tz``."""
    tz: str = ''
    enabled: bool = True
    next_at: int = 0
    last_at: int = 0
    last_post: str = ''
    created_by: str = ''
    """Who made it: a person's name or an agent's name."""

    @field_validator('days', mode='before')
    @classmethod
    def _days(cls, value):
        out = []
        for v in _json(value, list):
            try:
                day = int(v)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6 and day not in out:
                out.append(day)
        return sorted(out)

    @field_validator('enabled', mode='before')
    @classmethod
    def _enabled(cls, value):
        return True if value is None else bool(value) and str(value).lower() not in ('0', 'false')

    @field_validator('next_at', 'last_at', mode='before')
    @classmethod
    def _stamps(cls, value):
        return _int(value, 0)

    @field_validator('user_id', 'board', 'thread', 'to_name', 'kind', 'text', 'at_time', 'tz', 'last_post',
                     'created_by', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardCommand(Model):
    """A fixed workflow anyone on the board runs by posting ``/name …``:
    the agent gets the prompt and the rest of the post, and its result is
    posted back as the answer."""
    user_id: str
    name: str
    agent_id: str = ''
    prompt: str = ''
    description: str = ''
    created_at: int = 0

    @field_validator('created_at', mode='before')
    @classmethod
    def _stamp(cls, value):
        return _int(value, 0)

    @field_validator('user_id', 'name', 'agent_id', 'prompt', 'description', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


class BoardMirror(Model):
    """A board copied both ways to a Slack channel through one of the
    person's chat apps (cognitrix/channels)."""
    user_id: str
    board: str
    channel_id: str
    """The ChatChannel (a Slack app) that posts and listens."""
    slack_channel: str
    """The Slack channel id (C…)."""
    created_at: int = 0

    @field_validator('created_at', mode='before')
    @classmethod
    def _stamp(cls, value):
        return _int(value, 0)

    @field_validator('user_id', 'board', 'channel_id', 'slack_channel', mode='before')
    @classmethod
    def _texts(cls, value):
        return _text(value)


MODELS = (BoardPost, BoardFollow, BoardCursor, BoardSettings, BoardChannel, BoardAgent, BoardSchedule,
          BoardCommand, BoardMirror)
