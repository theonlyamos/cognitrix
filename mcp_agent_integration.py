#!/usr/bin/env python3
"""
MCP Agent Integration Utility

This script demonstrates how to integrate MCP server tools with Cognitrix agents,
making MCP tools directly available to the LLM without needing to use call_mcp_tool.

Usage examples:
    # Auto-sync MCP tools when creating an agent
    python mcp_agent_integration.py --agent my_agent --auto-sync
    
    # Manually refresh MCP tools for an existing agent
    python mcp_agent_integration.py --agent my_agent --refresh
    
    # List available MCP tools
    python mcp_agent_integration.py --list-tools
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Add Cognitrix to path
sys.path.insert(0, str(Path(__file__).parent))

from cognitrix.tools.mcp_client import (
    get_dynamic_client, 
    refresh_agent_mcp_tools,
    sync_mcp_tools_for_agent,
    mcp_list_servers,
    mcp_connect_server,
    _dynamic_mcp_tools
)
from cognitrix.agents.base import Agent

async def auto_connect_and_sync_agent(agent_name: str) -> str:
    """Auto-connect to available MCP servers and sync tools with agent"""
    try:
        # Load or create agent
        try:
            agent = Agent.load(agent_name)
            print(f"✓ Loaded existing agent: {agent_name}")
        except:
            print(f"Creating new agent: {agent_name}")
            agent = Agent(
                name=agent_name,
                model="gpt-4o-mini",
                system_prompt=f"You are {agent_name}, an AI assistant with access to MCP server tools."
            )
            agent.save()
        
        # Get configured servers
        servers = await mcp_list_servers()
        connected_count = 0
        
        for server in servers:
            if not server.get('connected', False):
                server_name = server.get('name')
                if server_name:
                    print(f"🔌 Connecting to MCP server: {server_name}")
                    result = await mcp_connect_server(server_name)
                    if "Successfully connected" in result:
                        connected_count += 1
                        print(f"✓ {result}")
                    else:
                        print(f"✗ {result}")
        
        # Sync MCP tools with agent
        print(f"🔄 Syncing MCP tools with agent...")
        sync_result = await refresh_agent_mcp_tools(agent)
        print(f"✓ {sync_result}")
        
        return f"Successfully set up agent '{agent_name}' with MCP tools"
        
    except Exception as e:
        return f"Error setting up agent: {e}"

async def refresh_agent_tools(agent_name: str) -> str:
    """Refresh MCP tools for an existing agent"""
    try:
        agent = Agent.load(agent_name)
        result = await refresh_agent_mcp_tools(agent)
        return result
    except Exception as e:
        return f"Error refreshing tools for agent '{agent_name}': {e}"

async def list_mcp_tools() -> str:
    """List all available MCP tools"""
    try:
        client = await get_dynamic_client()
        connected_servers = client.get_connected_servers()
        
        if not connected_servers:
            return "No MCP servers connected. Connect to servers first."
        
        output = ["📋 Available MCP Tools:", ""]
        
        for server_name in connected_servers:
            tools = await client.list_tools(server_name)
            if tools:
                output.append(f"🔧 Server: {server_name}")
                for tool in tools:
                    tool_name = tool.get('name', 'Unknown')
                    description = tool.get('description', 'No description')
                    unique_name = f"{server_name}_{tool_name}"
                    output.append(f"  • {unique_name}: {description}")
                output.append("")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"Error listing MCP tools: {e}"

async def show_agent_tools(agent_name: str) -> str:
    """Show tools available to a specific agent"""
    try:
        agent = Agent.load(agent_name)
        
        output = [f"🤖 Tools available to agent '{agent_name}':", ""]
        
        # Group tools by category
        tools_by_category = {}
        for tool in agent.tools:
            category = getattr(tool, 'category', 'other')
            if category not in tools_by_category:
                tools_by_category[category] = []
            tools_by_category[category].append(tool)
        
        for category, tools in tools_by_category.items():
            output.append(f"📂 {category.upper()} ({len(tools)} tools)")
            for tool in tools:
                tool_name = getattr(tool, '__name__', 'unknown')
                tool_doc = getattr(tool, '__doc__', 'No description') or 'No description'
                first_line = tool_doc.split('\n')[0].strip()
                output.append(f"  • {tool_name}: {first_line}")
            output.append("")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"Error showing agent tools: {e}"

async def main():
    parser = argparse.ArgumentParser(description="MCP Agent Integration Utility")
    parser.add_argument("--agent", help="Agent name to work with")
    parser.add_argument("--auto-sync", action="store_true", help="Auto-connect to MCP servers and sync tools")
    parser.add_argument("--refresh", action="store_true", help="Refresh MCP tools for existing agent")
    parser.add_argument("--list-tools", action="store_true", help="List available MCP tools")
    parser.add_argument("--show-agent-tools", action="store_true", help="Show tools available to agent")
    
    args = parser.parse_args()
    
    if args.list_tools:
        result = await list_mcp_tools()
        print(result)
    elif args.agent and args.auto_sync:
        result = await auto_connect_and_sync_agent(args.agent)
        print(result)
    elif args.agent and args.refresh:
        result = await refresh_agent_tools(args.agent)
        print(result)
    elif args.agent and args.show_agent_tools:
        result = await show_agent_tools(args.agent)
        print(result)
    else:
        print("Usage examples:")
        print("  python mcp_agent_integration.py --list-tools")
        print("  python mcp_agent_integration.py --agent my_agent --auto-sync")
        print("  python mcp_agent_integration.py --agent my_agent --refresh")
        print("  python mcp_agent_integration.py --agent my_agent --show-agent-tools")

if __name__ == "__main__":
    asyncio.run(main()) 