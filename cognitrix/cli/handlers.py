"""
Command handlers for managing different entity types.
"""
import asyncio
import logging
import sys
from argparse import Namespace

from rich import print

from .utils import print_json, print_table

logger = logging.getLogger('cognitrix.log')


# =====================
# Agent Management
# =====================
def _agent_row(a) -> dict:
    llm = getattr(a, 'llm', None)
    return {
        'id': str(a.id),
        'name': a.name,
        'provider': str(getattr(llm, 'provider', '') or ''),
        'model': str(getattr(llm, 'model', '') or ''),
        'tools': len(getattr(a, 'tools', None) or []),
        'description': getattr(a, 'description', '') or '',
    }


async def list_agents(as_json: bool = False):
    """List all available agents."""
    from cognitrix.agents import Agent
    agents = await Agent.all()
    rows = [_agent_row(a) for a in agents]
    if as_json:
        print_json(rows)
        return
    print_table([[i + 1, r['name'], r['provider'], r['model'], r['tools'], r['id']] for i, r in enumerate(rows)],
                ["#", "Agent Name", "Provider", "Model", "Tools", "ID"])


async def find_agent(name_or_id: str):
    """An agent by id, by name (any case), or by its number in the list."""
    from cognitrix.agents import Agent
    if not name_or_id:
        return None
    agent = await Agent.find_one({'id': name_or_id}) or await Agent.find_one({'name': name_or_id})
    if agent:
        return agent
    agents = await Agent.all()
    if name_or_id.isdigit() and 1 <= int(name_or_id) <= len(agents):
        return agents[int(name_or_id) - 1]
    wanted = name_or_id.strip().lower()
    return next((a for a in agents if str(a.name).lower() == wanted), None)


async def show_agent(name_or_id: str, as_json: bool = False) -> bool:
    agent = await find_agent(name_or_id)
    if not agent:
        print(f"No agent called {name_or_id}.", file=sys.stderr)
        return False
    row = _agent_row(agent)
    row['tool_names'] = [t.name for t in agent.tools or []]
    row['mcp_servers'] = list(getattr(agent, 'mcp_servers', None) or [])
    row['system_prompt'] = agent.system_prompt
    if as_json:
        print_json(row)
        return True
    for key in ('name', 'id', 'provider', 'model', 'description'):
        print(f"{key.replace('_', ' ').title()}: {row[key]}")
    print(f"Tools ({row['tools']}): {', '.join(row['tool_names'])}")
    if row['mcp_servers']:
        print(f"MCP servers: {', '.join(row['mcp_servers'])}")
    print(f"\nSystem prompt:\n{row['system_prompt']}")
    return True


async def add_agent(args: Namespace | None = None):
    """Create an agent. Asks for what wasn't given as a flag."""
    from cognitrix.agents import Agent
    from cognitrix.providers import LLM

    def ask(question: str) -> str:
        if not sys.stdin.isatty():
            return ''
        return input(question).strip()

    name = (getattr(args, 'name', None) or '') or ask("Enter agent name: ")
    if not name:
        raise ValueError('Give the agent a name: cognitrix agents NAME --new')
    provider_id = getattr(args, 'provider', '') or ''
    while not provider_id:
        provider_id = ask("\nEnter provider name (e.g. openai, anthropic, google, openrouter, ollama, groq): ")
        if not provider_id and not sys.stdin.isatty():
            raise ValueError('Pick a provider with --provider')
    model = getattr(args, 'model', '') or ''
    if not model and sys.stdin.isatty():
        llm = LLM.load_llm(provider_id)
        if llm:
            model = ask(f"\nEnter model name [{llm.model}]: ") or llm.model
    system_prompt = getattr(args, 'system_prompt', '') or ''
    if not system_prompt and sys.stdin.isatty():
        system_prompt = ask("\nEnter agent system prompt (blank for the default): ")
    tools = getattr(args, 'tools', None)
    new_agent = await Agent.create_agent(
        name, system_prompt=system_prompt, provider=provider_id,
        model=model or '', temperature=None,
        tools=[t.strip() for t in tools.split(',') if t.strip()] if tools else ['all'],
    )
    if not new_agent:
        raise ValueError(f"Couldn't create the agent. Is a key saved for {provider_id}? (cognitrix keys set {provider_id})")
    description = getattr(args, 'description', '') or ''
    if description:
        new_agent.description = description
        await new_agent.save()
    print(f"Agent {new_agent.name} created ({new_agent.id}).")
    return new_agent


async def update_agent(name_or_id: str, args: Namespace) -> bool:
    from cognitrix.providers import LLM
    from cognitrix.tools.base import ToolManager

    agent = await find_agent(name_or_id)
    if not agent:
        print(f"No agent called {name_or_id}.", file=sys.stderr)
        return False
    changed = []
    if getattr(args, 'provider', ''):
        llm = LLM.load_llm({'provider': args.provider, **({'model': args.model} if args.model else {})})
        if not llm:
            print(f"Couldn't load provider {args.provider}. Is a key saved for it?", file=sys.stderr)
            return False
        agent.llm = llm
        changed.append('provider')
    if getattr(args, 'model', ''):
        agent.llm.model = args.model
        changed.append('model')
    if getattr(args, 'system_prompt', ''):
        agent.system_prompt = args.system_prompt
        changed.append('system prompt')
    if getattr(args, 'description', ''):
        agent.description = args.description
        changed.append('description')
    if getattr(args, 'tools', None):
        names = [t.strip() for t in args.tools.split(',') if t.strip()]
        tools = ToolManager.list_all_tools() if 'all' in names else []
        if not tools:
            for n in names:
                tools.extend(ToolManager.get_tools_by_category(n.lower()))
                one = ToolManager.get_by_name(n.lower())
                if one:
                    tools.append(one)
        agent.tools = tools
        changed.append('tools')
    if getattr(args, 'rename', ''):
        agent.name = args.rename
        changed.append('name')
    if not changed:
        print("Nothing to change. Use --provider, --model, --system-prompt, --description, --tools or --rename.",
              file=sys.stderr)
        return False
    await agent.save()
    print(f"Updated {agent.name}: {', '.join(changed)}.")
    return True


async def delete_agent(agent_name_or_id: str) -> bool:
    """Delete an agent by name, id or list number."""
    from cognitrix.agents import Agent
    agent = await find_agent(agent_name_or_id)
    if not agent:
        print(f"No agent called {agent_name_or_id}.", file=sys.stderr)
        return False
    # delete_many: odbms' delete_one emits DELETE ... LIMIT, which SQLite rejects.
    await Agent.delete_many({'id': agent.id})
    print(f"Agent {agent.name} deleted.")
    return True


async def manage_agents(args: Namespace):
    """`cognitrix agents [NAME] [--new|--update|--delete|--list]`."""
    from .core import run_configuration

    await run_configuration()
    as_json = getattr(args, 'json', False)
    target = args.id or args.name
    ok = True
    try:
        if args.new:
            await add_agent(args)
        elif args.delete:
            if not target:
                raise ValueError('Name the agent to delete: cognitrix agents NAME --delete')
            ok = await delete_agent(target)
        elif args.update:
            if not target:
                raise ValueError('Name the agent to change: cognitrix agents NAME --update --model ...')
            ok = await update_agent(target, args)
        elif target and not args.list:
            ok = await show_agent(target, as_json)
        else:
            await list_agents(as_json)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    if not ok:
        sys.exit(1)


# =====================
# Task Management
# =====================
async def list_tasks(as_json: bool = False):
    """List all available tasks."""
    from cognitrix.tasks.base import Task
    tasks = await Task.all()
    if as_json:
        print_json([{'id': str(t.id), 'title': t.title, 'done': bool(getattr(t, 'done', False))} for t in tasks])
        return
    rows = [[i+1, t.title, 'done' if getattr(t, 'done', False) else '', t.id] for i, t in enumerate(tasks)]
    print_table(rows, ["#", "Task Title", "Status", "ID"])


# =====================
# Team Management
# =====================
async def list_teams(as_json: bool = False):
    """List all available teams."""
    from cognitrix.teams.base import Team
    teams = await Team.all()
    if as_json:
        print_json([{'id': str(t.id), 'name': t.name} for t in teams])
        return
    rows = [[i+1, t.name] for i, t in enumerate(teams)]
    print_table(rows, ["#", "Team Name"])


# =====================
# Tool Management
# =====================
def list_tools(category: str, as_json: bool = False):
    """List all available tools, optionally filtered by category."""
    from cognitrix.tools.base import ToolManager
    tools = ToolManager.get_tools_by_category(category or 'all')
    if as_json:
        print_json([{'name': t.name, 'category': t.category,
                     'description': (getattr(t, 'description', '') or '').strip().split('\n')[0]} for t in tools])
        return
    print(f"\nAvailable Tools (Category: {(category or 'all').title()}):")
    if not tools:
        print("No tools found for this category.")
        return

    rows = [[i+1, t.name, t.category] for i, t in enumerate(tools)]
    print_table(rows, ["#", "Tool Name", "Tool Category"])


def manage_tools(args: Namespace):
    """Handle tool management commands."""
    list_tools(args.list or 'all', getattr(args, 'json', False))


# =====================
# Accounts
# =====================
async def set_account_password(email: str, password: str) -> str | None:
    """Set a new password and sign the account out everywhere.
    Returns an error message, or None when it worked."""
    import time

    from cognitrix.common.utils import Utils
    from cognitrix.models import User

    if len(password) < 8:
        return 'Password must be at least 8 characters.'
    user = await User.find_one({'email': email.strip()})
    if not user:
        return f'No account uses the email {email.strip()}.'
    await User.update_one({'id': user.id}, {
        'password': Utils.hash_password(password),
        'tokens_valid_after': int(time.time()),
    })
    return None


async def reset_password(args: Namespace):
    """`cognitrix reset-password <email>`: the way back in after a forgotten password."""
    import getpass
    import sys

    from cognitrix.config import initialize_database

    password = args.password
    if not password:
        password = getpass.getpass('New password: ')
        if getpass.getpass('Repeat it: ') != password:
            print('The two passwords are different. Nothing was changed.')
            sys.exit(1)
    await initialize_database()
    error = await set_account_password(args.email, password)
    if error:
        print(error)
        sys.exit(1)
    print(f'Password changed for {args.email.strip()}. Sign in again with the new one.')


# =====================
# Session Management
# =====================
async def list_sessions(as_json: bool = False):
    """List all saved sessions (the newest last)."""
    from .handlers_sessions import list_saved_sessions
    await list_saved_sessions(as_json=as_json)


# =====================
# Celery Worker Management
# =====================
def start_worker():
    """Start Celery worker process."""
    import subprocess
    try:
        celery_args = ['celery', '-A', 'cognitrix.celery_worker', 'worker', '--loglevel=info']
        worker_process = subprocess.Popen(celery_args)
        print("Celery worker started.")
        return worker_process
    except Exception as e:
        print(f"Error starting Celery worker: {e}")
        return None
