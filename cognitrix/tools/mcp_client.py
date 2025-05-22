from cognitrix.tools.tool import tool
import asyncio
from typing import Dict, Any, Optional, List
import httpx

# Import MCP SDK
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Example: MCP server URL (should be configurable)
MCP_SERVER_URL = "https://remote.mcpservers.org/sequentialthinking/mcp"  # Change as needed

# --- Direct async functions for CLI/internal use ---
async def mcp_call_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """
    Call a tool on a remote MCP server using the Model Context Protocol.
    Args:
        tool_name (str): The name of the tool to call on the MCP server.
        arguments (dict, optional): Arguments to pass to the tool.
    Returns:
        str: The result from the MCP server tool call.
    """
    arguments = arguments or {}
    try:
        async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    result = await session.call_tool(tool_name, arguments)
                    return str(result.content)
                except Exception as e:
                    return f"MCP tool call failed: {e}"
    except (OSError, httpx.ConnectError) as e:
        return f"Could not connect to MCP server at {MCP_SERVER_URL}: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"

async def mcp_list_tools() -> List[Dict[str, Any]]:
    """
    List available tools from the MCP server.
    Returns:
        List[Dict[str, Any]]: List of tool metadata.
    """
    try:
        async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    tools = await session.list_tools()
                    return [tool.model_dump() for tool in tools.tools]
                except Exception as e:
                    return [{"error": f"MCP list_tools failed: {e}"}]
    except (OSError, httpx.ConnectError) as e:
        return [{"error": f"Could not connect to MCP server at {MCP_SERVER_URL}: {e}"}]
    except Exception as e:
        return [{"error": f"Unexpected error: {e}"}]

async def mcp_list_resources() -> List[Dict[str, Any]]:
    """
    List available resources from the MCP server.
    Returns:
        List[Dict[str, Any]]: List of resource metadata.
    """
    try:
        async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    resources = await session.list_resources()
                    return [resource.model_dump() for resource in resources.resources]
                except Exception as e:
                    return [{"error": f"MCP list_resources failed: {e}"}]
    except (OSError, httpx.ConnectError) as e:
        return [{"error": f"Could not connect to MCP server at {MCP_SERVER_URL}: {e}"}]
    except Exception as e:
        return [{"error": f"Unexpected error: {e}"}]

async def mcp_get_context_window() -> Dict[str, Any]:
    """
    Retrieve context window information from the MCP server (if supported).
    Returns:
        Dict[str, Any]: Context window details (e.g., token usage, limits).
    """
    try:
        async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    # Context window management is not implemented in the MCP SDK
                    return {"info": "Context window management not implemented in this MCP server."}
                except Exception as e:
                    return {"error": f"MCP get_context_window failed: {e}"}
    except (OSError, httpx.ConnectError) as e:
        return {"error": f"Could not connect to MCP server at {MCP_SERVER_URL}: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}

# --- Register as tools for agent use ---
call_mcp_tool = tool(category="mcp")(mcp_call_tool)
list_mcp_tools = tool(category="mcp")(mcp_list_tools)
list_mcp_resources = tool(category="mcp")(mcp_list_resources)
get_mcp_context_window = tool(category="mcp")(mcp_get_context_window) 