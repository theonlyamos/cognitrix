"""
Cognitrix MCP Package - Modular MCP client and server management.

This package provides a clean, modular interface for MCP (Model Context Protocol)
server management, tool registration, and dynamic client connections.

Modules:
- status: Connection status tracking and persistence
- client: DynamicMCPClient for server connections
- tools: Tool wrapper creation and agent integration
- manager: Server configuration and lifecycle management
- api: High-level API functions for tool and resource operations
"""

# Core client functionality
from .client import DynamicMCPClient, get_dynamic_client

# Status tracking
from .status import (
    update_connection_status,
    get_connection_status,
    get_all_connection_status,
    cleanup_stale_connections,
    clear_connection_status
)

# Tool management
from .tools import (
    create_mcp_tool_wrapper,
    sync_mcp_tools_for_agent,
    refresh_agent_mcp_tools,
    get_dynamic_mcp_tools,
    clear_dynamic_mcp_tools,
    remove_server_tools
)

# Server management
from .manager import (
    mcp_add_server,
    mcp_remove_server,
    mcp_list_servers,
    mcp_connect_server,
    mcp_disconnect_server,
    mcp_disconnect_all,
    mcp_get_connection_info
)

# API functions
from .api import (
    mcp_call_tool,
    mcp_list_tools,
    mcp_list_resources,
    mcp_get_context_window,
    mcp_get_server_info,
    mcp_health_check
)

# Tool decorators and registration
from cognitrix.tools.tool import tool

# Register enhanced MCP tools
@tool(category="mcp")
async def refresh_mcp_tools() -> str:
    """
    Refresh MCP tools by syncing with all connected servers.
    This will discover new tools from connected MCP servers.
    """
    try:
        from cognitrix.agents import Agent
        from cognitrix.mcp.tools import sync_mcp_tools_for_agent
        
        # Get all agents and sync tools for each
        agents = await Agent.list_agents()
        total_tools = 0
        
        for agent in agents:
            new_tools = await sync_mcp_tools_for_agent(agent)
            total_tools += len(new_tools)
        
        return f"Refreshed {total_tools} MCP tools across {len(agents)} agents"
        
    except Exception as e:
        return f"Error refreshing MCP tools: {e}"

@tool(category="mcp")
async def mcp_status() -> str:
    """
    Get comprehensive status of all MCP servers and connections.
    Shows both active connections and persistent status.
    """
    try:
        connection_info = await mcp_get_connection_info()
        health_status = await mcp_health_check()
        
        if "error" in connection_info:
            return f"Error getting connection info: {connection_info['error']}"
        
        status_report = []
        status_report.append(f"=== MCP Connection Status ===")
        status_report.append(f"Total configured servers: {connection_info['total_configured']}")
        status_report.append(f"Active connections: {connection_info['total_connected']}")
        
        if health_status.get("overall_status") != "error":
            status_report.append(f"Health status: {health_status['overall_status']}")
            status_report.append(f"Healthy servers: {health_status.get('healthy_count', 0)}")
            status_report.append(f"Unhealthy servers: {health_status.get('unhealthy_count', 0)}")
        
        # Show active connections
        if connection_info['active_connections']:
            status_report.append(f"\nActive connections:")
            for server in connection_info['active_connections']:
                status_report.append(f"  - {server}")
        
        # Show persistent statuses
        persistent = connection_info.get('persistent_statuses', {})
        if persistent:
            status_report.append(f"\nPersistent status:")
            for server, status in persistent.items():
                connected = status.get('connected', False)
                status_report.append(f"  - {server}: {'connected' if connected else 'disconnected'}")
        
        return "\n".join(status_report)
        
    except Exception as e:
        return f"Error getting MCP status: {e}"

# Register tool registrations
call_mcp_tool = tool(category="mcp")(mcp_call_tool)
list_mcp_tools = tool(category="mcp")(mcp_list_tools)
list_mcp_resources = tool(category="mcp")(mcp_list_resources)
get_mcp_context_window = tool(category="mcp")(mcp_get_context_window)

add_mcp_server = tool(category="mcp")(mcp_add_server)
remove_mcp_server = tool(category="mcp")(mcp_remove_server)
list_mcp_servers = tool(category="mcp")(mcp_list_servers)
connect_mcp_server = tool(category="mcp")(mcp_connect_server)
disconnect_mcp_server = tool(category="mcp")(mcp_disconnect_server)
disconnect_all_mcp_servers = tool(category="mcp")(mcp_disconnect_all)

get_mcp_server_info = tool(category="mcp")(mcp_get_server_info)
check_mcp_health = tool(category="mcp")(mcp_health_check)

__all__ = [
    # Core classes
    'DynamicMCPClient',
    'get_dynamic_client',
    
    # Status functions
    'update_connection_status',
    'get_connection_status',
    'get_all_connection_status',
    'cleanup_stale_connections',
    'clear_connection_status',
    
    # Tool functions
    'create_mcp_tool_wrapper',
    'sync_mcp_tools_for_agent',
    'refresh_agent_mcp_tools',
    'get_dynamic_mcp_tools',
    'clear_dynamic_mcp_tools',
    'remove_server_tools',
    
    # Manager functions
    'mcp_add_server',
    'mcp_remove_server',
    'mcp_list_servers',
    'mcp_connect_server',
    'mcp_disconnect_server',
    'mcp_disconnect_all',
    'mcp_get_connection_info',
    
    # API functions
    'mcp_call_tool',
    'mcp_list_tools',
    'mcp_list_resources',
    'mcp_get_context_window',
    'mcp_get_server_info',
    'mcp_health_check',
    
    # Registered tools
    'refresh_mcp_tools',
    'mcp_status',
    'call_mcp_tool',
    'list_mcp_tools',
    'list_mcp_resources',
    'get_mcp_context_window',
    'add_mcp_server',
    'remove_mcp_server',
    'list_mcp_servers',
    'connect_mcp_server',
    'disconnect_mcp_server',
    'disconnect_all_mcp_servers',
    'get_mcp_server_info',
    'check_mcp_health',
] 