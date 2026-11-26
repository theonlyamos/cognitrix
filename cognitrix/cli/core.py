"""
Core CLI functionality and main startup logic.
"""
import logging
import sys
from argparse import Namespace
from typing import Any

from cognitrix.agents import Agent
from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
from cognitrix.artifacts import delete_session_artifacts
from cognitrix.models.tool import Tool
from cognitrix.providers import LLM
from cognitrix.sessions.base import Session
from cognitrix.tasks.base import Task
from cognitrix.tasks.handler import handle_multi_step_task, parse_task_request
from cognitrix.teams.base import Team
from cognitrix.tools.base import ToolManager

from .handlers import list_agents, list_sessions, list_tasks, list_teams
from .shell import initialize_shell
from .tui import CognitrixApp
from .ui import start_web_ui

logger = logging.getLogger('cognitrix.log')


async def _clear_session_history(session: Session) -> None:
    session.chat = []
    await session.save()
    await delete_session_artifacts(str(session.id))

async def _last_cli_session(agent_id: str) -> Session | None:
    """The newest terminal chat with this agent. Chats from the app (they
    have an owner) and task runs are left alone: the main assistant's app
    chats don't continue in the terminal, and the reverse."""
    rows = [
        s for s in await Session.find({'agent_id': agent_id}) or []
        if not getattr(s, 'user_id', None) and not getattr(s, 'task_id', None) and not getattr(s, 'team_id', None)
    ]
    if not rows:
        return None
    return max(rows, key=lambda s: str(getattr(s, 'created_at', '') or getattr(s, 'started_at', '') or ''))


async def run_configuration():
    """Run the configuration for the CLI"""
    from cognitrix.config import initialize_database
    from cognitrix.skills.manager import get_skill_manager

    await initialize_database()

    # Create tables if they don't exist and not using mongodb
    from odbms import DBMS

    from cognitrix.agents import Agent
    from cognitrix.models.api_key import APIKey
    from cognitrix.models.user import User
    from cognitrix.session_ownership import SessionOwnership
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.events import TaskRunEvent
    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.run import TaskRun, TaskRunHead
    from cognitrix.tasks.step import TaskRunStep

    if DBMS.Database is not None and DBMS.Database.dbms != 'mongodb':
        from cognitrix.artifacts import Artifact, DocumentArtifact
        for model in (
            Agent, Task, Team, Session, Tool, User, TaskRun, TaskRunHead,
            TaskRunStep, TaskRunEvent, TaskRunPhaseMetric, APIKey, Artifact,
            SessionOwnership, DocumentArtifact,
        ):
            # Older odbms releases only ship async create_table(); newer ones
            # rename it to _create_table_async.
            create = getattr(model, '_create_table_async', None) or model.create_table
            await create()

    # Pre-warm skill cache at startup to avoid per-prompt I/O
    skill_mgr = get_skill_manager()
    await skill_mgr.discover_all()


async def start(args: Namespace):
    """Main startup function that handles CLI arguments and initializes the system."""

    await run_configuration()

    try:
        # Handle list commands
        if args.agents:
            await list_agents()
            sys.exit()
        elif args.tasks:
            await list_tasks()
            sys.exit()
        elif args.teams:
            await list_teams()
            sys.exit()
        elif args.sessions:
            await list_sessions()
            sys.exit()

        # The person's main assistant unless --agent names one
        # (cognitrix/cli/main_assistant.py).
        from . import app_turn
        from . import main_assistant as cli_agent

        try:
            resolved = await cli_agent.resolve(args.agent or '', getattr(args, 'user', '') or '')
        except cli_agent.NoSuchUser as e:
            print(f"No account with the email {e}", file=sys.stderr)
            sys.exit(1)
        assistant = resolved.agent if resolved else None

        if not assistant:
            provider_input: str | dict[str, Any] = args.provider or cli_agent._default_provider()
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
                name=args.agent or cli_agent.LEGACY_NAME,
                provider=provider_input,
                model=args.model,
                temperature=args.temperature,
                system_prompt=ASSISTANT_SYSTEM_PROMPT
            )
            if assistant:
                resolved = cli_agent.Resolved(assistant, 'created')

        if not assistant or resolved is None:
            raise Exception("Agent not found or could not be created")
        # A person's agent runs its turns the way the app's chats do.
        owned = bool(resolved.owner) and resolved.source in ('main', 'named')
        logger.info(cli_agent.describe(resolved))
        if args.verbose:
            print(cli_agent.describe(resolved), file=sys.stderr)

        # Configure agent with provided options (CLI overrides for this run).
        if args.provider or args.model or args.temperature is not None or getattr(args, 'max_tokens', 0) > 0:
            overrides: dict[str, Any] = {'provider': args.provider or assistant.llm.provider}
            if args.api_key:
                overrides['api_key'] = args.api_key
            if args.api_base:
                overrides['base_url'] = args.api_base
            elif not args.provider and getattr(assistant.llm, 'base_url', ''):
                overrides['base_url'] = assistant.llm.base_url
            if args.model:
                overrides['model'] = args.model
            elif not args.provider and assistant.llm.model:
                overrides['model'] = assistant.llm.model
            if args.temperature is not None:
                overrides['temperature'] = args.temperature
            if getattr(args, 'max_tokens', 0) > 0:
                overrides['max_tokens'] = args.max_tokens
            provider = LLM.load_llm(overrides)
            if provider:
                assistant.llm = provider

        # Load tools if asked for (--load-tools). Only for this run: the
        # agent's saved tools are what the app uses.
        if args.load_tools and (owned or not len(assistant.tools)):
            tools = list(assistant.tools) if owned else []
            names = {str(getattr(t, 'name', '')).lower() for t in tools}
            for cat in args.load_tools:
                found = list(ToolManager.get_tools_by_category(cat.strip().lower()))
                # Also check for individual tool by name
                tool_by_name = ToolManager.get_by_name(cat.strip().lower())
                if tool_by_name:
                    found.append(tool_by_name)
                for tool in found:
                    if str(tool.name).lower() not in names:
                        names.add(str(tool.name).lower())
                        tools.append(tool)
            assistant.tools = tools

        # The chat. For a person's agent it is a chat in the app, like one
        # started there (cognitrix/cli/app_turn.py): a new one per run unless
        # --continue is given. A one-shot prompt starts a fresh session
        # unless --continue is given, like other agent CLIs.
        one_shot = bool(args.prompt or getattr(args, 'prompt_file', ''))
        resume = getattr(args, 'continue_session', False)
        session = None
        if owned:
            if resume:
                session = await app_turn.last_chat(resolved.owner, assistant)
            if not session:
                session = await app_turn.new_chat(resolved.owner, assistant)
        else:
            if not one_shot or resume:
                session = await _last_cli_session(str(assistant.id))
            if not session:
                session = Session(agent_id=assistant.id)

        # Clear history if requested
        if args.clear_history:
            await _clear_session_history(session)

        # Set verbose mode. Only an agent this run created is saved: flags
        # such as --provider/--model are for this run and must not change the
        # person's main assistant (or any other saved agent) in the app.
        assistant.verbose = args.verbose
        if not owned and resolved.source in ('created', 'legacy', 'named'):
            await assistant.save()

        # Resolve the effective prompt: --prompt-file (safe for long/multi-line/
        # special-character prompts the shell would mangle) wins over --prompt/-p.
        prompt = args.prompt
        if getattr(args, 'prompt_file', ''):
            try:
                with open(args.prompt_file, encoding='utf-8') as f:
                    prompt = f.read()
            except OSError as e:
                logger.error("Could not read --prompt-file '%s': %s", args.prompt_file, e)
                sys.exit(1)

        # Handle different execution modes
        if prompt:
            # Multi-step task runs are explicit: --task, or a "/task ..." prompt.
            task_prompt = parse_task_request(prompt) or (prompt if getattr(args, 'task', False) else None)
            if task_prompt:
                logger.info("Task run requested, creating execution plan...")
                result = await handle_multi_step_task(
                    task_prompt,
                    assistant,
                    session,
                    assistant.llm,
                    args.stream
                )
                print(result)
            else:
                await app_turn.turn(session, assistant, prompt, stream=args.stream)
                if session.last_turn_error:
                    # One-shot mode is used from scripts: report failure.
                    sys.exit(1)
        elif args.ui.lower() == 'web':
            # Web UI mode
            await start_web_ui(assistant)
        elif args.ui.lower() == 'tui':
            # CLI mode has been replaced with the new beautiful Textual TUI
            app = CognitrixApp(agent=assistant, session=session)
            await app.run_async()
        else:
            # Fallback legacy interactive CLI mode
            await initialize_shell(session, assistant, stream=args.stream)

    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(1)
    except Exception as e:
        logging.exception(e)
        sys.exit(1)
