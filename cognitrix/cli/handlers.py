"""
Command handlers for managing different entity types.
"""
import sys
import asyncio
import logging
from argparse import Namespace

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
def list_tools(category='all'):
    """List tools by category."""
    from cognitrix.tools.base import Tool
    tools = Tool.get_tools_by_category(category)
    rows = [[i+1, t.name, t.category] for i, t in enumerate(tools)]
    print_table(rows, ["#", "Tool Name", "Tool Category"])


def manage_tools(args: Namespace):
    """Handle tool management commands."""
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
    """List all available LLM providers."""
    from cognitrix.providers import LLM
    providers = LLM.list_llms()
    rows = [[i+1, p.__name__] for i, p in enumerate(providers)]
    print_table(rows, ["#", "Provider"])


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