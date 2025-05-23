from cognitrix.tools.tool import tool
import asyncio
import logging
from typing import Dict, Any, Optional, List
from contextlib import AsyncExitStack

# Import MCP SDK
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

# Import the new dynamic system
from cognitrix.tools.mcp_server_manager import mcp_server_manager, MCPServerConfig, MCPTransportType

logger = logging.getLogger('cognitrix.log')

class DynamicMCPClient:
    """Dynamic MCP client that can connect to multiple server types"""
    
    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stacks: Dict[str, AsyncExitStack] = {}
        self.connections: Dict[str, Any] = {}
    
    async def connect_to_server(self, server_config: MCPServerConfig) -> bool:
        """Connect to an MCP server based on its configuration"""
        try:
            if server_config.name in self.sessions:
                logger.warning(f"Already connected to server: {server_config.name}")
                return True
            
            if server_config.transport == MCPTransportType.STDIO:
                return await self._connect_stdio(server_config)
            elif server_config.transport == MCPTransportType.SSE:
                return await self._connect_sse(server_config)
            elif server_config.transport == MCPTransportType.HTTP:
                return await self._connect_http(server_config)
            else:
                logger.error(f"Unsupported transport type: {server_config.transport}")
                return False
                
        except Exception as e:
            logger.error(f"Error connecting to server {server_config.name}: {e}")
            return False
    
    async def _connect_stdio(self, server_config: MCPServerConfig) -> bool:
        """Connect to a STDIO MCP server"""
        try:
            # Validate required fields
            if not server_config.command:
                logger.error(f"Command is required for STDIO server {server_config.name}")
                return False
            
            # Handle Windows-specific command resolution
            command = server_config.command
            args = server_config.args or []
            
            # On Windows, handle commands like 'npx' that need shell resolution
            import platform
            import shutil
            if platform.system() == "Windows":
                # Common Node.js commands that need .cmd extension on Windows
                node_commands = ['npm', 'npx', 'node', 'yarn', 'pnpm']
                if command in node_commands:
                    # Try with .cmd extension first
                    cmd_path = shutil.which(f"{command}.cmd")
                    if cmd_path:
                        logger.info(f"Resolved Windows command '{command}' to '{cmd_path}'")
                        command = cmd_path
                    else:
                        # Fallback to original command
                        cmd_path = shutil.which(command)
                        if cmd_path:
                            logger.info(f"Resolved Windows command '{command}' to '{cmd_path}'")
                            command = cmd_path
                        else:
                            logger.warning(f"Command '{command}' not found in PATH, trying as-is")
            
            # Prepare server parameters
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=server_config.env
            )
            
            # Create exit stack for resource management
            exit_stack = AsyncExitStack()
            self.exit_stacks[server_config.name] = exit_stack
            
            # Start the server and get streams
            stdio_transport = await exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read_stream, write_stream = stdio_transport
            
            # Create session
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize the session
            await session.initialize()
            
            # Store session and connection info
            self.sessions[server_config.name] = session
            self.connections[server_config.name] = {
                'type': 'stdio',
                'config': server_config,
                'streams': (read_stream, write_stream)
            }
            
            logger.info(f"Connected to STDIO MCP server: {server_config.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to STDIO server {server_config.name}: {e}")
            if server_config.name in self.exit_stacks:
                await self.exit_stacks[server_config.name].aclose()
                del self.exit_stacks[server_config.name]
            return False
    
    async def _connect_sse(self, server_config: MCPServerConfig) -> bool:
        """Connect to an SSE MCP server"""
        try:
            # Validate required fields
            if not server_config.url:
                logger.error(f"URL is required for SSE server {server_config.name}")
                return False
            
            # Create exit stack for resource management
            exit_stack = AsyncExitStack()
            self.exit_stacks[server_config.name] = exit_stack
            
            # Connect to SSE server
            sse_transport = await exit_stack.enter_async_context(
                sse_client(url=server_config.url)
            )
            read_stream, write_stream = sse_transport
            
            # Create session
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize the session
            await session.initialize()
            
            # Store session and connection info
            self.sessions[server_config.name] = session
            self.connections[server_config.name] = {
                'type': 'sse',
                'config': server_config,
                'url': server_config.url
            }
            
            logger.info(f"Connected to SSE MCP server: {server_config.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to SSE server {server_config.name}: {e}")
            if server_config.name in self.exit_stacks:
                await self.exit_stacks[server_config.name].aclose()
                del self.exit_stacks[server_config.name]
            return False
    
    async def _connect_http(self, server_config: MCPServerConfig) -> bool:
        """Connect to an HTTP MCP server"""
        try:
            # Validate required fields
            if not server_config.url:
                logger.error(f"URL is required for HTTP server {server_config.name}")
                return False
            
            # Create exit stack for resource management
            exit_stack = AsyncExitStack()
            self.exit_stacks[server_config.name] = exit_stack
            
            # Prepare headers
            headers = server_config.headers or {}
            
            # Connect to HTTP server
            http_transport = await exit_stack.enter_async_context(
                streamablehttp_client(server_config.url, headers=headers)
            )
            read_stream, write_stream, _ = http_transport
            
            # Create session
            session = await exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize the session
            await session.initialize()
            
            # Store session and connection info
            self.sessions[server_config.name] = session
            self.connections[server_config.name] = {
                'type': 'http',
                'config': server_config,
                'url': server_config.url,
                'headers': headers
            }
            
            logger.info(f"Connected to HTTP MCP server: {server_config.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to HTTP server {server_config.name}: {e}")
            if server_config.name in self.exit_stacks:
                await self.exit_stacks[server_config.name].aclose()
                del self.exit_stacks[server_config.name]
            return False
    
    async def disconnect_from_server(self, server_name: str) -> bool:
        """Disconnect from a specific server"""
        try:
            if server_name in self.exit_stacks:
                await self.exit_stacks[server_name].aclose()
                del self.exit_stacks[server_name]
            
            if server_name in self.sessions:
                del self.sessions[server_name]
            
            if server_name in self.connections:
                del self.connections[server_name]
            
            logger.info(f"Disconnected from MCP server: {server_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error disconnecting from server {server_name}: {e}")
            return False
    
    async def disconnect_all(self):
        """Disconnect from all servers"""
        server_names = list(self.sessions.keys())
        for server_name in server_names:
            await self.disconnect_from_server(server_name)
    
    def is_connected(self, server_name: str) -> bool:
        """Check if connected to a specific server"""
        return server_name in self.sessions
    
    def get_connected_servers(self) -> List[str]:
        """Get list of connected server names"""
        return list(self.sessions.keys())
    
    async def list_tools(self, server_name: str) -> Optional[List[Dict[str, Any]]]:
        """List tools available on a specific server"""
        if server_name not in self.sessions:
            logger.error(f"Not connected to server: {server_name}")
            return None
        
        try:
            session = self.sessions[server_name]
            response = await session.list_tools()
            return [
                {
                    'name': tool.name,
                    'description': tool.description,
                    'input_schema': dict(tool.inputSchema) if tool.inputSchema else {}
                }
                for tool in response.tools
            ]
        except Exception as e:
            logger.error(f"Error listing tools for server {server_name}: {e}")
            return None
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Optional[Any]:
        """Call a tool on a specific server"""
        if server_name not in self.sessions:
            logger.error(f"Not connected to server: {server_name}")
            return None
        
        try:
            session = self.sessions[server_name]
            result = await session.call_tool(tool_name, arguments)
            return result.content
        except Exception as e:
            logger.error(f"Error calling tool {tool_name} on server {server_name}: {e}")
            return None
    
    async def list_resources(self, server_name: str) -> Optional[List[Dict[str, Any]]]:
        """List resources available on a specific server"""
        if server_name not in self.sessions:
            logger.error(f"Not connected to server: {server_name}")
            return None
        
        try:
            session = self.sessions[server_name]
            response = await session.list_resources()
            return [resource.model_dump() for resource in response.resources]
        except Exception as e:
            logger.error(f"Error listing resources for server {server_name}: {e}")
            return None
    
    async def test_connection(self, server_config: MCPServerConfig) -> Dict[str, Any]:
        """Test connection to a server without maintaining the connection"""
        temp_client = DynamicMCPClient()
        try:
            success = await temp_client.connect_to_server(server_config)
            if success:
                # Try to list tools to verify the connection works
                tools = await temp_client.list_tools(server_config.name)
                tool_count = len(tools) if tools else 0
                
                await temp_client.disconnect_from_server(server_config.name)
                
                return {
                    "success": True,
                    "message": f"Successfully connected to {server_config.name}",
                    "tool_count": tool_count,
                    "transport": server_config.transport.value
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to connect to server"
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            await temp_client.disconnect_all()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect_all()

# Global dynamic client instance
_dynamic_client = None

async def get_dynamic_client():
    """Get or create the global dynamic client"""
    global _dynamic_client
    if _dynamic_client is None:
        _dynamic_client = DynamicMCPClient()
    return _dynamic_client

# --- Enhanced functions for dynamic server management ---
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
        
        result = []
        for server in servers:
            server_info = server.to_dict()
            server_info['connected'] = server.name in connected_servers
            result.append(server_info)
        
        return result
    except Exception as e:
        return [{"error": f"Error listing MCP servers: {e}"}]

async def mcp_connect_server(name: str) -> str:
    """
    Connect to a configured MCP server.
    Args:
        name (str): Server name to connect to
    Returns:
        str: Success or error message
    """
    try:
        server_config = mcp_server_manager.get_server(name)
        if not server_config:
            return f"MCP server '{name}' not found in configuration"
        
        if not server_config.enabled:
            return f"MCP server '{name}' is disabled"
        
        client = await get_dynamic_client()
        success = await client.connect_to_server(server_config)
        
        if success:
            return f"Successfully connected to MCP server '{name}'"
        else:
            return f"Failed to connect to MCP server '{name}'"
    except Exception as e:
        return f"Error connecting to MCP server: {e}"

async def mcp_disconnect_server(name: str) -> str:
    """
    Disconnect from an MCP server.
    Args:
        name (str): Server name to disconnect from
    Returns:
        str: Success or error message
    """
    try:
        client = await get_dynamic_client()
        success = await client.disconnect_from_server(name)
        
        if success:
            return f"Successfully disconnected from MCP server '{name}'"
        else:
            return f"Failed to disconnect from MCP server '{name}'"
    except Exception as e:
        return f"Error disconnecting from MCP server: {e}"

async def mcp_test_server(name: str) -> Dict[str, Any]:
    """
    Test connection to an MCP server.
    Args:
        name (str): Server name to test
    Returns:
        Dict[str, Any]: Test result
    """
    try:
        return await mcp_server_manager.test_server_connection(name)
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- Enhanced versions of original functions ---
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

# --- Register enhanced tools for agent use ---
call_mcp_tool = tool(category="mcp")(mcp_call_tool)
list_mcp_tools = tool(category="mcp")(mcp_list_tools)
list_mcp_resources = tool(category="mcp")(mcp_list_resources)
get_mcp_context_window = tool(category="mcp")(mcp_get_context_window)

# --- Register new management tools ---
add_mcp_server = tool(category="mcp")(mcp_add_server)
remove_mcp_server = tool(category="mcp")(mcp_remove_server)
list_mcp_servers = tool(category="mcp")(mcp_list_servers)
connect_mcp_server = tool(category="mcp")(mcp_connect_server)
disconnect_mcp_server = tool(category="mcp")(mcp_disconnect_server)
test_mcp_server = tool(category="mcp")(mcp_test_server) 