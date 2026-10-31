"""Who may see and change an agent.

Agents created through the web API belong to the user who created them.
Agents with no owner (the built-in default agent, agents made from the CLI,
and agents saved before ownership existed) are shared: every user can see and
run them, but only admins can change or delete them.

Admins are the users listed in COGNITRIX_ADMIN_EMAILS (comma-separated). When
that is unset, the first user who signed up is the admin, so a single-user
install keeps working without extra setup.
"""
from __future__ import annotations

import os
from typing import Any

from cognitrix.session_ownership import principal_key


def owner_key(user: Any) -> str:
    return principal_key(user)


def _owner(agent: Any) -> str:
    return str(getattr(agent, 'owner_id', None) or '')


def is_shared(agent: Any) -> bool:
    return not _owner(agent)


def can_view(agent: Any, user_key: str | None) -> bool:
    owner = _owner(agent)
    return not owner or (user_key is not None and owner == user_key)


async def is_admin(user: Any) -> bool:
    email = str(getattr(user, 'email', '') or '').strip().lower()
    configured = os.getenv('COGNITRIX_ADMIN_EMAILS', '')
    admins = {e.strip().lower() for e in configured.split(',') if e.strip()}
    if admins:
        return bool(email) and email in admins

    from cognitrix.models.user import User

    users = await User.all()
    if not users:
        return False

    def created(u):
        return str(getattr(u, 'created_at', '') or '')

    first = min(users, key=created)
    return owner_key(first) == owner_key(user)


async def can_edit(agent: Any, user: Any) -> bool:
    owner = _owner(agent)
    if owner:
        return owner == owner_key(user)
    return await is_admin(user)


async def visible_agents(user_key: str | None) -> list:
    from cognitrix.agents import Agent

    return [a for a in await Agent.all() if can_view(a, user_key)]


async def find_visible_agent(
    user_key: str | None, *, agent_id: str | None = None, name: str | None = None
):
    """Return the agent with this id or name that the user may see.

    Names are not unique across users, so a name lookup skips agents the
    user may not see.
    """
    from cognitrix.agents import Agent

    if agent_id:
        agent = await Agent.find_one({'id': agent_id})
        return agent if agent is not None and can_view(agent, user_key) else None
    if name:
        agent = await Agent.find_one({'name': name})
        if agent is not None and can_view(agent, user_key):
            return agent
        # The first match belongs to someone else; look for one this user
        # may see under the same name.
        for candidate in await Agent.find({'name': name}) or []:
            if can_view(candidate, user_key):
                return candidate
        return None
    return None
