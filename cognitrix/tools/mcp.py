import json
from typing import TYPE_CHECKING, Any

from cognitrix.tools.tool import tool

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent

# get_dynamic_client is imported lazily inside the tool functions: this module
# is loaded by the tools-package reflection at startup, and eagerly importing
# the MCP SDK here added ~0.2s+ to every launch.

async def _permitted(parent: "Agent") -> list:
    """The agent's servers it may use: shared ones or its owner's own."""
    from cognitrix.mcp.registry import agent_servers

    return await agent_servers(parent)


def _content_to_text(result: Any) -> Any:
    """MCP returns a list of content blocks; give the model their text."""
    if isinstance(result, list):
        parts = []
        for block in result:
            text = getattr(block, 'text', None)
            if text is not None:
                parts.append(text)
            elif hasattr(block, 'model_dump'):
                parts.append(json.dumps(block.model_dump(mode='json')))
            else:
                parts.append(str(block))
        return '\n'.join(parts)
    return result


# With more tools than this, a plain listing shows names only and the agent
# looks up the few it needs with ``query``.
INDEX_ABOVE = 25
SEARCH_RESULTS = 8


def _visible(parent: "Agent", server: str, tool: dict[str, Any]) -> bool:
    """Tools the person switched off for this agent aren't offered at all."""
    from cognitrix.mcp.tools import wrapper_name
    from cognitrix.safety.permissions import is_denied

    return not is_denied(parent, wrapper_name(server, str(tool.get('name') or '')))


def _full(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        'name': tool.get('name'),
        'description': tool.get('description'),
        'input_schema': tool.get('input_schema') or {},
        'read_only': bool((tool.get('annotations') or {}).get('readOnlyHint')),
    }


def _brief(tool: dict[str, Any]) -> dict[str, Any]:
    text = ' '.join(str(tool.get('description') or '').split())
    return {'name': tool.get('name'), 'description': text[:117] + '...' if len(text) > 120 else text}


@tool(category="mcp")
async def list_mcp_tools(query: str = '', server: str = '', *, parent: "Agent") -> str:
    """
    Finds tools in this agent's connected apps and MCP servers (Gmail, Slack, GitHub and so on).

    Pass a query to search: describe the task ("send an email", "find a channel") or give exact
    tool names separated by commas. The best matches come back with their parameters, ready for
    Run Mcp Tool. Without a query you get every server's tools; when there are many, only names
    and one-line descriptions, so search for the ones you need before calling them.

    Args:
        query: What you want to do, or exact tool names separated by commas. Optional.
        server: Only look in this server. Optional.
        parent: The agent calling the tool.

    Returns:
        A JSON string: servers and their tools.
    """
    from cognitrix.mcp import apps
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.mcp.registry import SIGN_IN_NEEDED, matches, prepare

    if not parent.mcp_servers:
        return "This agent does not have permission to access any MCP servers."
    servers = await _permitted(parent)
    if server:
        servers = [c for c in servers if matches(c, server)]
    if not servers:
        return "None of this agent's MCP servers are available."

    client = await get_dynamic_client()
    all_tools: dict[str, list[dict[str, Any]]] = {}
    problems: dict[str, str] = {}
    for config in servers:
        problem = await prepare(client, config)
        if problem:
            if problem.startswith(SIGN_IN_NEEDED):
                problems[config.name] = problem
            continue
        tools = [t for t in await apps.list_tools(client, config) if _visible(parent, config.name, t)]
        if tools:
            all_tools[config.name] = tools

    if not all_tools:
        if problems:
            return '\n'.join(problems.values())
        return "No permitted MCP servers are currently connected, or they have no tools."

    query = (query or '').strip()
    result: dict[str, Any]
    if query:
        exact = {name.strip().lower() for name in query.split(',') if name.strip()}
        named = [
            (name, t) for name, tools in all_tools.items() for t in tools
            if str(t.get('name') or '').lower() in exact
        ]
        found = named if named and len(named) >= len(exact) else apps.search(query, all_tools, SEARCH_RESULTS)
        if not found:
            return (
                f"No tool matches '{query}'. Try other words, or call this without a query to see "
                "every tool's name."
            )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for name, info in found:
            grouped.setdefault(name, []).append(_full(info))
        result = {'matches': grouped}
    elif sum(len(tools) for tools in all_tools.values()) > INDEX_ABOVE:
        result = {
            'servers': {name: [_brief(t) for t in tools] for name, tools in all_tools.items()},
            'note': (
                'Names only. Before calling one, get its parameters: call this again with '
                'query set to the task or to the exact tool names.'
            ),
        }
    else:
        result = {name: [_full(t) for t in tools] for name, tools in all_tools.items()}
    if problems:
        result = {**result, 'needs_sign_in': problems}
    return json.dumps(result, indent=2)

@tool(category="mcp")
async def run_mcp_tool(server: str, tool_name: str, arguments: dict[str, Any], *, parent: "Agent") -> Any:
    """
    Runs a specific tool on one of this agent's connected apps or MCP servers with the given arguments.
    Find the tool's name and parameters with List Mcp Tools first.

    Args:
        server: The name of the MCP server.
        tool_name: The name of the tool to run.
        arguments: A dictionary of arguments for the tool.
        parent: The agent calling this tool.

    Returns:
        The result of the tool execution.
    """
    from cognitrix.mcp import apps
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.mcp.registry import matches, prepare
    from cognitrix.mcp.tools import wrapper_name
    from cognitrix.safety.permissions import is_denied

    config = next((c for c in await _permitted(parent) if matches(c, server)), None)
    if config is None:
        return f"Error: Agent '{parent.name}' does not have permission to access MCP server '{server}'."
    if is_denied(parent, wrapper_name(config.name, tool_name)):
        return f"Error: '{tool_name}' on '{server}' is switched off for this agent."

    client = await get_dynamic_client()
    problem = await prepare(client, config)
    if problem:
        return problem
    result = await apps.call_tool(client, config, tool_name, arguments if isinstance(arguments, dict) else {})
    if result is None:
        return f"Error: MCP tool '{tool_name}' on '{server}' failed."
    return _content_to_text(result)

__all__ = [
    'list_mcp_tools',
    'run_mcp_tool'
]
