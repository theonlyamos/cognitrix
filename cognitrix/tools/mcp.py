import json
from typing import Any, Dict, List, TYPE_CHECKING
from cognitrix.tools.tool import tool
from cognitrix.mcp.client import get_dynamic_client

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent

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
    client = await get_dynamic_client()
    
    permitted_servers = parent.mcp_servers
    if not permitted_servers:
        return "This agent does not have permission to access any MCP servers."

    all_connected_servers = client.get_connected_servers()
    
    target_servers = [s for s in all_connected_servers if s in permitted_servers]
    if not target_servers:
        return "No permitted MCP servers are currently connected."

    all_tools: Dict[str, List[Dict[str, Any]]] = {}

    for server in target_servers:
        tools = await client.list_tools(server)
        if tools:
            all_tools[server] = tools

    if not all_tools:
        return "No tools found on permitted MCP servers."

    return json.dumps(all_tools, indent=2)

@tool(category="mcp")
async def run_mcp_tool(server: str, tool_name: str, arguments: Dict[str, Any], *, parent: "Agent") -> Any:
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

    client = await get_dynamic_client()
    result = await client.call_tool(server, tool_name, arguments)
    return result

__all__ = [
    'list_mcp_tools',
    'run_mcp_tool'
] 