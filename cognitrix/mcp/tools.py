"""
MCP tool wrapper creation and dynamic tool management.
Handles creation of Tool wrappers for MCP server tools and agent integration.
"""

import logging
import re
from typing import Any

from cognitrix.tools.base import Tool
from cognitrix.tools.tool import tool

logger = logging.getLogger('cognitrix.log')

# Store dynamically created MCP tools
_dynamic_mcp_tools: dict[str, Tool] = {}

# JSON-schema type -> the Python type-name string Tool.to_dict_format expects.
_JSON_TO_PYNAME = {
    'string': 'str', 'integer': 'int', 'number': 'float',
    'boolean': 'bool', 'array': 'list', 'object': 'dict',
}

# Provider tool names must be [A-Za-z0-9_-]; sanitize server/tool names that
# come from an external MCP server before using them in a tool name.
_NAME_SANITIZE = re.compile(r'[^A-Za-z0-9_-]')


def create_mcp_tool_wrapper(server_name: str, tool_info: dict[str, Any]) -> Tool:
    """Create a Tool wrapper for an MCP server tool.

    The wrapper's advertised schema is taken from the server's input_schema so
    the model sees the real parameter names (the old version advertised a single
    catch-all `kwargs` param, making argument-taking MCP tools uncallable).
    """
    tool_name = str(tool_info.get('name', 'unknown'))
    description = str(tool_info.get('description', 'No description available'))
    input_schema = tool_info.get('input_schema')
    if not isinstance(input_schema, dict):
        input_schema = {}

    unique_name = _NAME_SANITIZE.sub('_', f"{server_name}_{tool_name}")

    properties = input_schema.get('properties')
    if not isinstance(properties, dict):
        properties = {}
    required = input_schema.get('required')
    if not isinstance(required, list):
        required = []

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

    # Docstring carries per-param descriptions (:param) so to_dict_format picks
    # them up; the flat parameters/required below fix the advertised schema.
    doc_lines = [description, ""]
    for pname, pinfo in properties.items():
        pdesc = pinfo.get('description', '') if isinstance(pinfo, dict) else ''
        if pdesc:
            doc_lines.append(f":param {pname}: {pdesc}")
    mcp_tool_wrapper.__name__ = unique_name
    mcp_tool_wrapper.__doc__ = "\n".join(doc_lines)

    wrapped_tool = tool(category="mcp_dynamic", name=unique_name)(mcp_tool_wrapper)
    # Override the (kwargs-only) introspected schema with the server's real one.
    wrapped_tool.parameters = {
        pname: _JSON_TO_PYNAME.get((pinfo or {}).get('type', 'string'), 'str')
        if isinstance(pinfo, dict) else 'str'
        for pname, pinfo in properties.items()
    }
    wrapped_tool.required_params = [r for r in required if r in properties]

    return wrapped_tool

async def sync_mcp_tools_for_agent(agent) -> list[Tool]:
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

def get_dynamic_mcp_tools() -> dict[str, Tool]:
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
