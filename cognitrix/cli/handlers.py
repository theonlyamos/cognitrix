"""
Command handlers for managing different entity types.
"""
import asyncio
import logging
import sys
from argparse import Namespace

from rich import print

from .utils import print_table

logger = logging.getLogger('cognitrix.log')


# =====================
# Agent Management
# =====================
async def list_agents():
    """List all available agents."""
    from cognitrix.agents import Agent
    agents = await Agent.all()
    rows = [[i+1, a.name] for i, a in enumerate(agents)]
    print_table(rows, ["#", "Agent Name"])


def add_agent():
    """Interactive agent creation."""
    from cognitrix.agents import Agent
    from cognitrix.providers import LLM
    name = None
    provider_id = None
    model_override = None
    temperature = 0.1
    system_prompt = None
    try:
        while not name:
            name = input("Enter agent name: ")
            while not provider_id:
                provider_id = input("\nEnter provider name (e.g. openai, openrouter, ollama, groq): ").strip()
            llm = LLM.load_llm(provider_id)
            if llm:
                model_override = input(f"\nEnter model name [{llm.model}]: ") or llm.model
                temp = input(f"\nEnter model temperature [{llm.temperature}]: ")
                temperature = float(temp) if temp else llm.temperature
            while not system_prompt:
                system_prompt = input("\n[Enter agent system prompt]: ")
            new_agent = asyncio.run(Agent.create_agent(
                name, system_prompt=system_prompt, provider=provider_id,
                model=model_override or '', temperature=temperature, is_sub_agent=True
            ))
            if not new_agent:
                raise Exception("Error creating agent")
            print(f"\nAgent **{new_agent.name}** added successfully!")
    except Exception as e:
        logger.error(str(e))
    finally:
        sys.exit()


def delete_agent(agent_name_or_index: str):
    """Delete an agent by name or index."""
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
    """Handle agent management commands."""
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
    """List all available tasks."""
    from cognitrix.tasks.base import Task
    tasks = Task.all()
    rows = [[i+1, t.title] for i, t in enumerate(tasks)]
    print_table(rows, ["#", "Task Title"])


# =====================
# Team Management
# =====================
def list_teams():
    """List all available teams."""
    from cognitrix.teams.base import Team
    teams = Team.all()
    rows = [[i+1, t.name] for i, t in enumerate(teams)]
    print_table(rows, ["#", "Team Name"])


# =====================
# Tool Management
# =====================
def list_tools(category: str):
    """List all available tools, optionally filtered by category."""
    print(f"\nAvailable Tools (Category: {category.title()}):")

    from cognitrix.tools.base import ToolManager
    tools = ToolManager.get_tools_by_category(category)

    if not tools:
        print("No tools found for this category.")
        return

    rows = [[i+1, t.name, t.category] for i, t in enumerate(tools)]
    print_table(rows, ["#", "Tool Name", "Tool Category"])


def manage_tools(args: Namespace):
    """Handle tool management commands."""
    if args.list:
        list_tools(args.list)


# =====================
# Session Management
# =====================
async def list_sessions():
    """List all saved sessions."""
    from cognitrix.sessions.base import Session
    print("\nSaved Sessions:")
    sessions = await Session.list_sessions()
    rows = [[i, s.datetime, s.id] for i, s in enumerate(sessions)]
    print_table(rows, ["#", "Datetime", "Session ID"])


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
