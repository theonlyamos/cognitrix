"""
MCP tool wrapper creation and dynamic tool management.
Handles creation of Tool wrappers for MCP server tools and agent integration.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

from cognitrix.tools.tool import tool
from cognitrix.tools.base import Tool

logger = logging.getLogger('cognitrix.log')

# Store dynamically created MCP tools
_dynamic_mcp_tools: Dict[str, Tool] = {}

def create_mcp_tool_wrapper(server_name: str, tool_info: Dict[str, Any]) -> Tool:
    """Create a Tool wrapper for an MCP server tool"""
    tool_name = tool_info.get('name', 'unknown')
    description = tool_info.get('description', 'No description available')
    input_schema = tool_info.get('input_schema', {})
    
    # Create a unique tool name to avoid conflicts
    unique_name = f"{server_name}_{tool_name}"
    
    # Extract parameters from schema
    properties = input_schema.get('properties', {})
    required = input_schema.get('required', [])
    
    # Build parameter signature for the wrapper function
    params = []
    for param_name, param_info in properties.items():
        param_type = param_info.get('type', 'string')
        param_desc = param_info.get('description', '')
        is_required = param_name in required
        
        # Convert JSON schema types to Python types
        python_type = str  # default
        if param_type == 'integer':
            python_type = int
        elif param_type == 'number':
            python_type = float
        elif param_type == 'boolean':
            python_type = bool
        elif param_type == 'array':
            python_type = list
        elif param_type == 'object':
            python_type = dict
        
        if is_required:
            params.append(f"{param_name}: {python_type.__name__}")
        else:
            params.append(f"{param_name}: Optional[{python_type.__name__}] = None")
    
    # Create the wrapper function dynamically
    async def mcp_tool_wrapper(**kwargs):
        """Dynamically generated wrapper for MCP tool"""
        try:
            from cognitrix.mcp.client import get_dynamic_client
            client = await get_dynamic_client()
            if not client.is_connected(server_name):
                return f"Server '{server_name}' is not connected. Please connect first."
            
            # Filter out None values for optional parameters
            filtered_args = {k: v for k, v in kwargs.items() if v is not None}
            
            result = await client.call_tool(server_name, tool_name, filtered_args)
            return str(result) if result else "Tool call completed with no result"
            
        except Exception as e:
            return f"Error calling MCP tool '{tool_name}': {e}"
    
    # Update wrapper function metadata
    mcp_tool_wrapper.__name__ = unique_name
    mcp_tool_wrapper.__doc__ = f"""{description}
    
Server: {server_name}
Original tool: {tool_name}

Parameters:
{chr(10).join(f"  {param}" for param in params) if params else "  None"}
"""
    
    # Create and register the tool
    wrapped_tool = tool(category="mcp_dynamic", name=unique_name)(mcp_tool_wrapper)
    
    return wrapped_tool

async def sync_mcp_tools_for_agent(agent) -> List[Tool]:
    """Synchronize MCP server tools with agent tools"""
    new_tools = []
    
    try:
        from cognitrix.mcp.client import get_dynamic_client
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        for server_name in connected_servers:
            try:
                # Get tools from this server
                tools_list = await client.list_tools(server_name)
                if not tools_list:
                    continue
                
                for tool_info in tools_list:
                    tool_wrapper = create_mcp_tool_wrapper(server_name, tool_info)
                    unique_name = f"{server_name}_{tool_info.get('name', 'unknown')}"
                    
                    # Store in global registry
                    _dynamic_mcp_tools[unique_name] = tool_wrapper
                    new_tools.append(tool_wrapper)
                    
            except Exception as e:
                logger.error(f"Error syncing tools from server {server_name}: {e}")
                
    except Exception as e:
        logger.error(f"Error syncing MCP tools: {e}")
    
    return new_tools

async def refresh_agent_mcp_tools(agent) -> str:
    """Refresh an agent's MCP tools by syncing with connected servers"""
    try:
        # Remove existing dynamic MCP tools from agent
        agent.tools = [tool for tool in agent.tools if not (hasattr(tool, 'category') and tool.category == 'mcp_dynamic')]
        
        # Add new dynamic tools
        new_tools = await sync_mcp_tools_for_agent(agent)
        agent.tools.extend(new_tools)
        
        # Save agent if it has a save method
        if hasattr(agent, 'save'):
            await agent.save()
        
        return f"Refreshed {len(new_tools)} MCP tools for agent '{agent.name}'"
        
    except Exception as e:
        return f"Error refreshing MCP tools: {e}"

async def auto_sync_mcp_tools_on_connect(server_name: str):
    """Auto-sync MCP tools when a server connects (internal helper)"""
    try:
        # This would be called after successful connection
        # For now, we'll implement the enhanced connection function below
        pass
    except Exception as e:
        logger.error(f"Error auto-syncing tools for server {server_name}: {e}")

def get_dynamic_mcp_tools() -> Dict[str, Tool]:
    """Get all registered dynamic MCP tools"""
    return _dynamic_mcp_tools.copy()

def clear_dynamic_mcp_tools():
    """Clear all dynamic MCP tools"""
    global _dynamic_mcp_tools
    _dynamic_mcp_tools.clear()

def remove_server_tools(server_name: str):
    """Remove all tools from a specific server"""
    global _dynamic_mcp_tools
    to_remove = [name for name in _dynamic_mcp_tools.keys() if name.startswith(f"{server_name}_")]
    for name in to_remove:
        del _dynamic_mcp_tools[name]
    logger.debug(f"Removed {len(to_remove)} tools from server {server_name}") 