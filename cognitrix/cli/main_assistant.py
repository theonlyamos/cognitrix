"""Which agent the command line talks to when no ``--agent`` is given.

The CLI (``cognitrix -p ...``, the interactive shell, ``--ui web|tui``) and
``cognitrix acp`` use the person's **main assistant**: the agent the app marks
as main (``standing.primary``, see cognitrix/assistant/setup.py), not whatever
agent happens to be called "Assistant". ``--agent NAME`` still picks an agent
by name.

Whose main assistant: the account given with ``--user EMAIL``, else the
install's admin (on a desktop install, the person who set it up). With no
accounts at all (a fresh CLI-only install) the CLI keeps its own local
"Assistant" agent, created on first run.

In the sandboxed computer the CLI has its own local database, so the engine
passes the person's main assistant along when it runs ``cognitrix`` there
(``COGNITRIX_MAIN_ASSISTANT``, see :func:`profile_env`). The CLI keeps a local
copy of it under the same id and refreshes it on every run.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger('cognitrix.log')

PROFILE_ENV = 'COGNITRIX_MAIN_ASSISTANT'
LEGACY_NAME = 'Assistant'
# Fields of the main assistant handed to the sandbox CLI. Keys and connected
# apps never are: the sandbox gets model keys only as proxy placeholders.
_PROFILE_LLM_FIELDS = ('provider', 'model', 'temperature', 'max_tokens', 'reasoning_effort')
# Linux caps one environment entry (NAME=value) at 128 KiB; base64 adds a third.
_ENV_ENTRY_LIMIT = 128 * 1024
_PROFILE_MAX_BYTES = 90_000


@dataclass
class Resolved:
    agent: Any
    source: str  # 'named', 'main', 'sandbox', 'legacy' or 'created'
    owner: str | None = None

    @property
    def created(self) -> bool:
        return self.source == 'created'


class NoSuchUser(Exception):
    pass


# --- whose CLI is this ----------------------------------------------------

async def cli_owner(email: str = '') -> Any | None:
    """The account the CLI runs for: ``email``'s, else the install's admin.

    Raises NoSuchUser for an email with no account. None when there are no
    accounts (a CLI-only install).
    """
    from cognitrix.models.user import User

    if email:
        user = await User.find_one({'email': email.strip().lower()})
        if user is None:
            raise NoSuchUser(email)
        return user
    try:
        users = list(await User.all() or [])
    except Exception:
        logger.debug('Could not list accounts for the CLI', exc_info=True)
        return None
    if not users:
        return None
    from cognitrix.agents.access import is_admin

    users.sort(key=lambda u: (str(getattr(u, 'created_at', '') or ''), str(getattr(u, 'id', ''))))
    for user in users:
        if await is_admin(user):
            return user
    return users[0]


def _key(user: Any | None) -> str | None:
    if user is None:
        return None
    from cognitrix.agents.access import owner_key

    try:
        return owner_key(user)
    except ValueError:
        return None


# --- the sandbox copy -----------------------------------------------------

def profile_of(agent: Any) -> dict[str, Any]:
    """What the sandbox CLI needs to act as ``agent``."""
    llm = getattr(agent, 'llm', None)
    tools = [str(getattr(t, 'name', '') or '') for t in (getattr(agent, 'tools', None) or [])]
    return {
        'id': str(agent.id),
        'name': str(agent.name),
        'description': str(getattr(agent, 'description', '') or ''),
        'system_prompt': str(getattr(agent, 'system_prompt', '') or ''),
        'llm': {f: getattr(llm, f) for f in _PROFILE_LLM_FIELDS if llm is not None and getattr(llm, f, None)},
        'tools': [t for t in tools if t],
    }


def _fits(encoded: str) -> bool:
    return len(PROFILE_ENV) + 1 + len(encoded) + 1 < _ENV_ENTRY_LIMIT


def encode_profile(profile: dict[str, Any]) -> str:
    """The profile as one environment value that a command can still start
    with: a very long one loses its tool list, then the end of its prompt."""
    def enc(p: dict[str, Any]) -> str:
        raw = json.dumps(p, ensure_ascii=False).encode('utf-8')
        return base64.b64encode(raw).decode('ascii') if len(raw) <= _PROFILE_MAX_BYTES else ''

    encoded = enc(profile)
    if encoded and _fits(encoded):
        return encoded
    profile = {**profile, 'tools': []}
    prompt = str(profile.get('system_prompt') or '')
    while True:
        encoded = enc({**profile, 'system_prompt': prompt})
        if (encoded and _fits(encoded)) or not prompt:
            return encoded or enc({**profile, 'system_prompt': ''})
        raw_len = len(json.dumps({**profile, 'system_prompt': prompt}, ensure_ascii=False).encode('utf-8'))
        over = max(raw_len - _PROFILE_MAX_BYTES, 1024)
        prompt = prompt[: max(0, len(prompt) - over)]


def decode_profile(value: str) -> dict[str, Any] | None:
    try:
        data = json.loads(base64.b64decode(value.encode('ascii')).decode('utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get('id') or not data.get('name'):
        return None
    return data


async def profile_env(owner_key: Any) -> dict[str, str]:
    """``{COGNITRIX_MAIN_ASSISTANT: ...}`` for a ``cognitrix`` command run in
    this person's sandbox, or {} when they have no main assistant."""
    if not owner_key:
        return {}
    from cognitrix.assistant.setup import main_assistant

    agent = await main_assistant(str(owner_key))
    if agent is None:
        return {}
    return {PROFILE_ENV: encode_profile(profile_of(agent))}


async def _from_profile(profile: dict[str, Any]) -> Any | None:
    """The local copy of the main assistant the engine passed in, made or
    refreshed from ``profile``."""
    from cognitrix.agents import Agent
    from cognitrix.providers import LLM
    from cognitrix.tools.base import ToolManager

    wanted = profile.get('llm') if isinstance(profile.get('llm'), dict) else {}
    config = {'provider': wanted.get('provider') or _default_provider()}
    llm = LLM.load_llm(config['provider'])
    if llm is None:
        logger.warning("Your main assistant's provider (%s) isn't available here", config['provider'])
        return None
    for field in _PROFILE_LLM_FIELDS[1:]:
        if wanted.get(field) not in (None, ''):
            setattr(llm, field, wanted[field])
    tools = [t for t in (ToolManager.get_by_name(n) for n in profile.get('tools') or []) if t is not None]

    agent = await Agent.find_one({'id': str(profile['id'])})
    if agent is None:
        agent = Agent(id=str(profile['id']), name=str(profile['name']), llm=llm,
                      system_prompt=str(profile.get('system_prompt') or ''), tools=tools)
    else:
        agent.name = str(profile['name'])
        agent.llm = llm
        agent.system_prompt = str(profile.get('system_prompt') or '')
        agent.tools = tools
    agent.description = str(profile.get('description') or '')
    await agent.save()
    return agent


# --- picking the agent ------------------------------------------------------

def _default_provider() -> str:
    try:
        from cognitrix.config import settings

        return settings.ai_provider or 'google'
    except Exception:
        return 'google'


async def _by_name(name: str, owner: str | None) -> Any | None:
    from cognitrix.agents import Agent
    from cognitrix.agents import access as agent_access

    if owner:
        return await agent_access.find_visible_agent(owner, name=name)
    agent = await Agent.find_one({'name': name})
    if agent is None:
        # Case-insensitive, like the editor (ACP) and the shell's /agent.
        for candidate in await Agent.all() or []:
            if str(getattr(candidate, 'name', '')).strip().lower() == name.strip().lower():
                return candidate
    return agent


async def _local_agent(name: str) -> Any | None:
    """The CLI's own agent called ``name``: one no account owns."""
    from cognitrix.agents import Agent

    for agent in await Agent.all() or []:
        if (str(getattr(agent, 'name', '')).strip().lower() == name.lower()
                and not getattr(agent, 'owner_id', None) and not getattr(agent, 'is_sub_agent', False)):
            return agent
    return None


async def resolve(name: str = '', email: str = '') -> Resolved | None:
    """The agent the CLI should use, without creating one.

    ``name`` set: that agent, or None (the caller creates it, as before).
    Otherwise the main assistant, from the engine's profile in the sandbox,
    else the account's; else the local "Assistant" agent. None when nothing
    fits and the caller should create the local "Assistant".
    """
    owner = _key(await cli_owner(email))
    if name:
        agent = await _by_name(name, owner)
        if agent is not None:
            return Resolved(agent, 'named', owner)
        return None  # the caller makes a new agent with that name, as before

    raw = os.environ.get(PROFILE_ENV, '')
    profile = decode_profile(raw) if raw else None
    if profile is not None:
        agent = await _from_profile(profile)
        if agent is not None:
            return Resolved(agent, 'sandbox', owner)

    if owner:
        from cognitrix.assistant.setup import main_assistant

        agent = await main_assistant(owner)
        if agent is not None:
            return Resolved(agent, 'main', owner)

    # No account, or one with no agents of its own yet: the CLI's own agent.
    agent = await _local_agent(LEGACY_NAME)
    if agent is not None:
        return Resolved(agent, 'legacy', owner)
    return None


def describe(resolved: Resolved) -> str:
    """One line on which agent the CLI is using, for logs and ``--verbose``."""
    name = getattr(resolved.agent, 'name', '?')
    return {
        'named': f'Using {name}',
        'main': f'Using your main assistant, {name}',
        'sandbox': f'Using your main assistant, {name}',
        'legacy': f'Using {name} (no account here, so no main assistant)',
        'created': f'Created {name} (no account here, so no main assistant)',
    }.get(resolved.source, f'Using {name}')
