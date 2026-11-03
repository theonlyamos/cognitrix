"""Saved memories: short facts about a person that their agents remember
between chats, plus that person's memory settings.

Each memory belongs to one person (``user_id``). ``scope`` is ``global``
(every agent that reads global memory sees it) or ``agent`` (only the agent in
``agent_id`` sees it). See ``cognitrix/memory/saved.py`` and
docs/designs/global-memory.md.

Persistence rule: instance ``save()`` once at creation, ``update_one`` after.
"""
from odbms import Model
from pydantic import field_validator

SCOPES = ('global', 'agent')
SOURCES = ('user', 'agent', 'import')
AUTO_SAVE_MODES = ('auto', 'explicit')


class Memory(Model):
    user_id: str
    scope: str = 'global'
    agent_id: str = ''
    """Set when scope is ``agent``."""
    content: str
    source: str = 'agent'
    """``user`` (typed in Settings), ``agent`` (an agent saved it) or ``import``."""
    source_session_id: str = ''
    """The chat it was saved from, if any."""
    pinned: bool = False
    created: str = ''
    updated: str = ''
    """UTC 'YYYY-MM-DD HH:MM:SS'."""

    @field_validator('agent_id', 'source_session_id', 'created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('pinned', mode='before')
    @classmethod
    def _pinned_default(cls, value):
        return bool(value) if value is not None else False


class MemorySettings(Model):
    """One row per person. Missing row = the defaults below."""
    user_id: str
    enabled: bool = True
    """Master switch: off means agents neither read nor save memories."""
    history_enabled: bool = True
    """Agents may search and read this person's past chats (Search Chats / Read Chat)."""
    auto_save: str = 'auto'
    """``auto``: agents save what seems worth keeping (a note shows in the
    chat). ``explicit``: only when the person asks them to remember something."""

    @field_validator('enabled', 'history_enabled', mode='before')
    @classmethod
    def _enabled_default(cls, value):
        return True if value is None else bool(value)

    @field_validator('auto_save', mode='before')
    @classmethod
    def _auto_save_default(cls, value):
        return value if value in AUTO_SAVE_MODES else 'auto'
