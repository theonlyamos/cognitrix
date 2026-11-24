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

    @field_validator('pinned', 'retracted', mode='before')
    @classmethod
    def _bools(cls, value):
        return bool(value) and str(value).lower() not in ('0', 'false')

    @field_validator('posted_at', 'changed_at', 'edited_at', 'nudged_at', mode='before')
    @classmethod
    def _stamps(cls, value):
        return _int(value, 0)

    @field_validator('tags', 'replies', 'mentions', 'mention_wakes', mode='before')
    @classmethod
    def _lists(cls, value):
        return value if isinstance(value, list) else []

    @field_validator('wake', mode='before')
    @classmethod
    def _dicts(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('board', 'kind', 'text', 'author_kind', 'author_id', 'author_name', 'chat_id',
                     'to_name', 'to_id', 'status', mode='before')
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


MODELS = (BoardPost, BoardFollow, BoardCursor, BoardSettings)
