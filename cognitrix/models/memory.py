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

SCOPES = ('global', 'agent', 'project')
SOURCES = ('user', 'agent', 'import', 'dream')
AUTO_SAVE_MODES = ('auto', 'ask', 'explicit')
KINDS = ('about', 'preference', 'person', 'project', 'howto', 'other')
"""What a memory is about. '' means not sorted yet (dream mode sorts them)."""
CHANGE_ACTIONS = ('create', 'update', 'delete', 'approve')


class Memory(Model):
    user_id: str
    scope: str = 'global'
    agent_id: str = ''
    """Set when scope is ``agent``."""
    project_id: str = ''
    """Set when scope is ``project``: only chats and tasks in that project see it."""
    content: str
    source: str = 'agent'
    """``user`` (typed in Settings), ``agent`` (an agent saved it) or ``import``."""
    source_session_id: str = ''
    """The chat it was saved from, if any."""
    pinned: bool = False
    pending: bool = False
    """Proposed but not yet approved by the person ("Ask me first", a save
    after the agent read web or app content, or an import). Pending memories
    are never shown to agents."""
    replaces: str = ''
    """For a pending change to an existing memory: that memory's id. Approving
    the change deletes the old one."""
    created: str = ''
    updated: str = ''
    """UTC 'YYYY-MM-DD HH:MM:SS'."""
    kind: str = ''
    """One of KINDS, or '' when nobody sorted it yet."""
    valid_until: str = ''
    """'YYYY-MM-DD' for facts that stop being true ("on leave until the 20th").
    From the day after, agents no longer see it and dream mode retires it."""
    last_used: str = ''
    """UTC time an agent last found it by search or as a relevant fact."""
    use_count: int = 0

    @field_validator('agent_id', 'project_id', 'source_session_id', 'created', 'updated', 'replaces',
                     'kind', 'valid_until', 'last_used', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('use_count', mode='before')
    @classmethod
    def _count_default(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @field_validator('pinned', 'pending', mode='before')
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
    chat). ``ask``: agents propose memories and the person approves each one.
    ``explicit``: only when the person asks them to remember something."""
    dream_enabled: bool = True
    """Dream mode: a nightly pass that tidies memories (see cognitrix/memory/dream.py)."""

    @field_validator('enabled', 'history_enabled', 'dream_enabled', mode='before')
    @classmethod
    def _enabled_default(cls, value):
        return True if value is None else bool(value)

    @field_validator('auto_save', mode='before')
    @classmethod
    def _auto_save_default(cls, value):
        return value if value in AUTO_SAVE_MODES else 'auto'


class MemoryChange(Model):
    """One change to a memory, kept so it can be undone.

    ``before`` and ``after`` are JSON snapshots of the memory's fields ('' when
    there was none: ``create`` has no before, ``delete`` no after)."""
    user_id: str
    memory_id: str
    action: str
    by: str = 'agent'
    """'person', 'agent', 'dream' or 'import'."""
    dream_id: str = ''
    before: str = ''
    after: str = ''
    note: str = ''
    created: str = ''
    undone: bool = False

    @field_validator('dream_id', 'before', 'after', 'note', 'created', 'by', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('undone', mode='before')
    @classmethod
    def _undone_default(cls, value):
        return bool(value) if value is not None else False


class MemoryProfile(Model):
    """The short "About you" paragraph agents read before the list of memories.

    Dream mode writes it. Once the person edits it themselves (``locked``),
    dream mode leaves it alone until they unlock it."""
    user_id: str
    text: str = ''
    locked: bool = False
    updated: str = ''
    dream_id: str = ''

    @field_validator('text', 'updated', 'dream_id', mode='before')
    @classmethod
    def _text_default(cls, value):
        return '' if value is None else str(value)

    @field_validator('locked', mode='before')
    @classmethod
    def _locked_default(cls, value):
        return bool(value) if value is not None else False
