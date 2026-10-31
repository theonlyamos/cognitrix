import json
from typing import TYPE_CHECKING, Any

from cognitrix.tools.tool import tool

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent

# get_dynamic_client is imported lazily inside the tool functions: this module
# is loaded by the tools-package reflection at startup, and eagerly importing
# the MCP SDK here added ~0.2s+ to every launch.

async def _connect_permitted(client, servers: list[str]) -> None:
    """Connect the agent's permitted servers that aren't connected yet.

    Chat turns never connected them, so List_Mcp_Tools always answered
    "No permitted MCP servers are currently connected".
    """
    from cognitrix.mcp.server_manager import mcp_server_manager

    for server in servers:
        if client.is_connected(server):
            continue
        config = mcp_server_manager.get_server(server)
        if config is None or not config.enabled:
            continue
        try:
            await client.connect_to_server(config)
        except Exception:
            continue


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
    client = await get_dynamic_client()

    permitted_servers = parent.mcp_servers
    if not permitted_servers:
        return "This agent does not have permission to access any MCP servers."

    await _connect_permitted(client, list(permitted_servers))
    all_connected_servers = client.get_connected_servers()

    target_servers = [s for s in all_connected_servers if s in permitted_servers]
    if not target_servers:
        return "No permitted MCP servers are currently connected."

    all_tools: dict[str, list[dict[str, Any]]] = {}

    for server in target_servers:
        tools = await client.list_tools(server)
        if tools:
            all_tools[server] = tools

    if not all_tools:
        return "No tools found on permitted MCP servers."

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
    permitted_servers = parent.mcp_servers
    if server not in permitted_servers:
        return f"Error: Agent '{parent.name}' does not have permission to access MCP server '{server}'."

    from cognitrix.mcp.client import get_dynamic_client
    client = await get_dynamic_client()
    await _connect_permitted(client, [server])
    if not client.is_connected(server):
        return f"Error: MCP server '{server}' is not available."
    result = await client.call_tool(server, tool_name, arguments)
    if result is None:
        return f"Error: MCP tool '{tool_name}' on '{server}' failed."
    return _content_to_text(result)

__all__ = [
    'list_mcp_tools',
    'run_mcp_tool'
]
