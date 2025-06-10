"""
Interactive shell implementation with command completion and MCP support.
"""
import os
import sys
import getpass
import socket
import subprocess
from typing import Any, List
import re

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML

from cognitrix.agents.base import Agent
from cognitrix.sessions.base import Session

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


async def handle_slash_command(query: str, agent, session) -> bool | tuple:
    """Handle slash commands and return True if command was processed."""
    if not query.startswith('/'):
        return False
        
    parts = query[1:].strip().split()
    if not parts:
        console.print(Panel("[bold red]Empty command.[/bold red]", title="[red]Error[/red]", border_style="red"))
        return True
        
    cmd = parts[0].lower()
    args: List[Any] = parts[1:]
    
    # Entity listing commands
    if cmd in ['tools', 'agents', 'tasks', 'teams', 'mcp']:
        if cmd == 'tools':
            # List available tools for the current agent
            console.print(Panel("\n".join(f"[bold cyan][{i}][/bold cyan] [white]{tool.name}[/white]" for i, tool in enumerate(agent.tools)), 
                               title="[bold blue]🛠️  Available Tools[/bold blue]", border_style="blue"))
        elif cmd == 'agents':
            await list_agents()
        elif cmd == 'tasks':
            list_tasks()
        elif cmd == 'teams':
            list_teams()
        elif cmd == 'mcp':
            await handle_mcp_list()
        return True
    
    # Agent management commands
    if cmd == 'add' and args:
        entity = args[0].lower()
        if entity == 'agent':
            from cognitrix.prompts.generator import agent_generator
            
            console.print(Panel("[bold cyan]Provide a complete description of the new agent.[/bold cyan]\n\n[white]Include the following details:[/white]\n[bold yellow]•[/bold yellow] [cyan]Name[/cyan] of the agent\n[bold yellow]•[/bold yellow] [cyan]Description[/cyan] of the agent\n[bold yellow]•[/bold yellow] [cyan]Role[/cyan] of the agent\n[bold yellow]•[/bold yellow] [cyan]Tools[/cyan] that the agent will use", 
                               title="[bold blue]🤖 Create New Agent[/bold blue]", border_style="blue"))
            description = input("📝 Description: ")
            old_system_prompt = agent.system_prompt
            agent.system_prompt = agent_generator
            await session(description, agent, stream=False)
            agent.system_prompt = old_system_prompt
        else:
            console.print(Panel(f"[bold red]Unknown entity type:[/bold red] [yellow]{entity}[/yellow]", 
                               title="[red]Entity Error[/red]", border_style="red"))
        return True
    
    # History command
    if cmd == 'history':
        console.print(Panel("\n".join(f"[bold cyan][{chat['role']}][/bold cyan]: [white]{chat['message']}[/white]" for chat in session.chat), 
                           title="[bold magenta]💬 Chat History[/bold magenta]", border_style="magenta"))
        return True
    
    # Agent switching command
    if cmd == 'switch':
        if not args:
            # List available agents for switching
            from cognitrix.agents.base import Agent
            agents = await Agent.all()
            if agents:
                console.print(Panel("\n".join(f"[bold cyan][{i}][/bold cyan] [white]{a.name}[/white]" for i, a in enumerate(agents)), 
                                   title="[bold blue]🔄 Available Agents[/bold blue]", border_style="blue"))
                console.print(Panel("[bold yellow]Usage:[/bold yellow] [cyan]/switch <agent_name_or_index>[/cyan]", 
                                   title="[yellow]How to Switch[/yellow]", border_style="yellow"))
            else:
                console.print(Panel("[bold red]No agents available to switch to.[/bold red]", 
                                   title="[red]No Agents[/red]", border_style="red"))
        else:
            # Switch to specified agent
            agent_identifier = args[0]
            from cognitrix.agents.base import Agent
            from cognitrix.sessions.base import Session
            
            # Try to find agent by name first, then by index
            target_agent = await Agent.find_one({'name': agent_identifier})
            
            if not target_agent:
                # Try by index
                try:
                    agent_index = int(agent_identifier)
                    agents = await Agent.all()
                    if 0 <= agent_index < len(agents):
                        target_agent = agents[agent_index]
                except (ValueError, IndexError):
                    pass
            
            if target_agent:
                # Get/create session for new agent
                new_session = await Session.get_by_agent_id(target_agent.id)
                if not new_session:
                    new_session = Session(agent_id=target_agent.id)
                    await new_session.save()
                
                console.print(Panel(f"[bold green]✅ Switched to agent:[/bold green] [bold cyan]{target_agent.name}[/bold cyan]", 
                                   title="[green]Agent Switched[/green]", border_style="green"))
                
                # Return the new agent and session
                return (target_agent, new_session)
            else:
                console.print(Panel(f"[bold red]Agent not found:[/bold red] [yellow]{agent_identifier}[/yellow]", 
                                   title="[red]Switch Failed[/red]", border_style="red"))
        return True

    # Help command
    if cmd == 'help':
        help_text = """[bold green]Available Commands:[/bold green]

[bold cyan]Entity Management:[/bold cyan]
[yellow]• /add agent[/yellow] - Create a new agent
[yellow]• /agents[/yellow] - List all agents
[yellow]• /switch [agent_name_or_index][/yellow] - Switch to another agent
[yellow]• /tools[/yellow] - List available tools
[yellow]• /tasks[/yellow] - List all tasks
[yellow]• /teams[/yellow] - List all teams

[bold cyan]MCP Commands:[/bold cyan]
[yellow]• /mcp[/yellow] - List MCP servers
[yellow]• /mcp-tools [server_name][/yellow] - List MCP tools
[yellow]• /mcp-tool-info <tool_name> [server_name][/yellow] - Get tool details
[yellow]• /mcp-call <tool_name> [server_name] [args][/yellow] - Call MCP tool
[yellow]• /mcp-connect <server_name>[/yellow] - Connect to MCP server
[yellow]• /mcp-disconnect <server_name>[/yellow] - Disconnect from MCP server

[bold cyan]Session Commands:[/bold cyan]
[yellow]• /history[/yellow] - Show chat history
[yellow]• /help[/yellow] - Show this help message
[yellow]• /clear[/yellow] - Clear the screen

[bold cyan]System Commands:[/bold cyan]
[yellow]• cd <path>[/yellow] - Change directory
[yellow]• q, quit, exit[/yellow] - Exit the shell"""
        
        console.print(Panel(help_text, title="[bold blue]📚 Cognitrix Shell Help[/bold blue]", border_style="blue"))
        return True
    
    # Clear screen command
    if cmd == 'clear':
        import os
        os.system('cls' if os.name == 'nt' else 'clear')
        # Re-display welcome banner after clearing
        console.print(Panel(
            f"[bold cyan]🚀 Welcome to Cognitrix AI Shell![/bold cyan]\n\n[white]Type your message or a shell command.[/white]\n[bold yellow]💡 Tip:[/bold yellow] [dim]Type [bold yellow]/help[/bold yellow] for available commands[/dim]",
            title=f"[bold blue]🤖 Agent: {agent.name}[/bold blue]",
            border_style="cyan"
        ))
        return True
    
    # MCP-specific commands
    if cmd.startswith('mcp-'):
        # Remove agent and session from args for MCP commands
        await handle_mcp_command(cmd, args)
        return True
    
    # Other slash commands
    if cmd in ['delete', 'show']:
        await handle_entity_command(cmd, args + [agent, session])
        return True
        
    console.print(Panel(f"[bold red]Unknown slash command:[/bold red] [yellow]{query}[/yellow]\n[dim]Type [bold yellow]/help[/bold yellow] for available commands[/dim]", 
                       title="[red]Command Error[/red]", border_style="red"))
    return True


async def handle_mcp_list():
    """Handle MCP server listing."""
    from cognitrix.mcp import mcp_list_servers
    servers = await mcp_list_servers()
    
    if servers and isinstance(servers, list) and len(servers) > 0 and 'error' in servers[0]:
        console.print(Panel(f"[bold red]{servers[0]['error']}[/bold red]", 
                           title="[red]MCP Servers Error[/red]", border_style="red"))
    else:
        if not servers:
            console.print(Panel("[bold yellow]No MCP servers configured.[/bold yellow]", 
                               title="[yellow]MCP Servers[/yellow]", border_style="yellow"))
        else:
            server_list = []
            for i, server in enumerate(servers):
                status = "[bold green]🟢 Connected[/bold green]" if server.get('connected') else "[bold red]🔴 Disconnected[/bold red]"
                transport = server.get('transport', 'unknown')
                name = server.get('name', 'unnamed')
                description = server.get('description', 'No description')
                server_list.append(f"[bold cyan][{i+1}][/bold cyan] [bold white]{name}[/bold white] [dim]({transport})[/dim] - {status}")
                if description and description != 'No description':
                    server_list.append(f"    [dim italic]{description}[/dim italic]")
            console.print(Panel("\n".join(server_list), 
                               title="[bold blue]🔧 MCP Servers[/bold blue]", border_style="blue"))


async def handle_mcp_command(cmd: str, args: List[str]):
    """Handle MCP-specific commands."""
    if cmd == 'mcp-tools':
        from cognitrix.mcp import mcp_list_tools
        server_name = args[0] if args else None
        tools = await mcp_list_tools(server_name)
        
        if tools and isinstance(tools, list) and len(tools) > 0 and 'error' in tools[0]:
            console.print(Panel(f"[bold red]{tools[0]['error']}[/bold red]", 
                               title=f"[red]MCP Tools Error{' - ' + server_name if server_name else ''}[/red]", 
                               border_style="red"))
        else:
            if not tools:
                console.print(Panel("[bold yellow]No tools available.[/bold yellow]", 
                                   title=f"[yellow]MCP Tools{' - ' + server_name if server_name else ''}[/yellow]", 
                                   border_style="yellow"))
            else:
                tool_list = []
                for i, tool in enumerate(tools):
                    tool_name = tool.get('name', str(tool))
                    tool_desc = tool.get('description', 'No description')
                    server = tool.get('server', server_name or 'unknown')
                    tool_list.append(f"[bold cyan][{i+1}][/bold cyan] [bold white]{tool_name}[/bold white] [dim]({server})[/dim]")
                    if tool_desc and tool_desc != 'No description':
                        tool_list.append(f"    [dim italic]{tool_desc}[/dim italic]")
                console.print(Panel("\n".join(tool_list), 
                                   title=f"[bold green]🛠️  MCP Tools{' - ' + server_name if server_name else ''}[/bold green]", 
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
        console.print(Panel("[bold red]Usage:[/bold red] [yellow]/mcp-tool-info <tool_name> [server_name][/yellow]", 
                           title="[red]Usage Error[/red]", border_style="red"))
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
            details.append(f"[bold cyan]Name:[/bold cyan] [white]{target_tool.get('name', 'Unknown')}[/white]")
            details.append(f"[bold cyan]Server:[/bold cyan] [white]{target_tool.get('server', 'Unknown')}[/white]")
            details.append(f"[bold cyan]Description:[/bold cyan] [white]{target_tool.get('description', 'No description')}[/white]")
            
            # Show input schema
            schema = target_tool.get('input_schema', {})
            if schema:
                details.append("\n[bold yellow]Parameters:[/bold yellow]")
                properties = schema.get('properties', {})
                required = schema.get('required', [])
                
                for prop_name, prop_info in properties.items():
                    prop_type = prop_info.get('type', 'unknown')
                    prop_desc = prop_info.get('description', 'No description')
                    is_required = prop_name in required
                    req_str = " [bold red](required)[/bold red]" if is_required else " [dim](optional)[/dim]"
                    details.append(f"  [bold green]•[/bold green] [cyan]{prop_name}[/cyan] [dim]({prop_type})[/dim]{req_str}: [white]{prop_desc}[/white]")
            else:
                details.append("\n[bold yellow]Parameters:[/bold yellow] [dim]None[/dim]")
            
            console.print(Panel("\n".join(details), 
                               title=f"[bold blue]🔍 Tool Info: {tool_name}[/bold blue]", 
                               border_style="blue"))
        else:
            console.print(Panel(f"[bold red]Tool '[yellow]{tool_name}[/yellow]' not found.[/bold red]", 
                               title="[red]Tool Not Found[/red]", border_style="red"))
    else:
        error_msg = tools[0].get('error', 'Failed to list tools') if tools else 'No tools available'
        console.print(Panel(f"[bold red]{error_msg}[/bold red]", 
                           title="[red]Error[/red]", border_style="red"))


async def handle_mcp_tool_call(args: List[str]):
    """Handle MCP tool call command."""
    if not args:
        console.print(Panel("[bold red]Usage:[/bold red] [yellow]/mcp-call <tool_name> [server_name] [arg1=value1] [arg2=value2]...[/yellow]", 
                           title="[red]Usage Error[/red]", border_style="red"))
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
            console.print(Panel(f"[bold red]Invalid argument format:[/bold red] [yellow]{arg}[/yellow]. [white]Use key=value format.[/white]", 
                               title="[red]Argument Error[/red]", border_style="red"))
            return
    
    from cognitrix.mcp import mcp_call_tool
    result = await mcp_call_tool(tool_name, tool_args, server_name)
    
    console.print(Panel(f"[white]{str(result)}[/white]", 
                       title=f"[bold green]✅ Tool Result: {tool_name}[/bold green]", 
                       border_style="green"))


async def handle_mcp_connection(cmd: str, args: List[str]):
    """Handle MCP connection commands."""
    from cognitrix.mcp import mcp_connect_server, mcp_disconnect_server
    
    if cmd == 'mcp-connect':
        if not args:
            console.print(Panel("[bold red]Usage:[/bold red] [yellow]/mcp-connect <server_name>[/yellow]", 
                               title="[red]Usage Error[/red]", border_style="red"))
            return
        result = await mcp_connect_server(args[0])
        if result.get('success'):
            console.print(Panel(f"[bold green]✅ {result.get('message', 'Connected successfully')}[/bold green]", 
                               title="[green]Connection Success[/green]", border_style="green"))
        else:
            console.print(Panel(f"[bold red]❌ {result.get('error', 'Connection failed')}[/bold red]", 
                               title="[red]Connection Failed[/red]", border_style="red"))
    
    elif cmd == 'mcp-disconnect':
        if not args:
            console.print(Panel("[bold red]Usage:[/bold red] [yellow]/mcp-disconnect <server_name>[/yellow]", 
                               title="[red]Usage Error[/red]", border_style="red"))
            return
        result = await mcp_disconnect_server(args[0])
        if result.get('success'):
            console.print(Panel(f"[bold green]✅ {result.get('message', 'Disconnected successfully')}[/bold green]", 
                               title="[green]Disconnection Success[/green]", border_style="green"))
        else:
            console.print(Panel(f"[bold red]❌ {result.get('error', 'Disconnection failed')}[/bold red]", 
                               title="[red]Disconnection Failed[/red]", border_style="red"))


async def handle_entity_command(cmd: str, args: List[Any]):
    """Handle entity management commands (delete, show)."""
    if cmd == 'show' and len(args) >= 2:
        entity = args[0].lower()
        identifier = args[1]
        
        if entity == 'mcp':
            from cognitrix.mcp import mcp_health_check, mcp_get_server_info
            try:
                # Get server info first
                server_info = await mcp_get_server_info(identifier)
                if 'error' not in server_info:
                    console.print(Panel(f"[bold green]✅ Server '{identifier}' found[/bold green]\n[white]Info: {server_info}[/white]", 
                                       title="[green]MCP Server Info[/green]", border_style="green"))
                else:
                    console.print(Panel(f"[bold red]❌ {server_info.get('error', 'Server not found')}[/bold red]", 
                                       title="[red]MCP Server Error[/red]", border_style="red"))
            except Exception as e:
                console.print(Panel(f"[bold red]❌ Error checking server: {e}[/bold red]", 
                               title="[red]MCP Server Error[/red]", border_style="red"))
        else:
            console.print(Panel(f"[bold red]Unknown entity type:[/bold red] [yellow]{entity}[/yellow]", 
                               title="[red]Entity Error[/red]", border_style="red"))
    elif cmd == 'delete':
        console.print(Panel("[bold yellow]Delete functionality not yet implemented.[/bold yellow]", 
                           title="[yellow]Coming Soon[/yellow]", border_style="yellow"))
    else:
        console.print(Panel(f"[bold red]Unknown or malformed command:[/bold red] [yellow]/{cmd} {' '.join(str(arg) for arg in args)}[/yellow]", 
                           title="[red]Command Error[/red]", border_style="red"))





async def initialize_shell(session, agent, stream: bool = False):
    """Initialize and run the interactive shell."""

    # Welcome banner
    console.print(Panel(
        f"[bold cyan]🚀 Welcome to Cognitrix AI Shell![/bold cyan]\n\n[white]Type your message or a shell command.[/white]\n[bold yellow]💡 Tip:[/bold yellow] [dim]Type [bold yellow]/help[/bold yellow] for available commands or [bold yellow]q[/bold yellow] to quit[/dim]\n\n[bold green]Quick Commands:[/bold green]\n[cyan]• /help[/cyan] - Show all commands\n[cyan]• /tools[/cyan] - List available tools\n[cyan]• /add agent[/cyan] - Create a new agent\n[cyan]• /history[/cyan] - Show chat history",
        title=f"[bold blue]🤖 Agent: {agent.name}[/bold blue]",
        border_style="cyan"
    ))

    username = getpass.getuser()
    hostname = socket.gethostname()

    builtins = ['cd', 'exit', 'quit', 'q']
    custom_commands = [
        '/help', '/clear', '/history', '/tools', '/agents', '/tasks', '/teams',
        '/add', '/delete', '/show', '/switch', '/mcp', '/mcp-tools', '/mcp-tool-info', 
        '/mcp-call', '/mcp-connect', '/mcp-disconnect'
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
                
            prompt_str = HTML(f'<ansigreen><b>{username}@{hostname}</b></ansigreen>:<ansiblue><b>{display_cwd}</b></ansiblue><ansiyellow>$</ansiyellow> ')
            query = await prompt_session.prompt_async(prompt_str)
            query = query.strip()
            
            if not query:
                continue
                
            # Check for exit commands
            if query.lower() in ['q', 'quit', 'exit']:
                console.print(Panel("[bold yellow]👋 Goodbye! Thanks for using Cognitrix![/bold yellow]", 
                                   title="[yellow]Exiting[/yellow]", border_style="yellow"))
                break
            
            # Handle slash commands
            slash_result = await handle_slash_command(query, agent, session)
            if slash_result is True:
                continue
            elif isinstance(slash_result, tuple):
                # Agent switch occurred, update agent and session
                agent, session = slash_result
                # Re-display welcome banner with new agent
                console.print(Panel(
                    f"[bold cyan]🚀 Welcome to Cognitrix AI Shell![/bold cyan]\n\n[white]Type your message or a shell command.[/white]\n[bold yellow]💡 Tip:[/bold yellow] [dim]Type [bold yellow]/help[/bold yellow] for available commands or [bold yellow]q[/bold yellow] to quit[/dim]\n\n[bold green]Quick Commands:[/bold green]\n[cyan]• /help[/cyan] - Show all commands\n[cyan]• /tools[/cyan] - List available tools\n[cyan]• /add agent[/cyan] - Create a new agent\n[cyan]• /history[/cyan] - Show chat history",
                    title=f"[bold blue]🤖 Agent: {agent.name}[/bold blue]",
                    border_style="cyan"
                ))
                continue
            
            # Handle built-in shell commands
            if query.startswith('cd'):
                parts = query.split(maxsplit=1)
                path = parts[1].strip() if len(parts) > 1 else os.path.expanduser('~')
                try:
                    os.chdir(path)
                    console.print(Panel(f"[bold green]📁 Changed directory to[/bold green] [bold blue]{os.getcwd()}[/bold blue]", 
                                       title="[green]Directory Changed[/green]", border_style="green"))
                except Exception as e:
                    console.print(Panel(f"[bold red]❌ cd: {e}[/bold red]", 
                                       title="[red]Directory Error[/red]", border_style="red"))
                continue
            

            
            # Try to execute as shell command
            if run_shell_command(query):
                continue
            
            # Send to AI agent
            console.print(Panel(f"[bold blue]🤖 Sending to AI...[/bold blue]", 
                               title="[blue]Processing[/blue]", border_style="blue"))
            await session(query, agent, stream=stream)
            console.print()
            
        except KeyboardInterrupt:
            console.print(Panel("[bold yellow]👋 Interrupted! Goodbye![/bold yellow]", 
                               title="[yellow]Exiting[/yellow]", border_style="yellow"))
            break
        except Exception as e:
            import logging
            logging.exception(e)
            console.print(Panel(f"[bold red]💥 Error: {e}[/bold red]", 
                               title="[red]System Error[/red]", border_style="red"))
            break 