"""An app agents built and the versions it went through.

Persistence rule (as elsewhere): instance ``save()`` once at creation,
``update_one`` after. The code itself lives with the runner (the user's
sandbox computer, or this machine), not in the database.
"""
from __future__ import annotations

from typing import Any

from odbms import Model
from pydantic import Field, field_validator

STATUSES = ('building', 'online', 'asleep', 'failed', 'stopped')
RUNTIMES = ('sandbox', 'local')
VERSION_STATUSES = ('building', 'live', 'ok', 'failed')


def _text(value: Any) -> str:
    return '' if value is None else str(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class HostedApp(Model):
    owner_id: str
    """Principal key of the person the app belongs to."""
    agent_id: str = ''
    session_id: str = ''
    """The chat that built it; Edit in chat opens it."""
    task_id: str = ''
    """A task that keeps it fresh (it last published it)."""
    slug: str
    """The app's name in its link: /a/<slug>/."""
    title: str
    description: str = ''
    runtime: str = 'sandbox'
    folder: str = ''
    """Where the source was published from (on the engine's machine)."""
    command: str = ''
    install: str = ''
    port: int = 0
    """A fixed port the app insists on; 0 = whatever $PORT says."""
    env: dict = Field(default_factory=dict)
    status: str = 'building'
    version: int = 0
    """The live version (0 until one starts)."""
    latest: int = 0
    """The newest version tried."""
    last_error: str = ''
    branch: str = ''
    """For previews: the git branch it was built from."""
    shared_with: dict = Field(default_factory=dict)
    pinned: bool = False
    sleep_after: int = 15
    """Minutes with no visitors before it sleeps; 0 = never."""
    visits: dict = Field(default_factory=dict)
    """'YYYY-MM-DD' -> page views that day (last 30 days kept)."""
    last_visit: str = ''
    api_key_id: str = ''
    """The Cognitrix API key the app was given, if it asked for one."""
    created: str = ''
    updated: str = ''

    @field_validator('agent_id', 'session_id', 'task_id', 'description', 'folder', 'command', 'install',
                     'last_error', 'branch', 'last_visit', 'api_key_id', 'created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)

    @field_validator('shared_with', 'env', 'visits', mode='before')
    @classmethod
    def _dict_default(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('pinned', mode='before')
    @classmethod
    def _bool_default(cls, value):
        return bool(value) if value is not None else False

    @field_validator('port', 'version', 'latest', mode='before')
    @classmethod
    def _int_default(cls, value):
        return _int(value)

    @field_validator('sleep_after', mode='before')
    @classmethod
    def _sleep_default(cls, value):
        return 15 if value is None else _int(value)


class HostedAppVersion(Model):
    app_id: str
    number: int
    status: str = 'building'
    note: str = ''
    error: str = ''
    log: str = ''
    """The end of what it printed while starting."""
    branch: str = ''
    agent_id: str = ''
    session_id: str = ''
    task_id: str = ''
    created: str = ''

    @field_validator('note', 'error', 'log', 'branch', 'agent_id', 'session_id', 'task_id', 'created',
                     mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)

    @field_validator('number', mode='before')
    @classmethod
    def _int_default(cls, value):
        return _int(value)


MODELS = (HostedApp, HostedAppVersion)
