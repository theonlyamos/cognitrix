"""
Core CLI functionality and main startup logic.
"""
import logging
import sys
from argparse import Namespace
from typing import Any

from cognitrix.agents import Agent
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
from cognitrix.models.tool import Tool
from cognitrix.providers import LLM
from cognitrix.sessions.base import Session
from cognitrix.tasks.base import Task
from cognitrix.teams.base import Team
from cognitrix.tools.base import ToolManager

from .handlers import list_agents, list_sessions, list_tasks, list_teams
from .shell import initialize_shell
from .ui import start_web_ui

logger = logging.getLogger('cognitrix.log')

async def run_configuration():
    """Run the configuration for the CLI"""
    from cognitrix.config import initialize_database

    initialize_database()

    # Create tables if they don't exist and not using mongodb
    await Agent.create_table()
    await Task.create_table()
    await Team.create_table()
    await Session.create_table()
    await Tool.create_table()


async def start(args: Namespace):
    """Main startup function that handles CLI arguments and initializes the system."""

    await run_configuration()

    try:
        # Handle list commands
        if args.agents:
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

        # Initialize or find agent
        assistant = None
        if args.agent:
            assistant = await Agent.find_one({'name': args.agent})

        if not assistant:
            provider_input: str | dict[str, Any] = args.provider
            # Build provider config for first-time agent creation so CLI flags
            # (--api-key, --api-base, --model, --temperature, --max-tokens)
            # are honored before LLM validation runs.
            if args.provider:
                provider_overrides: dict[str, Any] = {'provider': args.provider}
                if args.api_key:
                    provider_overrides['api_key'] = args.api_key
                if args.api_base:
                    provider_overrides['base_url'] = args.api_base
                if args.model:
                    provider_overrides['model'] = args.model
                if args.temperature is not None:
                    provider_overrides['temperature'] = args.temperature
                if getattr(args, 'max_tokens', 0) > 0:
                    provider_overrides['max_tokens'] = args.max_tokens
                provider_input = provider_overrides

            assistant = await Agent.create_agent(
                name=args.agent,
                provider=provider_input,
                model=args.model,
                temperature=args.temperature,
                system_prompt=ASSISTANT_SYSTEM_PROMPT
            )

        if not assistant:
            raise Exception("Agent not found or could not be created")

        # Configure agent with provided options (CLI overrides env)
        if args.provider:
            overrides: dict[str, Any] = {'provider': args.provider}
            if args.api_key:
                overrides['api_key'] = args.api_key
            if args.api_base:
                overrides['base_url'] = args.api_base
            if args.model:
                overrides['model'] = args.model
            if args.temperature is not None:
                overrides['temperature'] = args.temperature
            if getattr(args, 'max_tokens', 0) > 0:
                overrides['max_tokens'] = args.max_tokens
            provider = LLM.load_llm(overrides)
            if provider:
                assistant.llm = provider

        # Load tools if specified
        if len(args.load_tools) and not len(assistant.tools):
            tools = []
            for cat in args.load_tools:
                cat_tools = ToolManager.get_tools_by_category(cat.strip().lower())
                tools.extend(cat_tools)

                # Also check for individual tool by name
                tool_by_name = ToolManager.get_by_name(cat.strip().lower())
                if tool_by_name:
                    tools.append(tool_by_name)

            assistant.tools = tools

        # Initialize or get session
        session = await Session.get_by_agent_id(assistant.id)
        if not session:
            session = Session(agent_id=assistant.id)

        # Clear history if requested
        if args.clear_history:
            session.chat = []
            await session.save()

        # Set verbose mode
        assistant.verbose = args.verbose
        await assistant.save()

        # Handle different execution modes
        if args.generate:
            # Single generation mode
            await session(args.generate, assistant, stream=args.stream)
        elif args.ui.lower() == 'web':
            # Web UI mode
            await start_web_ui(assistant)
        else:
            # Interactive CLI mode
            await initialize_shell(session, assistant, stream=args.stream)

    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(1)
    except Exception as e:
        logging.exception(e)
        sys.exit(1)
