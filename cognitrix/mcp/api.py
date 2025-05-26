"""
Enhanced API functions for MCP tool operations and resource management.
Provides high-level functions for calling tools, listing resources, and context management.
"""

import logging
from typing import Dict, Any, Optional, List

from cognitrix.mcp.client import get_dynamic_client

logger = logging.getLogger('cognitrix.log')

async def mcp_call_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None, server_name: Optional[str] = None) -> str:
    """
    Call a tool on an MCP server.
    Args:
        tool_name (str): The name of the tool to call
        arguments (dict, optional): Arguments to pass to the tool
        server_name (str, optional): Specific server to use, if None uses first available
    Returns:
        str: The result from the MCP server tool call
    """
    arguments = arguments or {}
    
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        if not connected_servers:
            return "No MCP servers connected. Please add and connect to MCP servers first."
        
        # Use specified server or first available
        target_server = server_name if server_name and server_name in connected_servers else connected_servers[0]
        
        result = await client.call_tool(target_server, tool_name, arguments)
        if result:
            return str(result)
        else:
            return f"Tool call failed or returned no result"
            
    except Exception as e:
        return f"MCP tool call failed: {e}"

async def mcp_list_tools(server_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List available tools from MCP servers.
    Args:
        server_name (str, optional): Specific server to query, if None queries all connected servers
    Returns:
        List[Dict[str, Any]]: List of tool metadata
    """
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        if not connected_servers:
            return [{"error": "No MCP servers connected. Please add and connect to MCP servers first."}]
        
        if server_name:
            if server_name not in connected_servers:
                return [{"error": f"Server '{server_name}' not connected"}]
            tools = await client.list_tools(server_name)
            return tools or [{"error": f"Failed to list tools from server '{server_name}'"}]
        else:
            # Query all connected servers
            all_tools = []
            for server in connected_servers:
                tools = await client.list_tools(server)
                if tools:
                    for tool in tools:
                        tool['server'] = server
                    all_tools.extend(tools)
            return all_tools
            
    except Exception as e:
        return [{"error": f"MCP list_tools failed: {e}"}]

async def mcp_list_resources(server_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List available resources from MCP servers.
    Args:
        server_name (str, optional): Specific server to query, if None queries all connected servers
    Returns:
        List[Dict[str, Any]]: List of resource metadata
    """
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        if not connected_servers:
            return [{"error": "No MCP servers connected. Please add and connect to MCP servers first."}]
        
        if server_name:
            if server_name not in connected_servers:
                return [{"error": f"Server '{server_name}' not connected"}]
            resources = await client.list_resources(server_name)
            return resources or [{"error": f"Failed to list resources from server '{server_name}'"}]
        else:
            # Query all connected servers
            all_resources = []
            for server in connected_servers:
                resources = await client.list_resources(server)
                if resources:
                    for resource in resources:
                        resource['server'] = server
                    all_resources.extend(resources)
            return all_resources
            
    except Exception as e:
        return [{"error": f"MCP list_resources failed: {e}"}]

async def mcp_get_context_window() -> Dict[str, Any]:
    """
    Retrieve context window information from connected MCP servers.
    Returns:
        Dict[str, Any]: Context window details
    """
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        if not connected_servers:
            return {"info": "No MCP servers connected"}
        
        context_info = {
            "connected_servers": connected_servers,
            "info": "Context window management varies by server implementation"
        }
        return context_info
        
    except Exception as e:
        return {"error": f"MCP get_context_window failed: {e}"}

async def mcp_get_server_info(server_name: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific MCP server.
    Args:
        server_name (str): Name of the server to query
    Returns:
        Dict[str, Any]: Server information including tools and resources
    """
    try:
        client = await get_dynamic_client()
        
        if not client.is_connected(server_name):
            return {"error": f"Server '{server_name}' is not connected"}
        
        # Get tools and resources
        tools = await client.list_tools(server_name) or []
        resources = await client.list_resources(server_name) or []
        
        return {
            "server_name": server_name,
            "connected": True,
            "tools": tools,
            "resources": resources,
            "tool_count": len(tools),
            "resource_count": len(resources)
        }
        
    except Exception as e:
        return {"error": f"Error getting server info: {e}"}

async def mcp_health_check() -> Dict[str, Any]:
    """
    Perform health check on all connected MCP servers.
    Returns:
        Dict[str, Any]: Health status of all servers
    """
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        health_status = {
            "overall_status": "healthy",
            "total_servers": len(connected_servers),
            "servers": {}
        }
        
        unhealthy_count = 0
        
        for server_name in connected_servers:
            try:
                # Try to list tools as a health check
                tools = await client.list_tools(server_name)
                if tools is not None:
                    health_status["servers"][server_name] = {
                        "status": "healthy",
                        "tool_count": len(tools)
                    }
                else:
                    health_status["servers"][server_name] = {
                        "status": "unhealthy",
                        "error": "Failed to list tools"
                    }
                    unhealthy_count += 1
            except Exception as e:
                health_status["servers"][server_name] = {
                    "status": "unhealthy",
                    "error": str(e)
                }
                unhealthy_count += 1
        
        if unhealthy_count > 0:
            health_status["overall_status"] = "degraded" if unhealthy_count < len(connected_servers) else "unhealthy"
        
        health_status["healthy_count"] = len(connected_servers) - unhealthy_count
        health_status["unhealthy_count"] = unhealthy_count
        
        return health_status
        
    except Exception as e:
        return {
            "overall_status": "error",
            "error": f"Health check failed: {e}"
        } 