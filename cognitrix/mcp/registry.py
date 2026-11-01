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
    return [config_from_row(row) for row in rows]


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


async def ensure_connected(client: Any, config: MCPServerConfig) -> bool:
    if client.is_connected(config.key):
        return True
    try:
        return bool(await client.connect_to_server(config))
    except Exception:
        logger.exception("Could not connect to MCP server '%s'", config.name)
        return False
