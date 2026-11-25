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
"""
from __future__ import annotations

import logging

logger = logging.getLogger('cognitrix.log')

# user id -> setting. Filled on first look and on every change. In-process,
# like the gate's other per-turn state.
_CACHE: dict[str, bool] = {}

# What the setting never answers (see module docstring).
NEVER_CATEGORIES = frozenset({'person_takeover'})


def remember(user_id: str, on: bool) -> None:
    _CACHE[str(user_id)] = bool(on)


def forget() -> None:
    _CACHE.clear()


async def is_on(user_id: str | None) -> bool:
    """The person's setting, False when unknown."""
    key = str(user_id or '').strip()
    if not key:
        return False
    if key in _CACHE:
        return _CACHE[key]
    try:
        from cognitrix.models.user import User

        user = await User.get(key)
        on = bool(getattr(user, 'auto_approve_all', None)) if user is not None else False
    except Exception:
        logger.debug('Could not read auto-approve for %s', key, exc_info=True)
        return False
    _CACHE[key] = on
    return on


async def set_on(user_id: str, on: bool) -> bool:
    from cognitrix.models.user import User

    await User.update_one({'id': str(user_id)}, {'auto_approve_all': bool(on)})
    remember(user_id, on)
    return bool(on)


async def answers_for(interface: str) -> bool:
    """Whether the person this turn would ask has auto-approve everything on."""
    if interface in ('web', 'ws', 'websocket'):
        from cognitrix.safety.approval_gate import web_turn_ctx

        ctx = web_turn_ctx.get(None) or {}
        if ctx.get('auto_approve') is False:
            return False
        return await is_on(ctx.get('user_key'))
    if interface == 'task':
        from cognitrix.tasks.approvals import task_turn_ctx

        ctx = task_turn_ctx.get(None) or {}
        return await is_on(ctx.get('user_id'))
    return False
