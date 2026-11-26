"""A person's "Auto-approve everything" setting (``users.auto_approve_all``).

When it is on, every approval card that would be put in front of that person
is answered Yes for them instead: ordinary tool calls, full-shell commands,
Coding Agent runs and the coding agent CLIs' own commands, Request Access,
folder requests, copy back from the sandbox, taking over their screen and
actions inside Cognitrix itself. It covers chats in the browser and the
desktop app, chat apps, agent email and background or scheduled tasks the
person started.

It never answers:
- a request for the person to take over the agent's computer (only they can
  do that, a Yes would leave the agent waiting for nobody);
- a chat a visitor started through a public link (``web_turn_ctx`` carries
  ``auto_approve: False``): strangers must not get the owner's standing Yes.

Saved on the account, so it survives the desktop app's restarts (its
localStorage changes with its port) and holds on every device signed in to
this server.

The CLI's ``--dangerously-skip-permissions`` (also ``--yolo`` and
``--dangerously-bypass-approvals-and-sandbox``) turns the same Yes on for one
run without touching the saved setting: ``enable_for_this_run`` covers every
approval in this process (the terminal, sub-agents, task runs it does itself),
and task runs it queues for a worker carry ``TaskRun.auto_approve``. Nobody is
watching such a run, so a request for the person to take over is refused at
once instead of waiting for them.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger('cognitrix.log')

# user id -> (setting, when read). The web app and task workers can be
# separate processes, so a reading only holds for a few seconds: turning it
# off reaches every process almost at once.
_CACHE: dict[str, tuple[bool, float]] = {}
FRESH_FOR = 5.0

# What the setting never answers (see module docstring).
NEVER_CATEGORIES = frozenset({'person_takeover'})


# Set by the CLI's --dangerously-skip-permissions for this process only.
_THIS_RUN = False

# Task run id -> whether it was queued by such a run (never changes).
_RUNS: dict[str, bool] = {}

# What the agent is told when it asks to be taken over in such a run.
NOBODY_WATCHING = 'nobody is watching this run (it runs with --dangerously-skip-permissions)'


def enable_for_this_run(on: bool = True) -> None:
    """Answer every approval in this process with Yes (CLI flag)."""
    global _THIS_RUN
    _THIS_RUN = bool(on)


def on_for_this_run() -> bool:
    return _THIS_RUN


async def run_flagged(run_id: str | None) -> bool:
    """Whether a task run was queued by a --dangerously-skip-permissions run."""
    key = str(run_id or '').strip()
    if not key:
        return False
    if key in _RUNS:
        return _RUNS[key]
    try:
        from cognitrix.tasks.run import TaskRun

        run = await TaskRun.get(key)
        on = bool(getattr(run, 'auto_approve', None)) if run is not None else False
    except Exception:
        logger.debug('Could not read auto-approve for run %s', key, exc_info=True)
        return False
    if len(_RUNS) > 2000:
        _RUNS.clear()
    _RUNS[key] = on
    return on


async def unattended_here(interface: str = 'task') -> bool:
    """Whether work started from here should run with every approval answered:
    this process has the CLI flag, or this is a task run that was queued by
    one. Task runs and hand-offs started here inherit it."""
    if _THIS_RUN:
        return True
    if interface in ('web', 'ws', 'websocket'):
        return False
    from cognitrix.tasks.approvals import task_turn_ctx

    ctx = task_turn_ctx.get(None) or {}
    return await run_flagged(ctx.get('run_id'))


def remember(user_id: str, on: bool) -> None:
    _CACHE[str(user_id)] = (bool(on), time.monotonic())


def forget() -> None:
    _CACHE.clear()
    _RUNS.clear()


async def is_on(user_id: str | None) -> bool:
    """The person's setting, False when unknown."""
    key = str(user_id or '').strip()
    if not key:
        return False
    cached = _CACHE.get(key)
    if cached is not None and time.monotonic() - cached[1] < FRESH_FOR:
        return cached[0]
    try:
        from cognitrix.models.user import User

        user = await User.get(key)
        on = bool(getattr(user, 'auto_approve_all', None)) if user is not None else False
    except Exception:
        logger.debug('Could not read auto-approve for %s', key, exc_info=True)
        return False
    remember(key, on)
    return on


async def set_on(user_id: str, on: bool) -> bool:
    from cognitrix.models.user import User

    await User.update_one({'id': str(user_id)}, {'auto_approve_all': bool(on)})
    remember(user_id, on)
    return bool(on)


async def answers_for(interface: str) -> bool:
    """Whether the person this turn would ask has auto-approve everything on
    (or this is a --dangerously-skip-permissions run)."""
    if interface in ('web', 'ws', 'websocket'):
        from cognitrix.safety.approval_gate import web_turn_ctx

        ctx = web_turn_ctx.get(None) or {}
        if ctx.get('auto_approve') is False:
            return False
        # A helper turn a --dangerously-skip-permissions run started (Call
        # Agent with a signed-in user runs as 'web'): no browser is attached,
        # so nobody could answer anyway. The flag is never on in a process
        # serving the web app (cli/main.py), and a turn with a live browser
        # channel keeps its own person's setting.
        if _THIS_RUN and ctx.get('emit') is None:
            return True
        return await is_on(ctx.get('user_key'))
    if interface == 'task':
        from cognitrix.tasks.approvals import task_turn_ctx

        ctx = task_turn_ctx.get(None) or {}
        if _THIS_RUN or await run_flagged(ctx.get('run_id')):
            return True
        return await is_on(ctx.get('user_id'))
    # The terminal, an editor over ACP and other callers: only the CLI flag.
    return _THIS_RUN
