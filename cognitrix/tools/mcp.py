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


@tool(category="mcp")
async def list_mcp_tools(*, parent: "Agent") -> str:
    """
    Lists available tools from permitted MCP servers, providing a dictionary of servers and their tools.

    Args:
        parent: The agent calling the tool.

    Returns:
        A JSON string representing a dictionary where keys are server names
        and values are lists of tool definitions for that server.
    """
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.mcp.registry import ensure_connected

    if not parent.mcp_servers:
        return "This agent does not have permission to access any MCP servers."
    servers = await _permitted(parent)
    if not servers:
        return "None of this agent's MCP servers are available."

    client = await get_dynamic_client()
    all_tools: dict[str, list[dict[str, Any]]] = {}
    for config in servers:
        if not await ensure_connected(client, config):
            continue
        tools = await client.list_tools(config.key)
        if tools:
            all_tools[config.name] = [
                {
                    'name': t.get('name'),
                    'description': t.get('description'),
                    'input_schema': t.get('input_schema') or {},
                    'read_only': bool((t.get('annotations') or {}).get('readOnlyHint')),
                }
                for t in tools
            ]

    if not all_tools:
        return "No permitted MCP servers are currently connected, or they have no tools."

    return json.dumps(all_tools, indent=2)

@tool(category="mcp")
async def run_mcp_tool(server: str, tool_name: str, arguments: dict[str, Any], *, parent: "Agent") -> Any:
    """
    Runs a specific tool on a specific MCP server with the given arguments. You must have permission to access the server.

    Args:
        server: The name of the MCP server.
        tool_name: The name of the tool to run.
        arguments: A dictionary of arguments for the tool.
        parent: The agent calling this tool.

    Returns:
        The result of the tool execution.
    """
    from cognitrix.mcp.client import get_dynamic_client
    from cognitrix.mcp.registry import ensure_connected, matches

    config = next((c for c in await _permitted(parent) if matches(c, server)), None)
    if config is None:
        return f"Error: Agent '{parent.name}' does not have permission to access MCP server '{server}'."

    client = await get_dynamic_client()
    if not await ensure_connected(client, config):
        return f"Error: MCP server '{server}' is not available."
    result = await client.call_tool(config.key, tool_name, arguments)
    if result is None:
        return f"Error: MCP tool '{tool_name}' on '{server}' failed."
    return _content_to_text(result)

__all__ = [
    'list_mcp_tools',
    'run_mcp_tool'
]
