"""Which MCP servers a person or an agent may use.

Servers come from two places:

- The database (``MCPServer``). A row belongs to the user who added it, or is
  shared (no owner) when an admin added it for everyone.
- The CLI's ``~/.cognitrix/mcp.json``. Those servers are shared, and only the
  machine's operator can change them (with ``cognitrix mcp ...``).

An agent lists its servers in ``agent.mcp_servers`` by database id, or by name
for ``mcp.json`` servers (older agents may also use a database server's name).
An agent may use a server that is shared or that belongs to the agent's
owner. A shared agent (no owner) may only use shared servers, so nobody can
reach another person's server, or the tokens in its headers, through it.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from cognitrix.mcp.server_manager import MCPServerConfig, MCPTransportType

logger = logging.getLogger('cognitrix.log')


def _load_json_object(text: str | None) -> dict[str, str]:
    from cognitrix.common.secret_box import unseal_or_empty

    text = unseal_or_empty(text)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def config_from_row(row: Any) -> MCPServerConfig:
    try:
        transport = MCPTransportType(str(row.transport))
    except ValueError:
        transport = MCPTransportType.HTTP
    return MCPServerConfig(
        name=row.name,
        transport=transport,
        description=row.description or '',
        url=row.url or None,
        command=row.command or None,
        args=list(row.args or []),
        env=_load_json_object(row.env) or None,
        headers=_load_json_object(row.headers) or None,
        timeout=int(row.timeout or 30),
        enabled=bool(row.enabled),
        id=str(row.id),
        owner_id=str(row.owner_id) if row.owner_id else None,
        auth_type=getattr(row, 'auth_type', None) or None,
        catalog_id=getattr(row, 'catalog_id', None) or None,
        status=getattr(row, 'status', None) or None,
        account=getattr(row, 'account', None) or None,
    )


def _owner(value: Any) -> str:
    return str(value or '')


def usable_by(config: MCPServerConfig, owner_key: str | None) -> bool:
    """Shared servers are usable by everyone; owned ones only by their owner."""
    owner = _owner(config.owner_id)
    return not owner or (owner_key is not None and owner == _owner(owner_key))


def matches(config: MCPServerConfig, ref: str) -> bool:
    return bool(ref) and (ref == config.id or ref == config.name)


async def _database_servers() -> list[MCPServerConfig]:
    from cognitrix.models.mcp_server import MCPServer

    try:
        rows = await MCPServer.all() or []
    except Exception:
        logger.exception("Could not read MCP servers from the database")
        return []
    from cognitrix.mcp import composio

    # The row that only holds someone's Composio API key is not a server.
    return [config_from_row(row) for row in rows if not composio.is_account(row)]


def _file_servers() -> list[MCPServerConfig]:
    from cognitrix.mcp.server_manager import mcp_server_manager

    servers = []
    for config in mcp_server_manager.list_servers():
        # mcp.json servers are shared by definition, whatever the file says.
        config.id = None
        config.owner_id = None
        servers.append(config)
    return servers


async def visible_servers(owner_key: str | None) -> list[MCPServerConfig]:
    """Every server this person may use: their own, then shared ones."""
    database = [c for c in await _database_servers() if usable_by(c, owner_key)]
    own = [c for c in database if c.owner_id]
    shared = [c for c in database if not c.owner_id]
    return own + shared + _file_servers()


async def resolve(ref: str, owner_key: str | None) -> MCPServerConfig | None:
    """Find the server ``ref`` (an id or a name) that ``owner_key`` may use.

    An id always wins. For a name, the person's own server comes first, then a
    shared database server, then an ``mcp.json`` server.
    """
    if not ref:
        return None
    candidates = await visible_servers(owner_key)
    for config in candidates:
        if config.id and config.id == ref:
            return config
    for config in candidates:
        if config.name == ref:
            return config
    return None


async def agent_servers(agent: Any, *, enabled_only: bool = True) -> list[MCPServerConfig]:
    """The servers an agent may use right now, from its ``mcp_servers`` list."""
    refs = list(getattr(agent, 'mcp_servers', None) or [])
    if not refs:
        return []
    owner_key = getattr(agent, 'owner_id', None) or None
    servers: list[MCPServerConfig] = []
    seen: set[str] = set()
    for ref in refs:
        config = await resolve(str(ref), owner_key)
        if config is None or config.key in seen:
            continue
        if enabled_only and not config.enabled:
            continue
        seen.add(config.key)
        servers.append(config)
    return servers


# Marks a tool result that means "the person has to sign in to this app".
# The chat shows a Reconnect card for it (frontend SafetyNotices).
SIGN_IN_NEEDED = 'App sign-in needed:'

# Token expiry of each live OAuth connection, so it's reopened with a fresh
# token before the old one stops working.
_live_expiry: dict[str, float | None] = {}


def sign_in_message(name: str, reason: str = 'expired') -> str:
    why = {
        'never': f"{name} isn't signed in yet",
        'setup': f"{name} isn't set up on this server any more",
    }.get(reason, f'the sign-in for {name} expired or was revoked')
    return (
        f'{SIGN_IN_NEEDED} {name}. Right now {why}. Tell the person to open Settings › '
        f'Connected apps and press Connect next to {name}, then try again.'
    )


async def prepare(client: Any, config: MCPServerConfig) -> str | None:
    """Connect to ``config`` if needed. None when ready, else why not (for the agent)."""
    from cognitrix.mcp import composio

    if composio.is_app(config):
        return await composio.prepare(config)  # called over REST: nothing to connect
    if config.auth_type == 'oauth' and config.id:
        return await _prepare_oauth(client, config)
    if client.is_connected(config.key):
        return None
    try:
        config = await _with_saved_secrets(config)
    except ValueError as exc:
        logger.warning("MCP server '%s' can't use its saved credential: %s", config.name, exc)
        return f"'{config.name}' can't use its saved sign-in. Check the sign-in named in its headers under Settings › Agent access."
    try:
        if await client.connect_to_server(config):
            return None
    except Exception:
        logger.exception("Could not connect to MCP server '%s'", config.name)
    return f"Couldn't connect to '{config.name}'. Check the server in Settings › Agent access."


async def _prepare_oauth(client: Any, config: MCPServerConfig) -> str | None:
    import dataclasses
    import time

    import httpx

    from cognitrix.mcp import oauth
    from cognitrix.models.mcp_server import MCPServer

    key = config.key
    expiry = _live_expiry.get(key)
    if client.is_connected(key) and (expiry is None or expiry - time.time() > oauth.REFRESH_MARGIN):
        return None
    row = await MCPServer.get(config.id)
    if row is None:
        return f"'{config.name}' was removed."
    name = oauth.app_name(row)
    try:
        token = await oauth.access_token(row)
    except oauth.ReconnectNeeded as exc:
        return sign_in_message(name, exc.reason)
    except httpx.HTTPError:
        return f"Couldn't reach {name} to renew its sign-in. Try again in a moment."
    if client.is_connected(key):
        await client.disconnect_from_server(key)
    fresh = await MCPServer.get(config.id) or row
    headers = {**(config.headers or {}), 'Authorization': f'Bearer {token}'}
    live = dataclasses.replace(config, headers=headers)
    try:
        connected = bool(await client.connect_to_server(live))
    except Exception:
        logger.exception("Could not connect to %s", name)
        connected = False
    if connected:
        _live_expiry[key] = oauth.expires_at(fresh)
        return None
    _live_expiry.pop(key, None)
    allow_private = bool(oauth.load(fresh).get('allow_private'))
    response = await oauth.probe(str(config.url or ''), token, allow_private=allow_private)
    if response is not None and response.status_code == 401:
        await oauth.mark_needs_reauth(fresh, 'revoked')
        return sign_in_message(name)
    return f"Couldn't connect to {name}. It may be down; try again in a moment."


async def ensure_connected(client: Any, config: MCPServerConfig) -> bool:
    return await prepare(client, config) is None


async def prepare_key(connection_key: str) -> str | None:
    """``prepare`` for a live connection key (a database id, or an mcp.json name)."""
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.models.mcp_server import MCPServer

    client = await get_dynamic_client()
    try:
        row = await MCPServer.get(connection_key)
    except Exception:
        row = None
    if row is None:
        if client.is_connected(connection_key):
            return None
        for config in _file_servers():
            if config.key == connection_key:
                return await prepare(client, config)
        return f"Server '{connection_key}' is not available."
    if not row.enabled:
        # Turned off while an agent still holds its tools.
        return f"'{row.name}' is turned off. Turn it back on in Settings to use it."
    return await prepare(client, config_from_row(row))


async def _with_saved_secrets(config: MCPServerConfig) -> MCPServerConfig:
    """Fill ``{{secret:NAME}}`` header values from the server owner's saved sign-ins.

    The stored config keeps the placeholder; only the live connection gets the
    value, and only when the server's host is one the credential allows.
    """
    import dataclasses

    from cognitrix.credentials import fill_mcp_headers

    if not config.headers:
        return config
    filled = await fill_mcp_headers(config.headers, config.owner_id, config.url)
    if filled is config.headers:
        return config
    return dataclasses.replace(config, headers=filled)
