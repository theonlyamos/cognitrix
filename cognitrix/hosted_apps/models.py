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
    icon: str = ''
    """'custom' (a stored image, see hosted_apps/icons.py) or 'generated' (its initials);
    '' for apps made before icons, until the startup check looks in their folder."""
    created: str = ''
    updated: str = ''

    @field_validator('agent_id', 'session_id', 'task_id', 'description', 'folder', 'command', 'install',
                     'last_error', 'branch', 'last_visit', 'api_key_id', 'icon', 'created', 'updated', mode='before')
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


POWERS = ('notify', 'apps')
"""What an app may ask the person who opens it for, on top of showing itself:

- ``notify``: system notifications (the desktop app's, or the browser's);
- ``apps``: the person's connected apps (reads freely; any change asks each time).

The desktop app's tray icon is a third one, kept per computer by the desktop app."""


class HostedAppGrant(Model):
    """What one person allowed an app to do for them (and what they were asked)."""
    app_id: str
    user_id: str
    allowed: list = Field(default_factory=list)
    asked: list = Field(default_factory=list)
    """Powers the person answered about, yes or no: those aren't asked again."""
    created: str = ''
    updated: str = ''

    @field_validator('allowed', 'asked', mode='before')
    @classmethod
    def _powers(cls, value):
        return [p for p in value if p in POWERS] if isinstance(value, list) else []

    @field_validator('created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)


ACCESS_LEVELS = ('read', 'all')
"""What an app's server code may do in one connected app: only look things up, or change things too."""


class HostedAppAccess(Model):
    """What the owner lets an app's own server code use (see ``hosted_apps/access.py``).

    Kept in its own table, one row per app. The app's pages asking a viewer for
    their connected apps is ``HostedAppGrant`` above; this is the owner's side,
    for work that runs with nobody looking (a nightly sync, a webhook).
    """
    app_id: str
    apps: dict = Field(default_factory=dict)
    """Connected app (MCP server row) id -> 'read' or 'all'."""
    credentials: list = Field(default_factory=list)
    """Names of the owner's saved sign-ins the app may send requests with."""
    secrets: str = ''
    """The app's own secrets, sealed JSON ``{NAME: value}``; they reach it as environment variables."""
    wants: dict = Field(default_factory=dict)
    """What the agent that built it asked for: ``{'apps': [...], 'credentials': [...], 'secrets': [...]}``."""
    full_api: bool = False
    """Its key may also run agents and read tasks (Publish App ``use_cognitrix_api``)."""
    paused: bool = False
    """Someone other than the owner published the live code: nothing here is used until the owner says so."""
    pending_restart: bool = False
    """Secrets changed since the running version started."""
    pending_key_id: str = ''
    """The key of a version that is starting right now."""
    created: str = ''
    updated: str = ''

    @field_validator('apps', 'wants', mode='before')
    @classmethod
    def _dict_default(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('credentials', mode='before')
    @classmethod
    def _list_default(cls, value):
        return [str(v) for v in value] if isinstance(value, list) else []

    @field_validator('full_api', 'paused', 'pending_restart', mode='before')
    @classmethod
    def _bool_default(cls, value):
        return bool(value) if value is not None else False

    @field_validator('secrets', 'pending_key_id', 'created', 'updated', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)


MODELS = (HostedApp, HostedAppVersion, HostedAppGrant, HostedAppAccess)
