"""A swarm run, its workers and each person's swarm limits.

Persistence rule (as elsewhere): instance ``save()`` once at creation,
``update_one`` after.
"""
from __future__ import annotations

import os
from typing import Any

from odbms import Model
from pydantic import Field, field_validator

SWARM_STATUSES = ('running', 'done', 'stopped')
WORKER_STATUSES = ('queued', 'running', 'reviewing', 'done', 'failed', 'stopped')
ENDED = frozenset({'done', 'failed', 'stopped'})

# Defaults a person can change in Settings > Swarms, inside the server's ceilings.
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_WORKERS = 25
DEFAULT_TOKEN_BUDGET = 2_000_000
DEFAULT_AUTO_START = 5


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, '') or default))
    except ValueError:
        return default


def ceiling_concurrency() -> int:
    """Most workers one person may have running at once, across their swarms."""
    return _env_int('COGNITRIX_SWARM_MAX_CONCURRENCY', 8)


def ceiling_workers() -> int:
    return _env_int('COGNITRIX_SWARM_MAX_WORKERS', 50)


def ceiling_tokens() -> int:
    return _env_int('COGNITRIX_SWARM_MAX_TOKENS', 20_000_000)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


class SwarmSettings(Model):
    """One row per person. Missing row = the defaults."""
    user_id: str
    enabled: bool = True
    """Off: agents are not offered the swarm tools."""
    concurrency: int = DEFAULT_CONCURRENCY
    """Workers of one swarm that run at the same time; the rest wait their turn."""
    max_workers: int = DEFAULT_MAX_WORKERS
    """Most workers one swarm may have in total."""
    token_budget: int = DEFAULT_TOKEN_BUDGET
    """Tokens (in and out) a swarm's workers may use before no more start."""
    auto_start: int = DEFAULT_AUTO_START
    """An agent may start this many workers by itself; more asks the person first."""
    admin_concurrency: int = 0
    """A ceiling an admin set for this person (0 = the server's)."""
    admin_max_workers: int = 0

    @field_validator('enabled', mode='before')
    @classmethod
    def _enabled_default(cls, value):
        return True if value is None else bool(value)

    @field_validator('concurrency', 'max_workers', 'token_budget', 'auto_start',
                     'admin_concurrency', 'admin_max_workers', mode='before')
    @classmethod
    def _ints(cls, value, info):
        defaults = {
            'concurrency': DEFAULT_CONCURRENCY, 'max_workers': DEFAULT_MAX_WORKERS,
            'token_budget': DEFAULT_TOKEN_BUDGET, 'auto_start': DEFAULT_AUTO_START,
        }
        return _int(value, defaults.get(info.field_name, 0))


class SwarmRun(Model):
    user_id: str
    """Principal key of the person the swarm works for."""
    session_id: str = ''
    """The lead's chat. Empty for a swarm inside a task run."""
    agent_id: str = ''
    agent_name: str = ''
    title: str = ''
    mode: str = 'fanout'
    """fanout (each worker its own job) or best_of (same job, a judge picks)."""
    status: str = 'running'
    stop_reason: str = ''
    wake: dict = Field(default_factory=dict)
    """When the lead is told: {'until': 'all' | 'count' | 'failure', 'count': n}."""
    limits: dict = Field(default_factory=dict)
    """{'concurrency', 'max_workers', 'token_budget'} fixed when it started."""
    review: bool = False
    """Each result is checked by a reviewer before it counts as done."""
    tokens: int = 0
    reported: list = Field(default_factory=list)
    """Names of workers whose result the lead has already been sent."""
    notes: list = Field(default_factory=list)
    """Things the lead must hear in the next report (a stall, the budget)."""
    judge: dict = Field(default_factory=dict)
    """best_of: {'winner', 'verdict'} once the judge has decided."""
    folder: str = ''
    """Where results are kept on the engine's machine."""
    sandbox: bool = False
    folders: list = Field(default_factory=list)
    """The lead chat's working folders when it started ({path, access, name, primary})."""
    task_id: str = ''
    run_id: str = ''
    step_index: int = -1
    """Set for a swarm inside a task run step."""
    started_at: str = ''
    finished_at: str = ''

    @field_validator('review', 'sandbox', mode='before')
    @classmethod
    def _bools(cls, value):
        return bool(value)

    @field_validator('tokens', 'step_index', mode='before')
    @classmethod
    def _numbers(cls, value, info):
        return _int(value, -1 if info.field_name == 'step_index' else 0)

    @field_validator('wake', 'limits', 'judge', mode='before')
    @classmethod
    def _dicts(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('reported', 'notes', 'folders', mode='before')
    @classmethod
    def _lists(cls, value):
        return value if isinstance(value, list) else []

    @field_validator('session_id', 'agent_id', 'agent_name', 'title', 'stop_reason', 'folder',
                     'task_id', 'run_id', 'started_at', 'finished_at', mode='before')
    @classmethod
    def _texts(cls, value):
        return '' if value is None else str(value)


class SwarmWorker(Model):
    swarm_id: str
    user_id: str
    name: str
    """Short name, unique in its swarm; how the lead and teammates address it."""
    role: str = ''
    """A saved agent's name, or a made-up role for a copy of the lead."""
    persona: str = ''
    agent_id: str = ''
    agent_name: str = ''
    model: dict = Field(default_factory=dict)
    """{'provider', 'model'} when this worker runs on another model than its agent's."""
    brief: str = ''
    depends_on: list = Field(default_factory=list)
    """Names of workers whose results this one needs before it starts."""
    may_edit: bool = False
    status: str = 'queued'
    chat_id: str = ''
    summary: str = ''
    result_path: str = ''
    error: str = ''
    tokens: int = 0
    attempts: int = 0
    review: dict = Field(default_factory=dict)
    """{'verdict': 'pass' | 'revise', 'notes', 'rounds'} from the quality gate."""
    worktree: dict = Field(default_factory=dict)
    """{'repo', 'path', 'branch', 'merged', 'changed'} when it edits its own copy."""
    received: int = 0
    """Messages from teammates and the lead it has been sent."""
    added_by: str = ''
    """Name of the worker that added this job (Add Work); '' for the lead."""
    stalled: bool = False
    position: int = 0
    started_at: str = ''
    finished_at: str = ''

    @field_validator('may_edit', 'stalled', mode='before')
    @classmethod
    def _bools(cls, value):
        return bool(value)

    @field_validator('tokens', 'attempts', 'received', 'position', mode='before')
    @classmethod
    def _numbers(cls, value):
        return _int(value)

    @field_validator('model', 'review', 'worktree', mode='before')
    @classmethod
    def _dicts(cls, value):
        return value if isinstance(value, dict) else {}

    @field_validator('depends_on', mode='before')
    @classmethod
    def _lists(cls, value):
        return value if isinstance(value, list) else []

    @field_validator('role', 'persona', 'agent_id', 'agent_name', 'brief', 'chat_id', 'summary',
                     'result_path', 'error', 'added_by', 'started_at', 'finished_at', mode='before')
    @classmethod
    def _texts(cls, value):
        return '' if value is None else str(value)


MODELS = (SwarmSettings, SwarmRun, SwarmWorker)
