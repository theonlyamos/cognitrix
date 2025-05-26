# MCP Agent Integration Guide

This guide shows you how to make MCP server tools directly available to your Cognitrix agents, so the LLM can call them as native tools instead of using `call_mcp_tool`.

## Quick Start

### Method 1: Using the Integration Utility Script

1. **Auto-setup a new agent with MCP tools:**

```bash
python mcp_agent_integration.py --agent my_web_agent --auto-sync
```

2. **Refresh MCP tools for existing agent:**

```bash
python mcp_agent_integration.py --agent my_web_agent --refresh
```

3. **List available MCP tools:**

```bash
python mcp_agent_integration.py --list-tools
```

4. **Show agent's current tools:**

```bash
python mcp_agent_integration.py --agent my_web_agent --show-agent-tools
```

### Method 2: Programmatic Integration

```python
import asyncio
from cognitrix.agent import Agent
from cognitrix.tools.mcp_client import (
    mcp_connect_server,
    refresh_agent_mcp_tools
)

async def setup_agent_with_mcp():
    # Load or create agent
    agent = Agent.load("my_agent")  # or Agent(name="my_agent", ...)

    # Connect to MCP servers
    await mcp_connect_server("playwright_server")
    await mcp_connect_server("weather_server")

    # Sync MCP tools to agent
    result = await refresh_agent_mcp_tools(agent)
    print(result)

    return agent

# Run it
agent = asyncio.run(setup_agent_with_mcp())
```

## How It Works

### Before Integration (Traditional Way)

Your agent would need to use the generic `call_mcp_tool` function:

```python
# Agent must explicitly call MCP tools through wrapper
result = call_mcp_tool(
    tool_name="screenshot",
    arguments={"url": "https://example.com"},
    server_name="playwright_server"
)
```

### After Integration (Direct Tool Access)

MCP server tools become individual agent tools:

```python
# Agent can directly call MCP tools as if they were native
result = playwright_server_screenshot(url="https://example.com")
result = weather_server_get_forecast(location="New York")
result = browser_use_server_navigate(url="https://example.com")
```

## Tool Naming Convention

MCP tools are automatically prefixed with their server name to avoid conflicts:

- `screenshot` from `playwright_server` → `playwright_server_screenshot`
- `get_forecast` from `weather_server` → `weather_server_get_forecast`
- `navigate` from `browser_use_server` → `browser_use_server_navigate`

## What Happens During Sync

1. **Discovery**: Connects to all configured MCP servers
2. **Tool Enumeration**: Lists available tools from each server
3. **Wrapper Creation**: Creates individual Tool objects for each MCP tool
4. **Schema Mapping**: Converts JSON schemas to Python type hints
5. **Registration**: Adds tools to the agent with category `mcp_dynamic`

## Example: Web Automation Agent

```python
import asyncio
from cognitrix.agent import Agent
from cognitrix.tools.mcp_client import mcp_connect_server, refresh_agent_mcp_tools

async def create_web_agent():
    # Create specialized web automation agent
    agent = Agent(
        name="web_automation_agent",
        model="gpt-4o",
        system_prompt="""You are a web automation specialist. You have direct access to:
        - playwright_server_screenshot: Take screenshots of web pages
        - playwright_server_navigate: Navigate to URLs
        - playwright_server_click: Click elements on pages
        - playwright_server_type: Type text into form fields

        Use these tools to help users automate web tasks."""
    )

    # Connect to Playwright MCP server
    await mcp_connect_server("playwright_server")

    # Sync MCP tools - now playwright tools are directly available
    await refresh_agent_mcp_tools(agent)

    agent.save()
    return agent

# The agent can now directly use tools like:
# - playwright_server_screenshot(url="https://example.com")
# - playwright_server_navigate(url="https://example.com")
# - playwright_server_click(selector="button#submit")
```

## Benefits

### 🎯 **Natural Tool Usage**

- LLM sees MCP tools as regular function calls
- No need to remember `call_mcp_tool` syntax
- Better function calling experience

### 🔍 **Better Tool Discovery**

- Tools appear in agent tool lists
- Clear documentation and type hints
- Proper parameter validation

### ⚡ **Performance**

- Direct routing to MCP servers
- No extra wrapper overhead
- Cleaner execution flow

### 🛠 **Developer Experience**

- IDE auto-completion for tool parameters
- Type safety with proper schemas
- Easy debugging and monitoring

## Managing Tool Lifecycle

### Adding New Tools

When you connect to new MCP servers, refresh tools:

```python
# Connect to new server
await mcp_connect_server("new_server")

# Refresh agent tools
await refresh_agent_mcp_tools(agent)
```

### Removing Tools

Disconnect from servers and refresh:

```python
# Disconnect from server
await mcp_disconnect_server("old_server")

# Refresh to remove tools
await refresh_agent_mcp_tools(agent)
```

### Tool Categories

MCP tools get categorized as `mcp_dynamic` to distinguish them from native tools.

## Configuration Requirements

Ensure your `~/.cognitrix/mcp.json` has proper server configurations:

```json
{
  "servers": {
    "playwright_server": {
      "transport": "stdio",
      "command": "npx.cmd",
      "args": ["@playwright/mcp@latest"],
      "description": "Browser automation server",
      "enabled": true
    }
  }
}
```

## Troubleshooting

### Tools Not Appearing

1. Check server connection: `/mcp` (should show "Connected")
2. Verify server has tools: `/mcp-tools playwright_server`
3. Refresh agent tools: `python mcp_agent_integration.py --agent NAME --refresh`

### Tool Call Failures

- Ensure server is still connected
- Check tool parameters match expected schema
- Review MCP server logs for errors

### Performance Issues

- Limit number of connected servers
- Use specific servers for specific tasks
- Monitor MCP server resource usage

## Advanced Usage

### Custom Tool Filtering

You can modify `sync_mcp_tools_for_agent` to filter which tools get added:

```python
# Only sync tools matching pattern
tools_list = await client.list_tools(server_name)
filtered_tools = [t for t in tools_list if 'screenshot' in t.get('name', '')]
```

### Tool Metadata Enhancement

Add custom metadata to generated tools:

```python
# Enhance tool descriptions
tool_wrapper.__doc__ = f"🌐 {description}\n\nServer: {server_name}\nCategory: Web Automation"
```

This integration system makes MCP tools feel like native Cognitrix tools, providing a seamless experience for both developers and LLMs!
