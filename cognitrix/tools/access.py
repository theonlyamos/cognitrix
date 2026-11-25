"""Request Access: an agent asks the person for a tool or connected app it doesn't have.

The agent calls ``request_access`` instead of giving up. The chat shows a
card (the approval card, ``access_request`` category) saying what it wants and
why, and the person picks:

- **Allow in this chat**: the tool or app works for this agent in this chat
  (saved on the chat, ``Session.granted_access``) and from the next step on;
- **Add to agent**: saved on the agent itself (only its owner sees this);
- **Connect <app>** first, when the app isn't connected yet (the usual
  sign-in, started from the card);
- **Deny**.

Only a person can grant: the call always asks (no auto-approve, "always
allow", bypass mode or reviewer answers it, see ``approval_gate`` and
``action_reviewer``), and the tool only grants what the person's answer
(``access_grant``, filled in by the agent loop from that answer) says.

``precheck`` runs before anyone is asked: it works out what is being asked
for (and what the card shows), and answers straight away when there is
nothing to ask (the agent already has it, it doesn't exist, or a setting
rules it out).
"""
from __future__ import annotations

import contextvars
import difflib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

logger = logging.getLogger('cognitrix.log')

TOOL_NAME = 'request_access'
ACCESS_CATEGORY = 'access_request'
# How long the card waits: connecting an app can take a few minutes.
ANSWER_TIMEOUT = 15 * 60.0

GRANTS = ('chat', 'agent')

# Tools turned on by a setting or added to every chat already: asking for
# them per agent would go around that setting, or changes nothing.
_NOT_GRANTABLE_CATEGORIES = frozenset({
    'payments', 'swarm', 'swarm_worker', 'meetings', 'email', 'brief', 'automation', 'board',
    'mcp', 'mcp_dynamic', 'code_mode', 'artifacts', 'apps', 'cards',
})
_NOT_GRANTABLE_NAMES = frozenset({
    'request_access', 'request_folder', 'ask_user', 'memory', 'search_chats', 'read_chat',
    'follow_up_later', 'set_theme', 'load_skill', 'show_card', 'list_mcp_tools', 'run_mcp_tool',
    'send_email',
})
_MCP_TOOL_NAMES = ('list_mcp_tools', 'run_mcp_tool')


def _key(name: Any) -> str:
    return re.sub(r'[\s\-_]+', '_', str(name or '').strip()).lower()


def _words(text: Any) -> str:
    return re.sub(r'[\s\-_]+', ' ', str(text or '')).strip().lower()


def _first_line(text: Any, limit: int = 220) -> str:
    for line in str(text or '').strip().splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ''


# --- the chat's grants ----------------------------------------------------------------

GrantSink = Callable[[str, str], None]
"""(kind 'tool' | 'app', name) -> saves it on the running chat."""

_grant_sink: contextvars.ContextVar[GrantSink | None] = contextvars.ContextVar('access_grant_sink', default=None)


def set_grant_sink(sink: GrantSink | None):
    return _grant_sink.set(sink)


def reset_grant_sink(token) -> None:
    _grant_sink.reset(token)


def merge_grant(granted: dict | None, kind: str, name: str) -> dict:
    """``granted`` with ``name`` added to its ``tools`` or ``apps`` list."""
    out = {'tools': list((granted or {}).get('tools') or []), 'apps': list((granted or {}).get('apps') or [])}
    bucket = out['tools' if kind == 'tool' else 'apps']
    if name and name not in bucket:
        bucket.append(name)
    return out


# --- who is asking -----------------------------------------------------------------------

def _person(agent: Any) -> str | None:
    """The person answering in this turn (their key), else the agent's owner."""
    try:
        from cognitrix.safety.approval_gate import web_turn_ctx

        key = (web_turn_ctx.get(None) or {}).get('user_key')
        if key:
            return str(key)
    except Exception:
        pass
    try:
        from cognitrix.tools.utils import current_execution_context

        user = getattr(current_execution_context(), 'user_id', None)
        if user:
            return str(user)
    except Exception:
        pass
    owner = getattr(agent, 'owner_id', None)
    return str(owner) if owner else None


def _owns(agent: Any, person: str | None) -> bool:
    owner = str(getattr(agent, 'owner_id', '') or '')
    return bool(owner) and owner == str(person or '')


# --- what is asked for ------------------------------------------------------------------

@dataclass
class Target:
    kind: str  # 'tool' | 'app'
    name: str  # tool name, or the connection id / catalog id of an app
    label: str
    description: str = ''
    tool: Any = None
    # Apps only
    catalog_id: str | None = None
    connection: Any = None  # MCPServerConfig when the person has connected it
    connected: bool = False
    can_connect: bool = False
    setup: str = ''
    extra: dict[str, Any] = field(default_factory=dict)


def _all_tools() -> list:
    from cognitrix.tools.base import ToolManager

    try:
        return list(ToolManager.list_all_tools())
    except Exception:
        logger.debug('Tool list unavailable', exc_info=True)
        return []


def grantable_tool(t: Any) -> bool:
    if _key(getattr(t, 'name', '')) in _NOT_GRANTABLE_NAMES:
        return False
    return str(getattr(t, 'category', '') or '').lower() not in _NOT_GRANTABLE_CATEGORIES


def find_tool(name: str) -> Any | None:
    """A registered tool by name (case, spaces, dashes, underscores don't matter)."""
    key = _key(name)
    squashed = key.replace('_', '')
    tools = _all_tools()
    for t in tools:
        if _key(getattr(t, 'name', '')) == key:
            return t
    # "web fetch" for Webfetch, "web_fetch" for Web Fetch.
    for t in tools:
        if _key(getattr(t, 'name', '')).replace('_', '') == squashed:
            return t
    return None


def suggest_tools(name: str, reason: str = '', limit: int = 5) -> list[str]:
    """Close tool names for something that didn't match, best first."""
    tools = [t for t in _all_tools() if grantable_tool(t)]
    names = {_words(t.name): t.name for t in tools}
    close = difflib.get_close_matches(_words(name), list(names), n=limit, cutoff=0.5)
    out = [names[c] for c in close]
    wanted = {w for w in re.findall(r'[a-z0-9]+', f'{name} {reason}'.lower()) if len(w) > 3}
    if wanted and len(out) < limit:
        scored = []
        for t in tools:
            title = _words(t.name)
            text = _first_line(t.description).lower()
            points = sum(2 if w in title else 1 if w in text else 0 for w in wanted)
            if points >= 2 and t.name not in out:
                scored.append((points, t.name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        out.extend(n for _h, n in scored[: limit - len(out)])
    return out


def _app_names(app: Any) -> set[str]:
    return {_words(app.id), _words(app.name), _words(str(app.name).replace('Google ', ''))}


async def _find_app(name: str, person: str | None) -> Target | None:
    """A connected app (the person's connection) or a catalog app they can connect."""
    from cognitrix.mcp import catalog, registry

    wanted = _words(name)
    if not wanted:
        return None
    try:
        servers = await registry.visible_servers(person)
    except Exception:
        logger.debug('Connected apps unavailable', exc_info=True)
        servers = []
    for config in servers:
        app = catalog.get(getattr(config, 'catalog_id', None))
        names = {_words(config.name), _words(config.id)} | (_app_names(app) if app else set())
        if wanted in names:
            status = str(getattr(config, 'status', '') or '')
            ready = bool(getattr(config, 'enabled', True)) and status not in ('needs_reauth', 'signed_out') and not (
                getattr(config, 'auth_type', None) == 'oauth' and not status)
            return Target(
                kind='app', name=str(config.id or config.name), label=app.name if app else config.name,
                description=(app.description if app else getattr(config, 'description', '')) or '',
                catalog_id=app.id if app else None, connection=config, connected=ready,
                can_connect=bool(app and catalog.available(app) and catalog.offered(app)),
                setup=catalog.setup_hint(app) if app else '',
            )
    for app in catalog.CATALOG:
        if wanted in _app_names(app) and catalog.offered(app):
            return Target(
                kind='app', name=app.id, label=app.name, description=app.description,
                catalog_id=app.id, connected=False, can_connect=catalog.available(app),
                setup=catalog.setup_hint(app),
            )
    return None


def _app_suggestions(name: str) -> list[str]:
    from cognitrix.mcp import catalog

    names = {_words(a.name): a.name for a in catalog.listed()}
    return [names[c] for c in difflib.get_close_matches(_words(name), list(names), n=4, cutoff=0.4)]


def _has_tool(agent: Any, name: str) -> bool:
    key = _key(name)
    return any(_key(getattr(t, 'name', '')) == key for t in getattr(agent, 'tools', None) or [])


async def _agent_has_app(agent: Any, target: Target) -> bool:
    if target.connection is None:
        return False
    from cognitrix.mcp import registry

    try:
        servers = await registry.agent_servers(agent)
    except Exception:
        return False
    return any(c.key == target.connection.key for c in servers)


@dataclass
class Precheck:
    target: Target | None = None
    # Set when nobody needs to be asked: the answer for the agent.
    answer: ToolOutcome | None = None
    card: dict[str, Any] | None = None


def _normal_kind(kind: Any) -> str:
    kind = str(kind or '').strip().lower()
    return 'app' if kind in ('app', 'apps', 'connected_app', 'connected app', 'connection', 'integration') else 'tool'


async def precheck(agent: Any, args: dict[str, Any]) -> Precheck:
    """What is asked for, and whether a person needs to be asked at all."""
    kind = _normal_kind(args.get('kind'))
    name = str(args.get('name') or '').strip()
    reason = str(args.get('reason') or '').strip()
    if not name:
        return Precheck(answer=ToolOutcome.failure('missing_name', 'Say which tool or app you need (name).'))
    person = _person(agent)

    if kind == 'tool':
        found = find_tool(name)
        if found is None:
            # Maybe it's an app ("gmail") asked for as a tool.
            app = await _find_app(name, person)
            if app is not None:
                kind, target = 'app', app
            else:
                close = suggest_tools(name, reason)
                hint = f" Close matches: {', '.join(close)}." if close else ''
                return Precheck(answer=ToolOutcome.failure(
                    'unknown_tool', f"There is no tool called '{name}'.{hint} Ask again with the exact name."))
        else:
            from cognitrix.safety.permissions import is_denied, switched_off

            if _has_tool(agent, found.name):
                return Precheck(answer=ToolOutcome.success(
                    f"You already have {found.name}. Call it directly (with Find Tools first if it isn't loaded)."))
            if not grantable_tool(found):
                return Precheck(answer=ToolOutcome.failure(
                    'not_requestable',
                    f"{found.name} can't be asked for here: it's turned on in the person's settings, not per "
                    'agent. Tell the person what you need instead.'))
            if is_denied(agent, found.name):
                return Precheck(answer=ToolOutcome.failure(
                    'permission_denied',
                    f"The person set {found.name} to never run for this agent. Don't ask for it; do without it."))
            off = switched_off(agent, found.name)
            if off:
                return Precheck(answer=ToolOutcome.failure('permission_denied', f'{off}. Do without it.'))
            target = Target(kind='tool', name=found.name, label=found.name,
                            description=_first_line(found.description), tool=found)
    if kind == 'app':
        target = await _find_app(name, person)
        if target is None:
            close = _app_suggestions(name)
            hint = f" Apps that can be connected include: {', '.join(close)}." if close else ''
            return Precheck(answer=ToolOutcome.failure(
                'unknown_app',
                f"'{name}' isn't a connected app here.{hint} If it's a website, use the browser or web tools "
                'instead; if it is an app the person uses, tell them it needs to be added in Settings › '
                'Connected apps.'))
        if await _agent_has_app(agent, target) and target.connected:
            return Precheck(answer=ToolOutcome.success(
                f'You already have {target.label}. Use List Mcp Tools and Run Mcp Tool to work with it.'))
        if not _owns(agent, person) and getattr(agent, 'owner_id', None):
            return Precheck(answer=ToolOutcome.failure(
                'not_owner',
                f"Only this agent's owner can give it {target.label}. Tell the person what you need instead."))
        if not target.connected and not target.can_connect:
            return Precheck(answer=ToolOutcome.failure(
                'app_not_set_up',
                f"{target.label} isn't connected and can't be connected on this server yet. "
                f"{target.setup or ''} Tell the person.".strip()))

    card = card_for(target, reason, can_add=_owns(agent, person))
    card['agent'] = str(getattr(agent, 'name', '') or '')
    return Precheck(target=target, card=card)


def card_for(target: Target, reason: str, *, can_add: bool) -> dict[str, Any]:
    """What the approval card shows (the ``access`` part of the event)."""
    card: dict[str, Any] = {
        'kind': target.kind,
        'name': target.name,
        'label': target.label,
        'reason': reason[:400],
        'description': target.description[:300],
        'can_add': bool(can_add),
    }
    if target.kind == 'app':
        card['app'] = {
            'id': target.catalog_id,
            'connected': bool(target.connected),
            'can_connect': bool(target.can_connect and target.catalog_id),
            'setup': target.setup[:400] if not target.connected else '',
        }
    return card


def risk_details(args: dict[str, Any]) -> str:
    kind = 'the connected app' if _normal_kind(args.get('kind')) == 'app' else 'the tool'
    name = str(args.get('name') or '').strip()[:120] or '?'
    reason = str(args.get('reason') or '').strip()[:300]
    return f'Asks for {kind} {name}' + (f': {reason}' if reason else '')


# --- granting ---------------------------------------------------------------------------

def _mcp_tools() -> list:
    from cognitrix.tools.base import ToolManager

    return [t for t in (ToolManager.get_by_name(n) for n in _MCP_TOOL_NAMES) if t is not None]


def _note_loaded(names: list[str]) -> None:
    """Tools on demand: send the new tools' schemas from the next step on."""
    try:
        from cognitrix.tools.code_mode import loaded_this_turn

        loaded = loaded_this_turn.get()
        if loaded is not None:
            loaded.update(names)
    except Exception:
        pass


def _give_tools(agent: Any, tools: list) -> list[str]:
    """Add tools to the running agent (this turn's copy). Their names."""
    added = []
    for t in tools:
        if not _has_tool(agent, t.name):
            agent.tools.append(t)
            added.append(t.name)
    _note_loaded([t.name for t in tools])
    return added


async def _save_on_agent(agent: Any, target: Target) -> bool:
    """Add the tool or app to the stored agent. False if it couldn't be saved."""
    from cognitrix.models.agent import Agent

    stored = await Agent.get(str(getattr(agent, 'id', '') or ''))
    if stored is None:
        return False
    if target.kind == 'tool':
        tools = list(stored.tools or [])
        if not _has_tool(stored, target.tool.name):
            tools.append(target.tool)
        stored.tools = tools
    else:
        refs = [str(r) for r in (stored.mcp_servers or [])]
        if target.name not in refs:
            refs.append(target.name)
        stored.mcp_servers = refs
        names = {_key(getattr(t, 'name', '')) for t in stored.tools or []}
        stored.tools = [*(stored.tools or []), *[t for t in _mcp_tools() if _key(t.name) not in names]]
    await stored.save()
    return True


async def grant(agent: Any, target: Target, how: str) -> ToolOutcome:
    """Give the agent the tool or app: in this chat, or saved on the agent."""
    if how == 'agent' and not _owns(agent, _person(agent)):
        how = 'chat'
    if target.kind == 'app' and not target.connected:
        return ToolOutcome.failure(
            'app_not_connected',
            f"{target.label} still isn't connected, so you can't use it yet. Ask the person to press Connect on "
            f'the card (or in Settings › Connected apps) and then ask again.')
    if how == 'agent':
        try:
            saved = await _save_on_agent(agent, target)
        except Exception:
            logger.exception('Could not add %s to agent %s', target.name, getattr(agent, 'id', ''))
            saved = False
        if not saved:
            how = 'chat'
    # Saved on the chat either way: an agent already loaded for this chat
    # (its open stream) keeps it without reloading.
    sink = _grant_sink.get()
    if sink is not None:
        sink(target.kind, target.name)

    if target.kind == 'tool':
        _give_tools(agent, [target.tool])
        where = 'for this agent from now on' if how == 'agent' else 'in this chat'
        return ToolOutcome.success(
            f'The person gave you {target.tool.name} {where}. You can call it now: carry on with the task.')

    refs = list(getattr(agent, 'mcp_servers', None) or [])
    if target.name not in refs:
        refs.append(target.name)
        agent.mcp_servers = refs
    _give_tools(agent, _mcp_tools())
    where = 'for this agent from now on' if how == 'agent' else 'in this chat'
    return ToolOutcome.success(
        f'The person gave you {target.label} {where}. Use List Mcp Tools (server '
        f"'{target.connection.name if target.connection else target.name}') to see its tools and Run Mcp Tool "
        'to use them, then carry on with the task.')


def chat_grants(agent: Any, granted: dict | None, *, request_tool: bool) -> Any:
    """This turn's copy of the agent with Request Access and the chat's grants.

    Always a copy with its own ``tools`` and ``mcp_servers`` lists, so a grant
    made mid-turn changes only this turn's agent.
    """
    tools = list(getattr(agent, 'tools', None) or [])
    refs = [str(r) for r in (getattr(agent, 'mcp_servers', None) or [])]
    have = {_key(getattr(t, 'name', '')) for t in tools}

    def add(t: Any) -> None:
        if t is not None and _key(t.name) not in have:
            tools.append(t)
            have.add(_key(t.name))

    if request_tool:
        add(find_tool(TOOL_NAME))
    from cognitrix.safety.permissions import is_denied

    for name in (granted or {}).get('tools') or []:
        found = find_tool(name)
        if found is not None and grantable_tool(found) and not is_denied(agent, found.name):
            add(found)
    apps = [str(a) for a in (granted or {}).get('apps') or [] if str(a)]
    for ref in apps:
        if ref not in refs:
            refs.append(ref)
    if apps:
        for t in _mcp_tools():
            add(t)
    return agent.model_copy(update={'tools': tools, 'mcp_servers': refs})


PROMPT_NOTE = (
    "If you need a tool or a connected app (like Gmail, GitHub, Slack or Notion) that you don't have, "
    "don't give up or tell the person you can't: call Request Access with what you need and why. The "
    'person decides on a card in the chat, and when they allow it you can use it straight away.'
)


@tool(category='system', retryable=False, max_attempts=1, approval_mode='always',
      supported_interfaces=['web', 'ws', 'task'])
async def request_access(name: str, reason: str, kind: str = 'tool', *, parent: Any = None,
                         access_grant: str | None = None) -> ToolOutcome:
    """Ask the person for a tool or connected app you don't have yet, instead of giving up.

    The person sees a card in the chat with what you asked for and why, and can allow it for this chat,
    add it to you for good, connect the app first, or say no. When they allow it you can use it right
    away. Only ask for something you need for the task at hand, and ask once.

    Args:
        name (str): The tool's name (like "Bash" or "Web Fetch") or the app's name (like "Gmail", "GitHub").
        reason (str): One sentence on why you need it for this task, shown to the person.
        kind (str): 'tool' for one of Cognitrix's tools, 'app' for a connected app.
    """
    if parent is None:
        return ToolOutcome.failure('no_agent', 'Request Access needs an agent.')
    if access_grant not in GRANTS:
        # Only a person's answer grants anything (the agent loop fills this
        # in from it); an approval without one gives nothing.
        return ToolOutcome.failure(
            'approval_required', 'Nothing was granted: the person has to allow this on the card in the chat.',
            denied=True)
    checked = await precheck(parent, {'kind': kind, 'name': name, 'reason': reason})
    if checked.answer is not None:
        return checked.answer
    return await grant(parent, checked.target, access_grant)
