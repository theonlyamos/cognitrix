import os
import sys
import time
import asyncio
import logging
import argparse
from pathlib import Path
from argparse import Namespace
from rich import print
from functools import lru_cache
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from rich.table import Table
import shlex
import getpass
import socket
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML

logger = logging.getLogger('cognitrix.log')

# =====================
# Utility Functions
# =====================
def print_table(rows, headers):
    """Prints a table given rows (list of lists) and headers (list)."""
    if not rows:
        print("\nNo data found.")
        return
    col_widths = [max(len(str(cell)) for cell in col) for col in zip(*([headers] + rows))]
    fmt = '| ' + ' | '.join(f'{{:<{w}}}' for w in col_widths) + ' |'
    sep = '|-' + '-|-'.join('-' * w for w in col_widths) + '-|'
    print('\n' + fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*row))
    print()

def str_or_file(string):
    if len(string) > 100:
        return string
    if Path(string).is_file() or Path(os.curdir, string).is_file():
        with open(Path(string), 'rt') as file:
            return file.read()
    return string

# =====================
# Agent Management
# =====================
async def list_agents():
    from cognitrix.agents import Agent
    agents = await Agent.all()
    rows = [[i+1, a.name] for i, a in enumerate(agents)]
    print_table(rows, ["#", "Agent Name"])

def add_agent():
    from cognitrix.providers import LLM
    from cognitrix.agents import Agent
    name = None
    provider = None
    system_prompt = None
    try:
        while not name:
            name = input("Enter agent name: ")
            while not provider:
                llms = LLM.list_llms()
                llms_str = "\nAvailable LLMs:"
                for index, llm_l in enumerate(llms):
                    llms_str += (f"\n[{index}] {llm_l.__name__}")
                print(llms_str)
                agent_llm = int(input("\n[Select LLM]: "))
                loaded_llm = llms[agent_llm]
                if loaded_llm:
                    provider = loaded_llm()
                    if provider:
                        provider.model = input(f"\nEnter model name [{provider.model}]: ") or provider.model
                        temp = input(f"\nEnter model temperature [{provider.temperature}]: ")
                        provider.temperature = float(temp) if temp else provider.temperature
            while not system_prompt:
                system_prompt = input("\n[Enter agent system prompt]: ")
            new_agent = asyncio.run(Agent.create_agent(name, system_prompt=system_prompt, provider=provider, is_sub_agent=True))
            if not new_agent:
                raise Exception("Error creating agent")
            print(f"\nAgent **{new_agent.name}** added successfully!")
    except Exception as e:
        logger.error(str(e))
    finally:
        sys.exit()

def delete_agent(agent_name_or_index: str):
    from cognitrix.agents import Agent
    if agent_name_or_index:
        agent_deleted1 = Agent.remove({'id': agent_name_or_index})
        agent_deleted2 = Agent.remove({'name': agent_name_or_index})
        if agent_deleted1 or agent_deleted2:
            print(f"\nAgent **{agent_name_or_index}** deleted successfully!")
        else:
            print(f"Agent **{agent_name_or_index}** couldn't be deleted")
    else:
        print("\nError deleting agent")
    sys.exit()

async def manage_agents(args: Namespace):
    try:
        if args.new:
            add_agent()
        elif args.delete:
            agent_id__or_name = args.id or args.name
            if not agent_id__or_name:
                raise Exception('Specify agent name or id to delete')
            delete_agent(agent_id__or_name)
        elif args.list:
            await list_agents()
    except KeyboardInterrupt:
        print()
        sys.exit()
    except Exception as e:
        logger.exception(e)
        sys.exit(1)

# =====================
# Task Management
# =====================
def list_tasks():
    from cognitrix.tasks.base import Task
    tasks = Task.all()
    rows = [[i+1, t.title] for i, t in enumerate(tasks)]
    print_table(rows, ["#", "Task Title"])

# =====================
# Team Management
# =====================
def list_teams():
    from cognitrix.teams.base import Team
    teams = Team.all()
    rows = [[i+1, t.name] for i, t in enumerate(teams)]
    print_table(rows, ["#", "Team Name"])

# =====================
# Tool Management
# =====================
def list_tools(category='all'):
    from cognitrix.tools.base import Tool
    tools = Tool.get_tools_by_category(category)
    rows = [[i+1, t.name, t.category] for i, t in enumerate(tools)]
    print_table(rows, ["#", "Tool Name", "Tool Category"])

def manage_tools(args: Namespace):
    try:
        if args.list:
            list_tools(args.list)
    except KeyboardInterrupt:
        print()
        sys.exit()
    except Exception as e:
        logging.exception(e)
        sys.exit(1)

# =====================
# Provider Management
# =====================
def list_providers():
    from cognitrix.providers import LLM
    providers = LLM.list_llms()
    rows = [[i+1, p.__name__] for i, p in enumerate(providers)]
    print_table(rows, ["#", "Provider"])

# =====================
# Session Management
# =====================
async def list_sessions():
    from cognitrix.sessions.base import Session
    print("\nSaved Sessions:")
    sessions = await Session.list_sessions()
    rows = [[i, s.datetime, s.id] for i, s in enumerate(sessions)]
    print_table(rows, ["#", "Datetime", "Session ID"])

# =====================
# UI Logic (Web/CLI)
# =====================
async def start_web_ui(agent):
    from .api.main import app
    from fastapi import WebSocket
    import uvicorn
    from cognitrix.utils.ws import WebSocketManager
    ws_manager = WebSocketManager(agent)
    @app.middleware("http")
    async def add_middleware_data(request, call_next):
        request.state.agent = agent
        response = await call_next(request)
        return response
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: 'WebSocket'):
        await ws_manager.websocket_endpoint(websocket) # type: ignore
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, forwarded_allow_ips="*")
    server = uvicorn.Server(config)
    await server.serve()

async def prompt_agent(assistant, prompt):
    async for response in assistant.generate(prompt):
        if response.result:
            print(f"\r{response.result}", end='')
    print()

console = Console()

class CognitrixCompleter(Completer):
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

async def initialize(session, agent, stream: bool = False):
    async def _handle_list_agents():
        from cognitrix.agents import Agent
        child_agents = [a for a in await Agent.all() if a.parent_id == agent.id]
        if not child_agents:
            console.print(Panel("No child agents found for the current assistant.", style="bold yellow"))
            return
        table = Table(title="Available Child Agents (under current assistant)", show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim")
        table.add_column("Name", style="bold")
        for i, a in enumerate(child_agents):
            table.add_row(str(i), a.name)
        console.print(table)

    def run_shell_command(command: str):
        try:
            result = subprocess.run(command, capture_output=True, text=True, shell=True)
            if result.returncode == 0:
                output = result.stdout.strip()
                if output:
                    console.print(Panel(output, title="[bold green]Shell Output", border_style="green"))
                else:
                    console.print(Panel("[dim]Command executed successfully, but no output.", title="[bold green]Shell Output", border_style="green"))
                return True
            else:
                return False
        except Exception:
            return False

    # Modern welcome banner
    console.print(Panel(f"[bold cyan]Welcome to Cognitrix AI Shell!\n[white]Type your message or a shell command. Type [bold yellow]q[/bold yellow] to quit.", title=f"[bold blue]Agent: {agent.name}", border_style="cyan"))

    username = getpass.getuser()
    hostname = socket.gethostname()

    builtins = ['cd', 'exit', 'quit', 'q']
    custom_commands = ['add agent', 'list tools', 'list agents', 'show history', '/mcp', '/mcp-tools', '/mcp-tool-info', '/mcp-call', '/mcp-connect', '/mcp-disconnect', '/add', '/delete', '/show']
    session_completer = CognitrixCompleter(builtins, custom_commands)
    prompt_session = PromptSession(completer=session_completer, history=InMemoryHistory())

    while True:
        try:
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
            if query.lower() in ['q', 'quit', 'exit']:
                console.print(Panel("Exiting...", style="bold yellow"))
                break
            # Unified slash command handling
            if query.startswith('/'):
                parts = query[1:].strip().split()
                if not parts:
                    console.print(Panel("Empty command.", style="bold red"))
                    continue
                cmd = parts[0].lower()
                args = parts[1:]
                # /<entity> (list)
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
                        from cognitrix.tools.mcp_client import mcp_list_servers
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
                        continue
                    continue
                # Handle /mcp-tools [server_name] command
                elif cmd == 'mcp-tools':
                    from cognitrix.tools.mcp_client import mcp_list_tools
                    server_name = args[0] if args else None
                    tools = await mcp_list_tools(server_name)
                    if tools and isinstance(tools, list) and len(tools) > 0 and 'error' in tools[0]:
                        console.print(Panel(tools[0]['error'], title=f"MCP Tools{' - ' + server_name if server_name else ''}", border_style="red"))
                    else:
                        if not tools:
                            console.print(Panel("No tools available.", title=f"MCP Tools{' - ' + server_name if server_name else ''}", border_style="yellow"))
                        else:
                            tool_list = []
                            for i, tool in enumerate(tools):
                                tool_name = tool.get('name', str(tool))
                                tool_desc = tool.get('description', 'No description')
                                server = tool.get('server', server_name or 'unknown')
                                tool_list.append(f"[{i+1}] {tool_name} ({server})")
                                if tool_desc and tool_desc != 'No description':
                                    tool_list.append(f"    {tool_desc}")
                            console.print(Panel("\n".join(tool_list), title=f"MCP Tools{' - ' + server_name if server_name else ''}", border_style="green"))
                    continue
                # Handle /mcp-tool-info <tool_name> [server_name] command
                elif cmd == 'mcp-tool-info':
                    if not args:
                        console.print(Panel("Usage: /mcp-tool-info <tool_name> [server_name]", border_style="red"))
                        continue
                    
                    tool_name = args[0]
                    server_name = args[1] if len(args) > 1 else None
                    
                    from cognitrix.tools.mcp_client import mcp_list_tools
                    tools = await mcp_list_tools(server_name)
                    
                    if tools and isinstance(tools, list) and len(tools) > 0 and 'error' not in tools[0]:
                        # Find the specific tool
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
                    continue
                # Handle /mcp-call <tool_name> [server_name] [args...] command
                elif cmd == 'mcp-call':
                    if not args:
                        console.print(Panel("Usage: /mcp-call <tool_name> [server_name] [arg1=value1] [arg2=value2]...", border_style="red"))
                        continue
                    
                    tool_name = args[0]
                    
                    # Parse arguments - check if second arg is a server name or tool argument
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
                            continue
                    
                    from cognitrix.tools.mcp_client import mcp_call_tool
                    result = await mcp_call_tool(tool_name, tool_args, server_name)
                    
                    # Display result in a nice format
                    console.print(Panel(str(result), title=f"Tool Result: {tool_name}", border_style="green"))
                    continue
                # Handle /mcp-connect <server_name> command
                elif cmd == 'mcp-connect':
                    if not args:
                        console.print(Panel("Usage: /mcp-connect <server_name>", border_style="red"))
                        continue
                    
                    server_name = args[0]
                    from cognitrix.tools.mcp_client import mcp_connect_server
                    result = await mcp_connect_server(server_name)
                    console.print(Panel(result, border_style="green" if "Successfully" in result else "red"))
                    continue
                # Handle /mcp-disconnect <server_name> command
                elif cmd == 'mcp-disconnect':
                    if not args:
                        console.print(Panel("Usage: /mcp-disconnect <server_name>", border_style="red"))
                        continue
                    
                    server_name = args[0]
                    from cognitrix.tools.mcp_client import mcp_disconnect_server
                    result = await mcp_disconnect_server(server_name)
                    console.print(Panel(result, border_style="green" if "Successfully" in result else "red"))
                    continue
                # /add <entity> [args]
                elif cmd == 'add' and args:
                    entity = args[0].lower()
                    if entity == 'agent':
                        add_agent()
                    elif entity == 'tool':
                        from cognitrix.tools.misc import create_tool
                        # Prompt for tool details interactively
                        name = input("Tool name: ")
                        description = input("Description: ")
                        category = input("Category: ")
                        function_code = input("Function code (Python): ")
                        result = create_tool(name, description, category, function_code)
                        print(result)
                    elif entity == 'task':
                        from cognitrix.tasks.base import Task
                        title = input("Task title: ")
                        description = input("Task description: ")
                        task = Task(title=title, description=description)
                        task.save()
                        print(f"Task '{title}' added.")
                    elif entity == 'team':
                        from cognitrix.teams.base import Team
                        name = input("Team name: ")
                        team = Team(name=name)
                        team.save()
                        print(f"Team '{name}' added.")
                    elif entity == 'mcp':
                        # Interactive MCP server configuration
                        from cognitrix.tools.mcp_client import mcp_add_server
                        
                        console.print(Panel("Adding new MCP server configuration", title="MCP Server Setup", border_style="blue"))
                        
                        name = input("Server name: ").strip()
                        if not name:
                            console.print(Panel("Server name is required!", border_style="red"))
                            continue
                        
                        description = input("Description (optional): ").strip()
                        
                        console.print("\nTransport types:")
                        console.print("1. STDIO (local command/script)")
                        console.print("2. HTTP (remote API)")
                        console.print("3. SSE (Server-Sent Events)")
                        
                        transport_choice = input("Choose transport type (1-3): ").strip()
                        
                        if transport_choice == "1":
                            command = input("Command (e.g., 'python', 'npx'): ").strip()
                            args_input = input("Arguments (space-separated, optional): ").strip()
                            args = args_input.split() if args_input else []
                            
                            result = await mcp_add_server(
                                name=name,
                                transport="stdio",
                                command=command,
                                args=args,
                                description=description
                            )
                        elif transport_choice in ["2", "3"]:
                            url = input("Server URL: ").strip()
                            if not url:
                                console.print(Panel("URL is required for HTTP/SSE transport!", border_style="red"))
                                continue
                            
                            transport = "http" if transport_choice == "2" else "sse"
                            result = await mcp_add_server(
                                name=name,
                                transport=transport,
                                url=url,
                                description=description
                            )
                        else:
                            console.print(Panel("Invalid transport choice!", border_style="red"))
                            continue
                        
                        console.print(Panel(result, border_style="green" if "Successfully" in result else "red"))
                    else:
                        console.print(Panel(f"Unknown entity for /add: {entity}", style="bold red"))
                    continue
                # /delete <entity> [identifier]
                elif cmd == 'delete' and args:
                    entity = args[0].lower()
                    identifier = args[1] if len(args) > 1 else None
                    if entity == 'agent' and identifier:
                        delete_agent(identifier)
                    elif entity == 'tool' and identifier:
                        from cognitrix.tools.base import Tool
                        tool = Tool.get_by_name(identifier)
                        if tool:
                            tool.delete()
                            print(f"Tool '{identifier}' deleted.")
                        else:
                            print(f"Tool '{identifier}' not found.")
                    elif entity == 'task' and identifier:
                        from cognitrix.tasks.base import Task
                        task = Task.find_one({'title': identifier})
                        if task:
                            task.delete()
                            print(f"Task '{identifier}' deleted.")
                        else:
                            print(f"Task '{identifier}' not found.")
                    elif entity == 'team' and identifier:
                        from cognitrix.teams.base import Team
                        team = Team.find_one({'name': identifier})
                        if team:
                            team.delete()
                            print(f"Team '{identifier}' deleted.")
                        else:
                            print(f"Team '{identifier}' not found.")
                    elif entity == 'mcp' and identifier:
                        from cognitrix.tools.mcp_client import mcp_remove_server
                        result = await mcp_remove_server(identifier)
                        console.print(Panel(result, border_style="green" if "Successfully" in result else "red"))
                    else:
                        console.print(Panel(f"Unknown or missing identifier for /delete {entity}", style="bold red"))
                    continue
                # /show <entity> [identifier]
                elif cmd == 'show' and args:
                    entity = args[0].lower()
                    identifier = args[1] if len(args) > 1 else None
                    if entity == 'agent' and identifier:
                        from cognitrix.agents import Agent
                        agent_obj = await Agent.find_one({'name': identifier})
                        if agent_obj:
                            print(agent_obj)
                        else:
                            print(f"Agent '{identifier}' not found.")
                    elif entity == 'tool' and identifier:
                        from cognitrix.tools.base import Tool
                        tool = Tool.get_by_name(identifier)
                        if tool:
                            print(tool)
                        else:
                            print(f"Tool '{identifier}' not found.")
                    elif entity == 'task' and identifier:
                        from cognitrix.tasks.base import Task
                        task = Task.find_one({'title': identifier})
                        if task:
                            print(task)
                        else:
                            print(f"Task '{identifier}' not found.")
                    elif entity == 'team' and identifier:
                        from cognitrix.teams.base import Team
                        team = Team.find_one({'name': identifier})
                        if team:
                            print(team)
                        else:
                            print(f"Team '{identifier}' not found.")
                    elif entity == 'mcp' and identifier:
                        from cognitrix.tools.mcp_client import mcp_list_servers, mcp_test_server
                        servers = await mcp_list_servers()
                        server = next((s for s in servers if s.get('name') == identifier), None)
                        if server:
                            # Show server details
                            details = []
                            details.append(f"Name: {server.get('name', 'Unknown')}")
                            details.append(f"Transport: {server.get('transport', 'Unknown')}")
                            details.append(f"Status: {'🟢 Connected' if server.get('connected') else '🔴 Disconnected'}")
                            details.append(f"Enabled: {'Yes' if server.get('enabled', True) else 'No'}")
                            
                            if server.get('description'):
                                details.append(f"Description: {server['description']}")
                            
                            if server.get('command'):
                                details.append(f"Command: {server['command']}")
                                if server.get('args'):
                                    details.append(f"Arguments: {' '.join(server['args'])}")
                            
                            if server.get('url'):
                                details.append(f"URL: {server['url']}")
                            
                            console.print(Panel("\n".join(details), title=f"MCP Server: {identifier}", border_style="blue"))
                            
                            # Test connection
                            console.print("\n[bold yellow]Testing connection...[/bold yellow]")
                            test_result = await mcp_test_server(identifier)
                            if test_result.get('success'):
                                console.print(Panel(f"✅ {test_result.get('message', 'Connection successful')}", border_style="green"))
                            else:
                                console.print(Panel(f"❌ {test_result.get('error', 'Connection failed')}", border_style="red"))
                        else:
                            console.print(Panel(f"MCP server '{identifier}' not found.", border_style="red"))
                    else:
                        console.print(Panel(f"Unknown or missing identifier for /show {entity}", style="bold red"))
                    continue
                else:
                    console.print(Panel(f"Unknown or malformed slash command: {query}", style="bold red"))
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
            # Legacy command handlers (for backward compatibility)
            command_handlers = {
                'add agent': add_agent,
                'list tools': lambda: console.print(Panel("\n".join(f"[{i}] {tool.name}" for i, tool in enumerate(agent.tools)), title="Available Tools", border_style="blue")),
                'list agents': _handle_list_agents,
                'show history': lambda: console.print(Panel("\n".join(f"[{chat['role']}]: {chat['message']}" for chat in session.chat), title="Chat History", border_style="magenta"))
            }
            if query in command_handlers:
                handler = command_handlers[query]
                if asyncio.iscoroutinefunction(handler):
                    await handler()
                else:
                    handler()
                continue
            shell_success = run_shell_command(query)
            if shell_success:
                continue
            console.print(Panel(f"[bold blue]Sending to AI...", border_style="blue"))
            await session(query, agent, stream=stream)
            console.print()
        except KeyboardInterrupt:
            console.print(Panel("Exiting...", style="bold yellow"))
            break
        except Exception as e:
            logger.exception(e)
            console.print(Panel(f"[bold red]Error: {e}", border_style="red"))
            break

# =====================
# Main Start Logic
# =====================
async def start(args: Namespace):
    from cognitrix.providers import LLM
    from cognitrix.agents import Agent
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.base import Task
    from cognitrix.tools.base import Tool
    from cognitrix.teams.base import Team
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
    from cognitrix.config import VERSION, run_configure
    run_configure()
    try:
        if args.providers:
            list_providers()
            sys.exit()
        elif args.agents:
            await list_agents()
            sys.exit()
        elif args.tasks:
            list_tasks()
            sys.exit()
        elif args.teams:
            list_teams()
            sys.exit()
        elif args.sessions:
            await list_sessions()
            sys.exit()
        assistant = None
        if args.agent:
            assistant = await Agent.find_one({'name': args.agent})
        if not assistant:
            assistant = await Agent.create_agent(name=args.agent, provider=args.provider, model=args.model, temperature=args.temperature, system_prompt=ASSISTANT_SYSTEM_PROMPT)
        if not assistant:
            raise Exception("Agent not found")
        if args.provider:
            provider = LLM.load_llm(args.provider)
            if provider:
                provider.provider = args.provider
                assistant.llm = provider
        if args.model and (assistant.llm.model.lower() != args.model.lower()):
            assistant.llm.model = args.model
        if len(args.load_tools) and not len(assistant.tools):
            tools = []
            for cat in args.load_tools:
                tools = Tool.get_tools_by_category(cat.strip().lower())
                tool_by_name = Tool.get_by_name(cat.strip().lower())
                if tool_by_name:
                    tools.append(tool_by_name)
            assistant.tools = tools
        session = await Session.get_by_agent_id(assistant.id)
        if not session:
            session = Session(agent_id=assistant.id)
        if args.clear_history:
            session.chat = []
            await session.save()
        assistant.verbose = args.verbose
        await assistant.save()
        if args.generate:
            await session(args.generate, assistant, stream=args.stream)
        elif args.ui.lower() == 'web':
            await start_web_ui(assistant)
        else:
            await initialize(session, assistant, stream=args.stream)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(1)
    except Exception as e:
        logging.exception(e)
        sys.exit(1)

# =====================
# Argument Parsing
# =====================
def get_arguments():
    from cognitrix.config import VERSION
    parser = argparse.ArgumentParser(description="Build and run AI agents on your computer")
    subparsers = parser.add_subparsers()
    # Agents
    agents_parser = subparsers.add_parser('agents', help="Manage agents")
    agents_parser.add_argument("name", type=str, nargs="?", help="Name of an agent to manage (details|update|remove)")
    agents_parser.add_argument('--new','--create', action='store_true', help='Create a new agent')
    agents_parser.add_argument('-l', '--list', action='store_true', help='List all saved agents')
    agents_parser.add_argument('--update', action='store_true', help='Update an agent')
    agents_parser.add_argument('--delete', action='store_true', help='Delete an agent')
    agents_parser.add_argument('--id', nargs='?', help='Specify agent id to update or delete')
    agents_parser.set_defaults(func=manage_agents)
    # Tools
    tools_parser = subparsers.add_parser('tools', help="Manage tools")
    tools_parser.add_argument('-l', '--list', type=str, default='all', nargs='?', choices=['all', 'general', 'system', 'web'], help='List tools by category')
    tools_parser.set_defaults(func=manage_tools)
    # Main args
    parser.add_argument('--name', type=str, default='Assistant', help='Set name of agent')
    parser.add_argument('--provider', default='groq', help='Set llm provider to use')
    parser.add_argument('--providers', action='store_true', help='Get a list of all supported providers')
    parser.add_argument('--agents', action='store_true', help='List all saved agents')
    parser.add_argument('--tasks', action='store_true', help='List all saved tasks')
    parser.add_argument('--teams', action='store_true', help='List all saved teams')
    parser.add_argument('--ui', default='cli', help='Determine preferred user interface')
    parser.add_argument('--agent', type=str, default='Assistant', help='Set which saved agent to use')
    parser.add_argument('--load-tools', type=lambda s: [i for i in s.split(',')], default='all', help='Add tools by categories to agent')
    parser.add_argument('--model', type=str, default='', help='Specify model or model_url to use')
    parser.add_argument('--api-key', type=str, default='', help='Set api key of selected llm')
    parser.add_argument('--api-base', type=str, default='', help='Set api base of selected llm. Set if using local llm.')
    parser.add_argument('--temperature', type=float, default=0.1, help='Set temperature of model')
    parser.add_argument('--system-prompt', type=str_or_file, default='', help='Set system prompt of model. Can be a string or a text file path')
    parser.add_argument('--prompt-template', type=str_or_file, default='', help='Set prompt template of model. Can be a string or a text file path')
    parser.add_argument('--generate', type=str, default='', help='Prompt the agent to generate text and then exit after printing out the response.')
    parser.add_argument('--audio', action='store_true', help='Get input from microphone')
    parser.add_argument('--stream', action='store_true', help='Enable response stream')
    parser.add_argument('--session', type=str, default="", help='Load saved session')
    parser.add_argument('--clear-history', action='store_true', default="", help='Clear agent history')
    parser.add_argument('--sessions', action='store_true', help='Get a list of all saved sessions')
    parser.add_argument('--verbose', action='store_true', help='Set verbose mode')
    parser.add_argument('-v','--version', action='version', version=f'%(prog)s {VERSION}')
    parser.set_defaults(func=start)
    return parser.parse_args()

# =====================
# Celery Worker (Optional)
# =====================
def start_worker():
    import subprocess
    try:
        celery_args = ['celery', '-A', 'cognitrix.celery_worker', 'worker', '--loglevel=info']
        worker_process = subprocess.Popen(celery_args)
        print("Celery worker started.")
        return worker_process
    except Exception as e:
        print(f"Error starting Celery worker: {e}")
        return None

# =====================
# Main Entrypoint
# =====================
def main():
    try:
        args = get_arguments()
        if asyncio.iscoroutinefunction(args.func):
            asyncio.run(args.func(args))
        else:
            args.func(args)
    except Exception as e:
        logging.exception(e)
        sys.exit(1)

if __name__ == "__main__":
    main()
