# Dynamic MCP Server Management in Cognitrix

This document explains how to use the dynamic MCP (Model Context Protocol) server management feature in Cognitrix, which allows you to add, configure, and connect to multiple MCP servers dynamically.

## Overview

The dynamic MCP server management system supports three transport types:

- **STDIO**: Local command-line servers (Python scripts, npm packages)
- **HTTP**: HTTP-based MCP servers with streamable connections
- **SSE**: Server-Sent Events based MCP servers

## Configuration

### Configuration File Location

MCP server configurations are stored in `~/.cognitrix/mcp.json`. This file is automatically created when you first add a server.

### Configuration Format

```json
{
  "servers": [
    {
      "name": "server_name",
      "transport": "stdio|http|sse",
      "description": "Server description",
      "enabled": true
      // Transport-specific fields...
    }
  ],
  "version": "1.0"
}
```

## Server Types and Configuration

### STDIO Servers

For local command-line servers (Python scripts, npm packages):

```json
{
  "name": "weather_server",
  "transport": "stdio",
  "description": "Weather information server",
  "command": "python",
  "args": ["weather_server.py"],
  "env": { "API_KEY": "your-key" },
  "working_directory": "/path/to/server",
  "enabled": true
}
```

**Fields:**

- `command`: Executable command (e.g., "python", "node", "npx")
- `args`: Command arguments as array
- `env`: Environment variables (optional)
- `working_directory`: Working directory for the command (optional)

### HTTP Servers

For HTTP-based MCP servers:

```json
{
  "name": "api_server",
  "transport": "http",
  "description": "Remote API server",
  "url": "https://api.example.com/mcp",
  "headers": {
    "Authorization": "Bearer token",
    "Content-Type": "application/json"
  },
  "timeout": 30,
  "enabled": true
}
```

**Fields:**

- `url`: Server URL
- `headers`: HTTP headers (optional)
- `timeout`: Connection timeout in seconds

### SSE Servers

For Server-Sent Events based servers:

```json
{
  "name": "sse_server",
  "transport": "sse",
  "description": "SSE-based server",
  "url": "http://localhost:8000/sse",
  "headers": { "Custom-Header": "value" },
  "timeout": 30,
  "enabled": true
}
```

**Fields:**

- `url`: SSE endpoint URL
- `headers`: HTTP headers (optional)
- `timeout`: Connection timeout in seconds

## Using MCP Tools in Agents

### Available Tools

The following MCP management tools are available to agents:

#### Server Management

- `add_mcp_server`: Add a new MCP server configuration
- `remove_mcp_server`: Remove an MCP server configuration
- `list_mcp_servers`: List all configured servers
- `connect_mcp_server`: Connect to a configured server
- `disconnect_mcp_server`: Disconnect from a server
- `test_mcp_server`: Test connection to a server

#### Tool Usage

- `call_mcp_tool`: Call a tool on an MCP server
- `list_mcp_tools`: List available tools from connected servers
- `list_mcp_resources`: List available resources from connected servers
- `get_mcp_context_window`: Get context window information

### Example Agent Usage

```python
# Agent can add a new server
result = await add_mcp_server(
    name="my_server",
    transport="stdio",
    command="python",
    args=["my_server.py"],
    description="My custom server"
)

# Connect to the server
await connect_mcp_server("my_server")

# List available tools
tools = await list_mcp_tools()

# Call a specific tool
result = await call_mcp_tool(
    tool_name="weather_forecast",
    arguments={"location": "New York"},
    server_name="my_server"  # Optional: specify server
)
```

## CLI Commands

### Basic MCP Commands

```bash
# List MCP server tools
cognitrix
> /mcp

# Add MCP server (interactive)
> /add mcp

# Show MCP server info
> /show mcp server_name

# Delete MCP server
> /delete mcp server_name
```

### Loading MCP Tools

```bash
# Load MCP tools category
cognitrix --load-tools "mcp"

# Load multiple categories including MCP
cognitrix --load-tools "mcp,web,system"
```

## Programming Interface

### Adding Servers Programmatically

```python
from cognitrix.tools.mcp_server_manager import mcp_server_manager, MCPServerConfig, MCPTransportType

# Create server configuration
server_config = MCPServerConfig(
    name="my_server",
    transport=MCPTransportType.STDIO,
    command="python",
    args=["server.py"],
    description="My server"
)

# Add to manager
success = mcp_server_manager.add_server(server_config)
```

### Using Dynamic Client

```python
from cognitrix.tools.mcp_client_dynamic import DynamicMCPClient

async def use_mcp_server():
    client = DynamicMCPClient()

    # Connect to server
    server_config = mcp_server_manager.get_server("my_server")
    await client.connect_to_server(server_config)

    # Use server
    tools = await client.list_tools("my_server")
    result = await client.call_tool("my_server", "tool_name", {"arg": "value"})

    # Cleanup
    await client.disconnect_all()
```

## Example Configurations

### Popular MCP Servers

#### Playwright Browser Automation

```json
{
  "name": "playwright",
  "transport": "stdio",
  "description": "Browser automation with Playwright",
  "command": "npx",
  "args": ["@playwright/mcp@latest"],
  "enabled": true
}
```

#### Weather Service

```json
{
  "name": "weather",
  "transport": "stdio",
  "description": "Weather information service",
  "command": "python",
  "args": ["weather_server.py"],
  "enabled": true
}
```

#### Browser Use SSE Server

```json
{
  "name": "browser_use",
  "transport": "sse",
  "description": "Browser automation via SSE",
  "url": "http://localhost:8000/sse",
  "enabled": true
}
```

## Testing and Debugging

### Test Script

Run the included test script to verify functionality:

```bash
python test_mcp_dynamic.py
```

### Manual Testing

```python
import asyncio
from cognitrix.tools.mcp_client import *

async def test():
    # Add server
    result = await mcp_add_server(
        name="test_server",
        transport="stdio",
        command="echo",
        args=["hello"]
    )
    print(result)

    # List servers
    servers = await mcp_list_servers()
    print(servers)

asyncio.run(test())
```

## Error Handling

The system includes comprehensive error handling:

- **Connection errors**: Graceful handling of server unavailability
- **Configuration errors**: Validation of server configurations
- **Transport errors**: Specific error messages for different transport types
- **Timeout handling**: Configurable timeouts for connections

## Best Practices

1. **Server Naming**: Use descriptive names for servers
2. **Error Handling**: Always check return values from MCP tools
3. **Resource Management**: Disconnect from servers when done
4. **Configuration**: Keep server configurations in version control
5. **Testing**: Test server connections before using in production

## Troubleshooting

### Common Issues

1. **Server not found**: Check server name and configuration
2. **Connection timeout**: Increase timeout or check server availability
3. **Permission errors**: Ensure proper file permissions for STDIO servers
4. **Port conflicts**: Check if ports are available for HTTP/SSE servers

### Debug Mode

Enable debug logging to troubleshoot issues:

```python
import logging
logging.getLogger('cognitrix.log').setLevel(logging.DEBUG)
```

## Migration from Legacy MCP

The new system maintains backward compatibility with the legacy hardcoded MCP server. If no dynamic servers are connected, the system falls back to the legacy behavior.

To migrate:

1. Add your servers using the new configuration system
2. Connect to them explicitly
3. Remove any hardcoded server URLs from your code

## Future Enhancements

Planned features:

- Server discovery and auto-configuration
- Load balancing across multiple servers
- Server health monitoring
- Configuration templates for popular servers
- Web UI for server management
