"""
Dynamic MCP client for connecting to multiple server types.
Handles STDIO, SSE, and HTTP MCP server connections.
"""

import asyncio
import logging
import platform
import shutil
from contextlib import AsyncExitStack
from typing import Dict, Any, Optional, List

# Import MCP SDK
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

# Import the server manager and status tracking
from cognitrix.mcp.server_manager import MCPServerConfig, MCPTransportType
from cognitrix.mcp.status import update_connection_status

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
                update_connection_status(server_config.name, True, {'transport': server_config.transport.value})
                return True
            
            success = False
            if server_config.transport == MCPTransportType.STDIO:
                success = await self._connect_stdio(server_config)
            elif server_config.transport == MCPTransportType.SSE:
                success = await self._connect_sse(server_config)
            elif server_config.transport == MCPTransportType.HTTP:
                success = await self._connect_http(server_config)
            else:
                logger.error(f"Unsupported transport type: {server_config.transport}")
                success = False
            
            # Update global connection status
            update_connection_status(server_config.name, success, {
                'transport': server_config.transport.value,
                'description': server_config.description
            })
            
            return success
                
        except Exception as e:
            logger.error(f"Error connecting to server {server_config.name}: {e}")
            update_connection_status(server_config.name, False, {'error': str(e)})
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
            
            # Update connection status
            update_connection_status(server_name, False)
            
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