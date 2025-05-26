"""
MCP server management functions.
Handles server configuration, connection, and disconnection operations.
"""

import logging
from typing import Dict, Any, List

from cognitrix.mcp.server_manager import mcp_server_manager, MCPServerConfig, MCPTransportType
from cognitrix.mcp.client import get_dynamic_client
from cognitrix.mcp.status import get_all_connection_status

logger = logging.getLogger('cognitrix.log')

async def mcp_add_server(name: str, transport: str, **kwargs) -> str:
    """
    Add a new MCP server configuration.
    Args:
        name (str): Server name
        transport (str): Transport type (stdio, http, sse)
        **kwargs: Additional configuration based on transport type
    Returns:
        str: Success or error message
    """
    try:
        transport_enum = MCPTransportType(transport.lower())
        
        # Create server config based on transport type
        if transport_enum == MCPTransportType.STDIO:
            server_config = MCPServerConfig(
                name=name,
                transport=transport_enum,
                command=kwargs.get('command'),
                args=kwargs.get('args', []),
                env=kwargs.get('env'),
                working_directory=kwargs.get('working_directory'),
                description=kwargs.get('description', '')
            )
        elif transport_enum in [MCPTransportType.HTTP, MCPTransportType.SSE]:
            server_config = MCPServerConfig(
                name=name,
                transport=transport_enum,
                url=kwargs.get('url'),
                headers=kwargs.get('headers'),
                timeout=kwargs.get('timeout', 30),
                description=kwargs.get('description', '')
            )
        else:
            return f"Unsupported transport type: {transport}"
        
        success = mcp_server_manager.add_server(server_config)
        if success:
            return f"Successfully added MCP server '{name}' with {transport} transport"
        else:
            return f"Failed to add MCP server '{name}'"
            
    except ValueError as e:
        return f"Invalid transport type '{transport}'. Must be one of: stdio, http, sse"
    except Exception as e:
        return f"Error adding MCP server: {e}"

async def mcp_remove_server(name: str) -> str:
    """
    Remove an MCP server configuration.
    Args:
        name (str): Server name to remove
    Returns:
        str: Success or error message
    """
    try:
        # Disconnect if currently connected
        client = await get_dynamic_client()
        if client.is_connected(name):
            await client.disconnect_from_server(name)
        
        success = mcp_server_manager.remove_server(name)
        if success:
            return f"Successfully removed MCP server '{name}'"
        else:
            return f"MCP server '{name}' not found"
    except Exception as e:
        return f"Error removing MCP server: {e}"

async def mcp_list_servers() -> List[Dict[str, Any]]:
    """
    List all configured MCP servers.
    Returns:
        List[Dict[str, Any]]: List of server configurations
    """
    try:
        servers = mcp_server_manager.list_servers()
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        connection_statuses = get_all_connection_status()
        
        result = []
        for server in servers:
            server_info = server.to_dict()
            # Check both active connection and persistent status
            is_connected = server.name in connected_servers
            persistent_status = connection_statuses.get(server.name, {}).get('connected', False)
            
            server_info['connected'] = is_connected or persistent_status
            server_info['active_session'] = is_connected
            server_info['persistent_status'] = persistent_status
            
            result.append(server_info)
        
        return result
    except Exception as e:
        return [{"error": f"Error listing MCP servers: {e}"}]

async def mcp_connect_server(name: str) -> dict:
    """
    Connect to a configured MCP server.
    Args:
        name (str): Server name to connect to
    Returns:
        dict: {"success": bool, "message": str} or {"success": False, "error": str}
    """
    try:
        server_config = mcp_server_manager.get_server(name)
        if not server_config:
            return {"success": False, "error": f"MCP server '{name}' not found in configuration"}
        if not server_config.enabled:
            return {"success": False, "error": f"MCP server '{name}' is disabled"}
        client = await get_dynamic_client()
        success = await client.connect_to_server(server_config)
        if success:
            return {"success": True, "message": f"Successfully connected to MCP server '{name}'"}
        else:
            return {"success": False, "error": f"Failed to connect to MCP server '{name}'"}
    except Exception as e:
        return {"success": False, "error": f"Error connecting to MCP server: {e}"}

async def mcp_disconnect_server(name: str) -> dict:
    """
    Disconnect from an MCP server.
    Args:
        name (str): Server name to disconnect from
    Returns:
        dict: {"success": bool, "message": str} or {"success": False, "error": str}
    """
    try:
        client = await get_dynamic_client()
        success = await client.disconnect_from_server(name)
        if success:
            return {"success": True, "message": f"Successfully disconnected from MCP server '{name}'"}
        else:
            return {"success": False, "error": f"Failed to disconnect from MCP server '{name}'"}
    except Exception as e:
        return {"success": False, "error": f"Error disconnecting from MCP server: {e}"}

async def mcp_disconnect_all() -> str:
    """
    Disconnect from all MCP servers.
    Returns:
        str: Success message
    """
    try:
        client = await get_dynamic_client()
        await client.disconnect_all()
        return "Disconnected from all MCP servers"
    except Exception as e:
        return f"Error disconnecting from all servers: {e}"

async def mcp_get_connection_info() -> Dict[str, Any]:
    """
    Get detailed connection information for all servers.
    Returns:
        Dict[str, Any]: Connection information
    """
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        connection_statuses = get_all_connection_status()
        
        return {
            "active_connections": connected_servers,
            "persistent_statuses": connection_statuses,
            "total_configured": len(mcp_server_manager.list_servers()),
            "total_connected": len(connected_servers)
        }
    except Exception as e:
        return {"error": f"Error getting connection info: {e}"} 