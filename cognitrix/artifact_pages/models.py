"""Artifacts: pages agents publish (HTML or Markdown), their versions,
comments, viewers' permission answers and the small data store a page can use.

Not to be confused with ``cognitrix.artifacts.Artifact``, which is a single
image or document a tool produced. An ``ArtifactPage`` is a web page with a
history of versions that people open, share and comment on.

Persistence rule (as elsewhere): instance ``save()`` once at creation,
``update_one`` after. Page sources live on disk (``store.page_dir``), not in
the database.
"""
from __future__ import annotations

from typing import Any

from odbms import Model
from pydantic import Field, field_validator

FORMATS = ('html', 'markdown')
ROLE_VIEW = 'view'
ROLE_EDIT = 'edit'
SHARE_ROLES = (ROLE_VIEW, ROLE_EDIT)
EVERYONE = '*'
POWERS = ('ask', 'data', 'download', 'apps')


def _text(value: Any) -> str:
    return '' if value is None else str(value)


class ArtifactPage(Model):
    owner_id: str
    """Principal key of the person the page belongs to (the agent's person)."""
    agent_id: str = ''
    """The agent that made it."""
    session_id: str = ''
    """The chat it was made in, if any. Comments sent to the agent go there."""
    task_id: str = ''
    """The last task that published it: such a page is kept fresh by that task."""
    title: str
    format: str = 'html'
    version: int = 1
    """Newest version number."""
    size_bytes: int = 0
    shared_with: dict = Field(default_factory=dict)
    """User key (or ``*`` for everyone on the server) to ``view`` / ``edit``."""
    public: bool = False
    public_token: str = ''
    share_version: int = 0
    """The version people it's shared with see; 0 means always the newest."""
    pinned: bool = False
    """Shown on the owner's dashboard."""
    powers: list = Field(default_factory=list)
    """What the page may ask its viewers for (``POWERS``)."""
    created: str = ''
    updated: str = ''

    @field_validator('agent_id', 'session_id', 'task_id', 'public_token', 'created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)

    @field_validator('shared_with', mode='before')
    @classmethod
    def _dict_default(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('powers', mode='before')
    @classmethod
    def _list_default(cls, value):
        return [p for p in value if p in POWERS] if isinstance(value, list) else []

    @field_validator('public', 'pinned', mode='before')
    @classmethod
    def _bool_default(cls, value):
        return bool(value) if value is not None else False

    @field_validator('version', 'size_bytes', 'share_version', mode='before')
    @classmethod
    def _int_default(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


class ArtifactVersion(Model):
    page_id: str
    number: int
    size_bytes: int = 0
    sha256: str = ''
    note: str = ''
    """What changed, in the agent's words."""
    agent_id: str = ''
    session_id: str = ''
    task_id: str = ''
    run_id: str = ''
    created: str = ''

    @field_validator('note', 'agent_id', 'session_id', 'task_id', 'run_id', 'sha256', 'created', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)


class ArtifactComment(Model):
    page_id: str
    thread_id: str = ''
    """Empty on the first comment of a thread (the thread is that comment's id)."""
    author_id: str
    """A user key, or ``agent:<agent id>`` for an agent's reply."""
    author_name: str = ''
    body: str
    anchor: dict = Field(default_factory=dict)
    """Where on the page: ``x``/``y`` (0-1 of the page width/height) and ``text``."""
    version: int = 0
    sent_to_agent: bool = False
    resolved: bool = False
    created: str = ''

    @field_validator('thread_id', 'author_name', 'created', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)

    @field_validator('anchor', mode='before')
    @classmethod
    def _dict_default(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('sent_to_agent', 'resolved', mode='before')
    @classmethod
    def _bool_default(cls, value):
        return bool(value) if value is not None else False


class ArtifactConsent(Model):
    """What one viewer allowed a page to do (a subset of the page's powers)."""
    page_id: str
    user_id: str
    allowed: list = Field(default_factory=list)
    created: str = ''

    @field_validator('allowed', mode='before')
    @classmethod
    def _list_default(cls, value):
        return [p for p in value if p in POWERS] if isinstance(value, list) else []


class ArtifactData(Model):
    """One saved value of a page: shared by all viewers (``user_id`` empty) or one viewer's own."""
    page_id: str
    user_id: str = ''
    key: str
    value: str = ''
    """JSON text."""
    updated: str = ''

    @field_validator('user_id', 'value', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)


class ArtifactFile(Model):
    """A document an agent wrote with its Write tool in someone's chat, listed
    on the Artifacts page next to pages and generated images."""
    owner_id: str
    agent_id: str = ''
    session_id: str = ''
    task_id: str = ''
    path: str
    name: str = ''
    mime_type: str = 'text/plain'
    size_bytes: int = 0
    created: str = ''
    updated: str = ''

    @field_validator('agent_id', 'session_id', 'task_id', 'name', 'created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)


MODELS = (ArtifactPage, ArtifactVersion, ArtifactComment, ArtifactConsent, ArtifactData, ArtifactFile)
