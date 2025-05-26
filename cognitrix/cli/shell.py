"""
Interactive shell implementation with command completion and MCP support.
"""
import os
import sys
import getpass
import socket
import subprocess
from typing import List
import re

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML

from .handlers import list_agents, list_tasks, list_teams, list_tools, add_agent

console = Console()


class CognitrixCompleter(Completer):
    """Custom completer for Cognitrix CLI commands."""
    
    def __init__(self, builtins, custom_commands):
        self.builtins = builtins
        self.custom_commands = custom_commands
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        # Complete built-ins and custom commands
        for cmd in self.builtins + self.custom_commands:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))
        # If 'cd ' or starts with a shell command, complete paths
        if text.startswith('cd '):
            # Complete after 'cd '
            arg = text[3:].lstrip()
            subdoc = Document(arg, cursor_position=len(arg))
            for c in self.path_completer.get_completions(subdoc, complete_event):
                yield c
        elif text and (text.split()[0] not in self.builtins + self.custom_commands):
            # For general shell commands, complete files/dirs after first word
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                arg = parts[1]
                subdoc = Document(arg, cursor_position=len(arg))
                for c in self.path_completer.get_completions(subdoc, complete_event):
                    yield c


def run_shell_command(command: str) -> bool:
    """Execute a shell command and display the result."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            output = result.stdout.strip()
            if output:
                console.print(Panel(output, title="[bold green]Shell Output", border_style="green"))
            else:
                console.print(Panel("[dim]Command executed successfully, but no output.", 
                                   title="[bold green]Shell Output", border_style="green"))
            return True
        else:
            # console.print(Panel(f"[bold red]Command failed with exit code {result.returncode}\n{result.stderr}", 
            #                    title="[bold red]Shell Error", border_style="red"))
            return False
    except Exception as e:
        # console.print(Panel(f"[bold red]Error executing command: {e}", 
        #                    title="[bold red]Shell Error", border_style="red"))
        return False


async def handle_slash_command(query: str, agent, session) -> bool:
    """Handle slash commands and return True if command was processed."""
    if not query.startswith('/'):
        return False
        
    parts = query[1:].strip().split()
    if not parts:
        console.print(Panel("Empty command.", style="bold red"))
        return True
        
    cmd = parts[0].lower()
    args = parts[1:]
    
    # Entity listing commands
    if cmd in ['tools', 'agents', 'tasks', 'teams', 'mcp'] and not args:
        if cmd == 'tools':
            list_tools()
        elif cmd == 'agents':
            await list_agents()
        elif cmd == 'tasks':
            list_tasks()
        elif cmd == 'teams':
            list_teams()
        elif cmd == 'mcp':
            await handle_mcp_list()
        return True
    
    # MCP-specific commands
    if cmd.startswith('mcp-'):
        await handle_mcp_command(cmd, args)
        return True
    
    # Other slash commands
    if cmd in ['add', 'delete', 'show']:
        await handle_entity_command(cmd, args)
        return True
        
    console.print(Panel(f"Unknown slash command: {query}", style="bold red"))
    return True


async def handle_mcp_list():
    """Handle MCP server listing."""
    from cognitrix.mcp import mcp_list_servers
    servers = await mcp_list_servers()
    
    if servers and isinstance(servers, list) and len(servers) > 0 and 'error' in servers[0]:
        console.print(Panel(servers[0]['error'], title="MCP Servers", border_style="red"))
    else:
        if not servers:
            console.print(Panel("No MCP servers configured.", title="MCP Servers", border_style="yellow"))
        else:
            server_list = []
            for i, server in enumerate(servers):
                status = "🟢 Connected" if server.get('connected') else "🔴 Disconnected"
                transport = server.get('transport', 'unknown')
                name = server.get('name', 'unnamed')
                description = server.get('description', 'No description')
                server_list.append(f"[{i+1}] {name} ({transport}) - {status}")
                if description and description != 'No description':
                    server_list.append(f"    {description}")
            console.print(Panel("\n".join(server_list), title="MCP Servers", border_style="blue"))


async def handle_mcp_command(cmd: str, args: List[str]):
    """Handle MCP-specific commands."""
    if cmd == 'mcp-tools':
        from cognitrix.mcp import mcp_list_tools
        server_name = args[0] if args else None
        tools = await mcp_list_tools(server_name)
        
        if tools and isinstance(tools, list) and len(tools) > 0 and 'error' in tools[0]:
            console.print(Panel(tools[0]['error'], 
                               title=f"MCP Tools{' - ' + server_name if server_name else ''}", 
                               border_style="red"))
        else:
            if not tools:
                console.print(Panel("No tools available.", 
                                   title=f"MCP Tools{' - ' + server_name if server_name else ''}", 
                                   border_style="yellow"))
            else:
                tool_list = []
                for i, tool in enumerate(tools):
                    tool_name = tool.get('name', str(tool))
                    tool_desc = tool.get('description', 'No description')
                    server = tool.get('server', server_name or 'unknown')
                    tool_list.append(f"[{i+1}] {tool_name} ({server})")
                    if tool_desc and tool_desc != 'No description':
                        tool_list.append(f"    {tool_desc}")
                console.print(Panel("\n".join(tool_list), 
                                   title=f"MCP Tools{' - ' + server_name if server_name else ''}", 
                                   border_style="green"))
    
    elif cmd == 'mcp-tool-info':
        await handle_mcp_tool_info(args)
    
    elif cmd == 'mcp-call':
        await handle_mcp_tool_call(args)
    
    elif cmd in ['mcp-connect', 'mcp-disconnect', 'mcp-sync']:
        await handle_mcp_connection(cmd, args)


async def handle_mcp_tool_info(args: List[str]):
    """Handle MCP tool info command."""
    if not args:
        console.print(Panel("Usage: /mcp-tool-info <tool_name> [server_name]", border_style="red"))
        return
    
    tool_name = args[0]
    server_name = args[1] if len(args) > 1 else None
    
    from cognitrix.mcp import mcp_list_tools
    tools = await mcp_list_tools(server_name)
    
    if tools and isinstance(tools, list) and len(tools) > 0 and 'error' not in tools[0]:
        target_tool = None
        for tool in tools:
            if tool.get('name') == tool_name:
                target_tool = tool
                break
        
        if target_tool:
            details = []
            details.append(f"Name: {target_tool.get('name', 'Unknown')}")
            details.append(f"Server: {target_tool.get('server', 'Unknown')}")
            details.append(f"Description: {target_tool.get('description', 'No description')}")
            
            # Show input schema
            schema = target_tool.get('input_schema', {})
            if schema:
                details.append("\nParameters:")
                properties = schema.get('properties', {})
                required = schema.get('required', [])
                
                for prop_name, prop_info in properties.items():
                    prop_type = prop_info.get('type', 'unknown')
                    prop_desc = prop_info.get('description', 'No description')
                    is_required = prop_name in required
                    req_str = " (required)" if is_required else " (optional)"
                    details.append(f"  • {prop_name} ({prop_type}){req_str}: {prop_desc}")
            else:
                details.append("\nParameters: None")
            
            console.print(Panel("\n".join(details), title=f"Tool Info: {tool_name}", border_style="blue"))
        else:
            console.print(Panel(f"Tool '{tool_name}' not found.", border_style="red"))
    else:
        error_msg = tools[0].get('error', 'Failed to list tools') if tools else 'No tools available'
        console.print(Panel(error_msg, border_style="red"))


async def handle_mcp_tool_call(args: List[str]):
    """Handle MCP tool call command."""
    if not args:
        console.print(Panel("Usage: /mcp-call <tool_name> [server_name] [arg1=value1] [arg2=value2]...", border_style="red"))
        return
    
    tool_name = args[0]
    server_name = None
    tool_args = {}
    
    remaining_args = args[1:]
    
    # If first remaining arg doesn't contain '=', treat it as server name
    if remaining_args and '=' not in remaining_args[0]:
        server_name = remaining_args[0]
        remaining_args = remaining_args[1:]
    
    # Parse tool arguments (key=value format)
    for arg in remaining_args:
        if '=' in arg:
            key, value = arg.split('=', 1)
            # Try to parse as JSON for complex types, otherwise use as string
            try:
                import json
                tool_args[key] = json.loads(value)
            except:
                tool_args[key] = value
        else:
            console.print(Panel(f"Invalid argument format: {arg}. Use key=value format.", border_style="red"))
            return
    
    from cognitrix.mcp import mcp_call_tool
    result = await mcp_call_tool(tool_name, tool_args, server_name)
    
    console.print(Panel(str(result), title=f"Tool Result: {tool_name}", border_style="green"))


async def handle_mcp_connection(cmd: str, args: List[str]):
    """Handle MCP connection commands."""
    from cognitrix.mcp import mcp_connect_server, mcp_disconnect_server
    
    if cmd == 'mcp-connect':
        if not args:
            console.print(Panel("Usage: /mcp-connect <server_name>", border_style="red"))
            return
        result = await mcp_connect_server(args[0])
        if result.get('success'):
            console.print(Panel(f"✅ {result.get('message', 'Connected successfully')}", border_style="green"))
        else:
            console.print(Panel(f"❌ {result.get('error', 'Connection failed')}", border_style="red"))
    
    elif cmd == 'mcp-disconnect':
        if not args:
            console.print(Panel("Usage: /mcp-disconnect <server_name>", border_style="red"))
            return
        result = await mcp_disconnect_server(args[0])
        if result.get('success'):
            console.print(Panel(f"✅ {result.get('message', 'Disconnected successfully')}", border_style="green"))
        else:
            console.print(Panel(f"❌ {result.get('error', 'Disconnection failed')}", border_style="red"))
    
    # elif cmd == 'mcp-sync':
    #     result = await mcp_sync_tools()
    #     if result.get('success'):
    #         console.print(Panel(f"✅ {result.get('message', 'Tools synced successfully')}", border_style="green"))
    #     else:
    #         console.print(Panel(f"❌ {result.get('error', 'Sync failed')}", border_style="red"))


async def handle_entity_command(cmd: str, args: List[str]):
    """Handle entity management commands (add, delete, show)."""
    if cmd == 'add' and args:
        entity = args[0].lower()
        if entity == 'agent':
            add_agent()
    
    # elif cmd == 'show' and len(args) >= 2:
    #     entity = args[0].lower()
    #     identifier = args[1]
        
    #     if entity == 'mcp':
    #         from cognitrix.mcp import mcp_test_server
    #         test_result = await mcp_test_server(identifier)
    #         if test_result.get('success'):
    #             console.print(Panel(f"✅ {test_result.get('message', 'Connection successful')}", border_style="green"))
    #         else:
    #             console.print(Panel(f"❌ {test_result.get('error', 'Connection failed')}", border_style="red"))
    #     else:
    #         console.print(Panel(f"Unknown entity type: {entity}", style="bold red"))
    else:
        console.print(Panel(f"Unknown or malformed command: /{cmd} {' '.join(args)}", style="bold red"))


async def handle_legacy_commands(query: str, agent, session):
    """Handle legacy command format for backward compatibility."""
    command_handlers = {
        'add agent': add_agent,
        'list tools': lambda: console.print(Panel("\n".join(f"[{i}] {tool.name}" for i, tool in enumerate(agent.tools)), 
                                                  title="Available Tools", border_style="blue")),
        'list agents': lambda: list_agents(),
        'show history': lambda: console.print(Panel("\n".join(f"[{chat['role']}]: {chat['message']}" for chat in session.chat), 
                                                    title="Chat History", border_style="magenta"))
    }
    
    if query in command_handlers:
        handler = command_handlers[query]
        if hasattr(handler, '__call__'):
            import asyncio
            if asyncio.iscoroutinefunction(handler):
                await handler()
            else:
                handler()
        return True
    return False


async def initialize_shell(session, agent, stream: bool = False):
    """Initialize and run the interactive shell."""
    async def _handle_list_agents():
        """Helper function to list child agents."""
        child_agents = agent.child_agents or []
        table = Table(title="Child Agents")
        table.add_column("#", style="cyan")
        table.add_column("Name", style="bold")
        for i, a in enumerate(child_agents):
            table.add_row(str(i), a.name)
        console.print(table)

    # Welcome banner
    console.print(Panel(
        f"[bold cyan]Welcome to Cognitrix AI Shell!\n[white]Type your message or a shell command. Type [bold yellow]q[/bold yellow] to quit.",
        title=f"[bold blue]Agent: {agent.name}",
        border_style="cyan"
    ))

    username = getpass.getuser()
    hostname = socket.gethostname()

    builtins = ['cd', 'exit', 'quit', 'q']
    custom_commands = [
        'add agent', 'list tools', 'list agents', 'show history', 
        '/mcp', '/mcp-tools', '/mcp-tool-info', '/mcp-call', '/mcp-sync', 
        '/mcp-connect', '/mcp-disconnect', '/add', '/delete', '/show'
    ]
    
    session_completer = CognitrixCompleter(builtins, custom_commands)
    prompt_session = PromptSession(completer=session_completer, history=InMemoryHistory())

    while True:
        try:
            # Build prompt string
            cwd = os.getcwd()
            home = os.path.expanduser('~')
            sep = os.path.sep
            
            if cwd.startswith(home):
                display_cwd = '~' + cwd[len(home):]
            else:
                display_cwd = cwd
                
            # Normalize separators for display
            if sep != '/':
                display_cwd = display_cwd.replace('/', sep)
                
            prompt_str = HTML(f'<ansigreen>{username}@{hostname}</ansigreen>:<ansiblue>{display_cwd}</ansiblue>$ ')
            query = await prompt_session.prompt_async(prompt_str)
            query = query.strip()
            
            if not query:
                continue
                
            # Check for exit commands
            if query.lower() in ['q', 'quit', 'exit']:
                console.print(Panel("Exiting...", style="bold yellow"))
                break
            
            # Handle slash commands
            if await handle_slash_command(query, agent, session):
                continue
            
            # Handle built-in shell commands
            if query.startswith('cd'):
                parts = query.split(maxsplit=1)
                path = parts[1].strip() if len(parts) > 1 else os.path.expanduser('~')
                try:
                    os.chdir(path)
                    console.print(Panel(f"Changed directory to [bold blue]{os.getcwd()}[/bold blue]", border_style="green"))
                except Exception as e:
                    console.print(Panel(f"[bold red]cd: {e}", border_style="red"))
                continue
            
            # Handle legacy commands
            if await handle_legacy_commands(query, agent, session):
                continue
            
            # Try to execute as shell command
            if run_shell_command(query):
                continue
            
            # Send to AI agent
            console.print(Panel(f"[bold blue]Sending to AI...", border_style="blue"))
            await session(query, agent, stream=stream)
            console.print()
            
        except KeyboardInterrupt:
            console.print(Panel("Exiting...", style="bold yellow"))
            break
        except Exception as e:
            import logging
            logging.exception(e)
            console.print(Panel(f"[bold red]Error: {e}", border_style="red"))
            break 