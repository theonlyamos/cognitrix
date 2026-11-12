"""Who may see and change an agent.

Agents created through the web API belong to the user who created them.
Agents with no owner (the built-in default agent, agents made from the CLI,
and agents saved before ownership existed) are shared: every user can see and
run them, but only admins can change or delete them.

Admins are the users listed in COGNITRIX_ADMIN_EMAILS (comma-separated). When
that is unset, the first user who signed up is the admin, so a single-user
install keeps working without extra setup.

An owner can share an agent with other people on the server
(``Agent.shared_with``: user key, or ``*`` for everyone, to a role). "use"
lets them see it and chat with it; "edit" also lets them change its
instructions, model, tools and approval rules. Only the owner manages
sharing, connections, folders, check-ins and deletion. Whoever chats with a
shared agent uses the apps and model key its owner connected to it.

Tasks and teams follow the ownership rules through their own owner_id field.
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


EVERYONE = '*'
ROLE_USE = 'use'
ROLE_EDIT = 'edit'
SHARE_ROLES = (ROLE_USE, ROLE_EDIT)


def shares(agent: Any) -> dict[str, str]:
    """Who an agent is shared with: user key (or ``*``) to role."""
    raw = getattr(agent, 'shared_with', None)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(v) in SHARE_ROLES and str(k)}


def share_role(agent: Any, user_key: str | None) -> str | None:
    """The role sharing gives this person on the agent, or None."""
    if not user_key:
        return None
    given = shares(agent)
    roles = {given.get(str(user_key)), given.get(EVERYONE)}
    if ROLE_EDIT in roles:
        return ROLE_EDIT
    return ROLE_USE if ROLE_USE in roles else None


def can_view(agent: Any, user_key: str | None) -> bool:
    owner = _owner(agent)
    if not owner or (user_key is not None and owner == user_key):
        return True
    return share_role(agent, user_key) is not None


async def is_admin(user: Any) -> bool:
    # A role set on the People page wins over the older rules below.
    role = str(getattr(user, 'role', None) or '').lower()
    if role in ('admin', 'member'):
        return role == 'admin'
    email = str(getattr(user, 'email', '') or '').strip().lower()
    configured = os.getenv('COGNITRIX_ADMIN_EMAILS', '')
    admins = {e.strip().lower() for e in configured.split(',') if e.strip()}
    if admins:
        return bool(email) and email in admins

    from cognitrix.models.user import User

    try:
        users = await User.all()
    except Exception:
        users = []
    if not users:
        # No accounts exist, so this caller cannot be a signed-in web user
        # competing with others (CLI, scripts, tests): treat it as the admin.
        return True

    def created(u):
        return str(getattr(u, 'created_at', '') or '')

    first = min(users, key=created)
    return owner_key(first) == owner_key(user)


async def can_manage(agent: Any, user: Any) -> bool:
    """Owner-level control: sharing, connections, folders, check-ins, deleting."""
    owner = _owner(agent)
    if owner:
        return owner == owner_key(user)
    return await is_admin(user)


async def can_edit(agent: Any, user: Any) -> bool:
    if await can_manage(agent, user):
        return True
    return bool(_owner(agent)) and share_role(agent, owner_key(user)) == ROLE_EDIT


async def my_role(agent: Any, user: Any) -> str:
    """'owner', 'edit' or 'use': what this person may do with an agent they can see."""
    if await can_manage(agent, user):
        return 'owner'
    return ROLE_EDIT if await can_edit(agent, user) else ROLE_USE


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
