"""One way to list and call a connection's tools, whatever is behind it.

A connection an agent uses (an ``MCPServerConfig``) is usually an MCP server.
Two kinds of tools aren't MCP, and callers shouldn't have to care:

- built-in tools added by an option on a connected app, such as "Send email"
  on Gmail (``cognitrix/mcp/native.py``), listed after the server's own;
- every tool of an app connected through Composio (``cognitrix/mcp/composio.py``).

``search`` ranks tools for a task, so an agent with many connected apps can
look up the few it needs instead of being handed every schema.
"""
from __future__ import annotations

import re
from typing import Any

from cognitrix.mcp import composio, native


async def _row(config: Any) -> Any | None:
    if not getattr(config, 'id', None):
        return None
    from cognitrix.models.mcp_server import MCPServer

    try:
        return await MCPServer.get(config.id)
    except Exception:
        return None


def _has_options(config: Any) -> bool:
    from cognitrix.mcp import catalog

    app = catalog.get(getattr(config, 'catalog_id', None))
    return bool(app and app.options and (getattr(config, 'auth_type', None) or '') == 'oauth')


async def list_tools(client: Any, config: Any) -> list[dict[str, Any]]:
    """Every tool of a ready connection: the server's own, then built-in ones."""
    if composio.is_app(config):
        row = await _row(config)
        if row is None:
            return []
        try:
            return await composio.list_tools(row)
        except composio.ComposioError:
            return []
    tools = list(await client.list_tools(config.key) or [])
    if _has_options(config):
        row = await _row(config)
        names = {t.get('name') for t in tools}
        tools.extend(t.info() for t in (native.tools_for(row) if row is not None else []) if t.name not in names)
    return tools


async def call_tool(client: Any, config: Any, tool_name: str, arguments: dict[str, Any]) -> Any:
    """Call a tool of a ready connection. Returns MCP content blocks or text."""
    if composio.is_app(config):
        row = await _row(config)
        if row is None:
            return f"'{config.name}' was removed."
        return await composio.call_tool(row, tool_name, arguments)
    if _has_options(config) and tool_name not in (client.tool_annotations.get(config.key) or {}):
        row = await _row(config)
        tool = native.find(row, tool_name) if row is not None else None
        if tool is not None:
            return await native.call(row, tool, arguments)
    return await client.call_tool(config.key, tool_name, arguments)


async def annotations(client: Any, config: Any, tool_name: str) -> dict[str, Any] | None:
    """A tool's hints (read-only, destructive), or None when it isn't known yet."""
    if composio.is_app(config):
        row = await _row(config)
        try:
            tools = await composio.list_tools(row) if row is not None else []
        except composio.ComposioError:
            tools = []
        return next((t.get('annotations') for t in tools if t.get('name') == tool_name), None)
    known = (client.tool_annotations.get(config.key) or {}).get(tool_name)
    if known is not None or not _has_options(config):
        return known
    row = await _row(config)
    tool = native.find(row, tool_name) if row is not None else None
    return dict(tool.annotations) if tool is not None else None


# --- search -------------------------------------------------------------------

_WORD = re.compile(r'[a-z0-9]+')
_STOP = frozenset({'a', 'an', 'the', 'to', 'of', 'in', 'on', 'for', 'and', 'or', 'my', 'me', 'with', 'from', 'by'})


def _words(text: str) -> list[str]:
    # camelCase and snake_case names split into their words.
    spaced = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(text or ''))
    return [w for w in _WORD.findall(spaced.lower()) if w not in _STOP]


def _stem(word: str) -> str:
    for ending in ('ing', 'es', 's', 'ed'):
        if len(word) > len(ending) + 2 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def score(query: str, server: str, tool: dict[str, Any]) -> float:
    """How well a tool fits a search. 0 means no match."""
    name = str(tool.get('name') or '')
    wanted = {_stem(w) for w in _words(query)}
    if not wanted:
        return 0.0
    if query.strip().lower() in (name.lower(), f'{server}.{name}'.lower()):
        return 100.0
    in_name = {_stem(w) for w in _words(name)}
    in_server = {_stem(w) for w in _words(server)}
    in_text = {_stem(w) for w in _words(str(tool.get('description') or '')[:600])}
    title = str((tool.get('annotations') or {}).get('title') or '')
    in_name |= {_stem(w) for w in _words(title)}
    total = 0.0
    for word in wanted:
        if word in in_name:
            total += 3.0
        elif word in in_server:
            total += 2.0
        elif word in in_text:
            total += 1.0
        elif any(word in other or other in word for other in in_name if len(other) > 3 and len(word) > 3):
            total += 1.5
    return total / len(wanted)


def search(query: str, tools_by_server: dict[str, list[dict[str, Any]]], limit: int = 8) -> list[tuple[str, dict[str, Any]]]:
    """The best matching (server name, tool) pairs for a task, best first."""
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for server, tools in tools_by_server.items():
        for tool in tools:
            points = score(query, server, tool)
            if points > 0:
                ranked.append((points, server, tool))
    ranked.sort(key=lambda item: (-item[0], item[1], str(item[2].get('name') or '')))
    return [(server, tool) for _points, server, tool in ranked[:limit]]
