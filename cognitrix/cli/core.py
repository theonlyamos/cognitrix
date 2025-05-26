"""
Core CLI functionality and main startup logic.
"""
import sys
import logging
from argparse import Namespace

from .handlers import list_providers, list_agents, list_tasks, list_teams, list_sessions
from .ui import start_web_ui
from .shell import initialize_shell

logger = logging.getLogger('cognitrix.log')


async def start(args: Namespace):
    """Main startup function that handles CLI arguments and initializes the system."""
    from cognitrix.providers import LLM
    from cognitrix.agents import Agent
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.base import Task
    from cognitrix.tools.base import Tool
    from cognitrix.teams.base import Team
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
    from cognitrix.config import VERSION, run_configure
    
    # Initialize configuration
    run_configure()
    
    try:
        # Handle list commands
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
        
        # Initialize or find agent
        assistant = None
        if args.agent:
            assistant = await Agent.find_one({'name': args.agent})
        
        if not assistant:
            assistant = await Agent.create_agent(
                name=args.agent, 
                provider=args.provider, 
                model=args.model, 
                temperature=args.temperature, 
                system_prompt=ASSISTANT_SYSTEM_PROMPT
            )
        
        if not assistant:
            raise Exception("Agent not found or could not be created")
        
        # Configure agent with provided options
        if args.provider:
            provider = LLM.load_llm(args.provider)
            if provider:
                provider.provider = args.provider
                assistant.llm = provider
        
        if args.model and (assistant.llm.model.lower() != args.model.lower()):
            assistant.llm.model = args.model
        
        # Load tools if specified
        if len(args.load_tools) and not len(assistant.tools):
            tools = []
            for cat in args.load_tools:
                cat_tools = Tool.get_tools_by_category(cat.strip().lower())
                tools.extend(cat_tools)
                
                # Also check for individual tool by name
                tool_by_name = Tool.get_by_name(cat.strip().lower())
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