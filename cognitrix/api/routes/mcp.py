"""MCP servers: add, change, test and remove the servers agents can use.

Who can do what:
- Anyone signed in can add http/sse servers for themselves. On a shared
  server those must use https and a public address, so an agent can't be
  pointed at the server's own network.
- Only an admin can add stdio servers (they run a command on this machine),
  shared servers (everyone can use them), or servers on a private address.
  In the desktop app and single-user installs the person is the admin.
- Servers from the CLI's mcp.json show up as shared and read-only here.

Header and environment values can hold tokens, so responses only list their
names. When saving, a value of "***" keeps the stored value for that name.

Browser sessions only: an API key must not be able to add servers.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents.access import is_admin, owner_key
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.mcp import registry
from cognitrix.mcp.server_manager import MCPServerConfig
from cognitrix.models.mcp_server import MCPServer

mcp_api = APIRouter(prefix='/mcp/servers')

KEEP = '***'
_NAME = re.compile(r'^[A-Za-z0-9_-]{1,40}$')
Transport = Literal['http', 'sse', 'stdio']


class ServerBody(BaseModel):
    name: str
    transport: Transport = 'http'
    description: str = ''
    url: str | None = None
    headers: dict[str, str] = {}
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    timeout: int = 30
    enabled: bool = True
    shared: bool = False


class ServerUpdate(BaseModel):
    name: str | None = None
    transport: Transport | None = None
    description: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    timeout: int | None = None
    enabled: bool | None = None


def _desktop() -> bool:
    return os.environ.get('COGNITRIX_DESKTOP') == '1'


async def _can_edit(config: MCPServerConfig, ctx: AuthContext, admin: bool) -> bool:
    if not config.id:
        return False  # mcp.json servers are managed from the CLI
    if config.owner_id:
        return config.owner_id == owner_key(ctx.user)
    return admin


def _project(config: MCPServerConfig, *, can_edit: bool, connected: bool) -> dict[str, Any]:
    return {
        'id': config.id,
        'ref': config.key,
        'name': config.name,
        'description': config.description,
        'transport': config.transport.value,
        # Addresses and commands can carry tokens; only editors see them.
        'url': config.url if can_edit else None,
        'command': config.command if can_edit else None,
        'args': list(config.args or []) if can_edit else [],
        'header_names': sorted((config.headers or {}).keys()) if can_edit else [],
        'env_names': sorted((config.env or {}).keys()) if can_edit else [],
        'timeout': config.timeout,
        'enabled': config.enabled,
        'shared': not config.owner_id,
        'source': 'database' if config.id else 'config_file',
        'can_edit': can_edit,
        'connected': connected,
    }


async def _resolve_host(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0].split('%')[0]) for info in infos]


async def _check_url(url: str | None, *, allow_private: bool) -> str:
    url = (url or '').strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Enter the server's full address, starting with https://")
    if allow_private:
        return url
    if parsed.scheme != 'https':
        raise HTTPException(status_code=400, detail="Use an https:// address")
    try:
        addresses = await _resolve_host(parsed.hostname, parsed.port or 443)
    except (OSError, UnicodeError):
        raise HTTPException(status_code=400, detail=f"Couldn't find {parsed.hostname}")
    for address in addresses:
        if not address.is_global:
            raise HTTPException(
                status_code=400,
                detail="That address is on a private network. Only an admin can add servers on private addresses.",
            )
    return url


def _sealed(values: dict[str, str]) -> str:
    """Header and env values are encrypted at rest."""
    from cognitrix.common.secret_box import seal

    return seal(json.dumps(values)) if values else ''


def _merge_secrets(new: dict[str, str], old: dict[str, str] | None) -> dict[str, str]:
    old = old or {}
    merged: dict[str, str] = {}
    for key, value in new.items():
        key = str(key).strip()
        if not key:
            continue
        if value == KEEP:
            if key in old:
                merged[key] = old[key]
            continue
        merged[key] = str(value)
    return merged


async def _validate(
    *, name: str, transport: str, url: str | None, command: str | None,
    owner: str | None, admin: bool, exclude_key: str | None = None,
) -> tuple[str, str | None]:
    name = name.strip()
    if not _NAME.match(name):
        raise HTTPException(
            status_code=400,
            detail="Use 1 to 40 letters, numbers, dashes or underscores for the name",
        )
    for other in await registry.visible_servers(owner):
        if other.name == name and other.key != exclude_key:
            raise HTTPException(status_code=409, detail=f"You already have a server called {name}")
    if transport == 'stdio':
        if not admin:
            raise HTTPException(
                status_code=403,
                detail="Only an admin can add servers that run a command on this machine",
            )
        if not (command or '').strip():
            raise HTTPException(status_code=400, detail="A command is required for a stdio server")
        return name, None
    return name, await _check_url(url, allow_private=admin or _desktop())


async def _find(ref: str, ctx: AuthContext) -> MCPServerConfig:
    config = await registry.resolve(ref, owner_key(ctx.user))
    if config is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return config


async def _disconnect(key: str) -> None:
    from cognitrix.mcp.client import get_dynamic_client

    client = await get_dynamic_client()
    if client.is_connected(key):
        await client.disconnect_from_server(key)


@mcp_api.get('')
async def list_servers(ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.mcp.client import get_dynamic_client

    admin = await is_admin(ctx.user)
    client = await get_dynamic_client()
    servers = await registry.visible_servers(owner_key(ctx.user))
    return {
        'servers': [
            _project(c, can_edit=await _can_edit(c, ctx, admin), connected=client.is_connected(c.key))
            for c in servers
        ],
        'can_add_local': admin,
        'can_add_shared': admin,
    }


@mcp_api.post('')
async def add_server(body: ServerBody, ctx: AuthContext = Depends(jwt_only)):
    admin = await is_admin(ctx.user)
    if body.shared and not admin:
        raise HTTPException(status_code=403, detail="Only an admin can add a server for everyone")
    owner = None if body.shared else owner_key(ctx.user)
    name, url = await _validate(
        name=body.name, transport=body.transport, url=body.url,
        command=body.command, owner=owner, admin=admin,
    )
    stdio = body.transport == 'stdio'
    row = MCPServer(
        name=name,
        owner_id=owner,
        transport=body.transport,
        description=body.description.strip(),
        url=url,
        command=body.command.strip() if stdio and body.command else None,
        args=[str(a) for a in body.args] if stdio else [],
        env=_sealed(_merge_secrets(body.env, None)) if stdio else '',
        headers='' if stdio else _sealed(_merge_secrets(body.headers, None)),
        timeout=max(5, min(int(body.timeout or 30), 300)),
        enabled=body.enabled,
    )
    await row.save()
    config = registry.config_from_row(row)
    return _project(config, can_edit=True, connected=False)


@mcp_api.patch('/{server_id}')
async def update_server(server_id: str, body: ServerUpdate, ctx: AuthContext = Depends(jwt_only)):
    admin = await is_admin(ctx.user)
    config = await _find(server_id, ctx)
    if not await _can_edit(config, ctx, admin):
        raise HTTPException(status_code=403, detail="You can't change this server")

    transport = body.transport or config.transport.value
    if transport == 'stdio' and not admin:
        raise HTTPException(status_code=403, detail="Only an admin can change servers that run a command on this machine")
    name, url = await _validate(
        name=body.name if body.name is not None else config.name,
        transport=transport,
        url=body.url if body.url is not None else config.url,
        command=body.command if body.command is not None else config.command,
        owner=config.owner_id, admin=admin, exclude_key=config.key,
    )
    stdio = transport == 'stdio'
    changes: dict[str, Any] = {'name': name, 'transport': transport, 'url': url}
    if body.description is not None:
        changes['description'] = body.description.strip()
    if body.timeout is not None:
        changes['timeout'] = max(5, min(int(body.timeout), 300))
    if body.enabled is not None:
        changes['enabled'] = body.enabled
    if stdio:
        if body.command is not None:
            changes['command'] = body.command.strip()
        if body.args is not None:
            changes['args'] = [str(a) for a in body.args]
        if body.env is not None:
            changes['env'] = _sealed(_merge_secrets(body.env, config.env))
        changes['headers'] = ''
    else:
        changes['command'] = None
        changes['args'] = []
        changes['env'] = ''
        if body.headers is not None:
            changes['headers'] = _sealed(_merge_secrets(body.headers, config.headers))

    await MCPServer.update_one({'id': config.id}, changes)
    await _disconnect(config.key)
    row = await MCPServer.get(config.id)
    return _project(registry.config_from_row(row), can_edit=True, connected=False)


@mcp_api.delete('/{server_id}')
async def delete_server(server_id: str, ctx: AuthContext = Depends(jwt_only)):
    admin = await is_admin(ctx.user)
    config = await _find(server_id, ctx)
    if not await _can_edit(config, ctx, admin):
        raise HTTPException(status_code=403, detail="You can't remove this server")
    await _disconnect(config.key)
    # delete_many: odbms' sqlite delete_one emits DELETE ... LIMIT, which
    # SQLite rejects. An id filter still deletes one row.
    await MCPServer.delete_many({'id': config.id})
    return {'deleted': True}


@mcp_api.post('/{server_ref}/test')
async def test_server(server_ref: str, ctx: AuthContext = Depends(jwt_only)):
    """Connect (or reconnect) and list the server's tools."""
    from cognitrix.mcp.client import get_dynamic_client

    admin = await is_admin(ctx.user)
    config = await _find(server_ref, ctx)
    if not config.enabled:
        return {'connected': False, 'tools': [], 'error': "This server is turned off"}
    if config.owner_id and config.transport.value != 'stdio':
        # Addresses can start resolving somewhere else after they were saved.
        await _check_url(config.url, allow_private=admin or _desktop())

    client = await get_dynamic_client()
    await _disconnect(config.key)
    if not await registry.ensure_connected(client, config):
        return {
            'connected': False,
            'tools': [],
            'error': "Couldn't connect. Check the address, headers or command.",
        }
    from cognitrix.safety.destructive_ops import RiskLevel, mcp_tool_risk

    tools = await client.list_tools(config.key) or []
    return {
        'connected': True,
        'tools': [
            {
                'name': t.get('name'),
                'description': t.get('description') or '',
                'read_only': bool((t.get('annotations') or {}).get('readOnlyHint')),
                'asks_first': mcp_tool_risk(str(t.get('name') or ''), t.get('annotations')).risk_level
                != RiskLevel.LOW,
            }
            for t in tools
        ],
        'error': None,
    }
