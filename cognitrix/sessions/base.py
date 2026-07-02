import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Optional, Self

from odbms import Model
from rich import print

from cognitrix.providers.base import LLMResponse

# from cognitrix.teams.base import Team

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.teams.base import Team

logger = logging.getLogger('cognitrix.log')


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"

# Hard ceiling on retained chat messages per session. The LLM prompt only uses
# the sliding window (last ~10), so this just bounds memory/serialization growth
# over very long sessions. ponytail: fixed cap; make configurable if needed.
MAX_CHAT_HISTORY = 1000


class Session(Model):
    chat: list[dict[str, Any]] = []
    """The chat history of the session"""

    datetime: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """When the session was started"""

    agent_id: str | None = None
    """The id of the agent that started the session"""

    task_id: str | None = None
    """The id of the task that started the session"""

    team_id: str | None = None
    """The id of the team that started the session"""

    started_at: str | None = None
    """Started date of the task"""

    completed_at: str | None = None
    """Completion date of the task"""

    pid: str | None = None
    """Worker Id of task"""

    @classmethod
    async def load(cls, session_id: str) -> Self:
        """Load an existing session or create a new one if it doesn't exist"""
        session = await cls.get(session_id)
        if not session:
            session = cls()
            await session.save()
        return session

    @classmethod
    async def list_sessions(cls) -> list[Self]:
        return await cls.all()

    @classmethod
    async def delete(cls, session_id: str):
        """Delete a session by id.

        odbms exposes delete() as an instance method, so load the row and
        delete the instance. The previous implementation called cls.delete()
        which recursed infinitely.
        """
        session = await cls.get(session_id)
        if not session:
            return False
        await Model.delete(session)
        return True


    def update_history(self, message: dict[str, Any] | list[dict[str, Any]]):
        if isinstance(message, list):
            self.chat.extend(message)
        else:
            self.chat.append(message)
        # Bound growth: drop the oldest messages past the ceiling. The prompt
        # builder trims any orphan tool message left at the new start.
        if len(self.chat) > MAX_CHAT_HISTORY:
            del self.chat[:len(self.chat) - MAX_CHAT_HISTORY]

    @property
    async def agent(self):
        from cognitrix.agents.base import Agent
        return await Agent.get(self.agent_id) if self.agent_id else None

    @property
    async def team(self) -> Optional['Team']:
        from cognitrix.teams.base import Team
        return await Team.get(self.team_id) if self.team_id else None



    @classmethod
    async def get_by_agent_id(cls, agent_id: str) -> Self:
        """Retrieve a session by agent_id"""
        agent_id_str = str(agent_id)
        session = await cls.find_one({'agent_id': agent_id_str})
        if not session:
            session = cls(agent_id=agent_id_str)
            await session.save()
        return session


    @classmethod
    async def get_by_task_id(cls, task_id: str) -> list[Self]:
        """Retrieve a session by task_id"""
        return await cls.find({'task_id': task_id}) # type: ignore

    async def __call__(self, message: str|dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws'] = 'cli', stream: bool = False, output: Callable = print, wsquery: dict[str, str] | None= None, save_history: bool = True):

        # Add timing for turn duration
        turn_start_time = time.monotonic()

        # Add the new message to the history before building the prompt
        if wsquery is None:
            wsquery = {}
        if save_history:
            self.update_history(agent.process_prompt(message))

        # Build the context-aware prompt using the manager
        prompt = await agent.get_context_manager().build_prompt(agent, self)

        formatted_tools = [tool.to_dict_format() for tool in agent.tools]
        # Only advertise tools to models with native tool-use; otherwise the tool
        # list is embedded in the system prompt (AgentManager.formatted_system_prompt).
        active_tools = formatted_tools if agent.llm.supports_tool_use else None

        MAX_TOOL_ROUNDS = 10
        tool_rounds = 0
        try:
            while True:  # The loop now primarily handles tool calls, not initial message processing
                if tool_rounds >= MAX_TOOL_ROUNDS:
                    logger.warning("Exceeded max tool rounds (%s); stopping turn", MAX_TOOL_ROUNDS)
                    if interface == 'cli':
                        output(f"\n[Stopped after {MAX_TOOL_ROUNDS} tool rounds]")
                    break
                try:
                    response: LLMResponse = LLMResponse()
                    called_tools: bool = False

                    # The prompt is now a complete history, not a single message
                    llm_result = await agent.llm(prompt, stream=stream, tools=active_tools)

                    if stream:
                        async_iter = llm_result  # type: ignore[arg-type]
                    else:
                        async def _single_resp(result=llm_result):
                            yield result  # type: ignore[misc]

                        async_iter = _single_resp()

                    async for response in async_iter:  # type: ignore[misc]
                        if stream:
                            if interface == 'cli':
                                output(f"{response.current_chunk}", end="")
                            else:
                                await output({'type': wsquery['type'], 'content': response.current_chunk, 'action': wsquery['action'], 'complete': False})
                        else:
                            if response.result:
                                if interface == 'cli':
                                    output(f"\n{agent.name}:", response.result)
                                else:
                                    await output({'type': wsquery['type'], 'content': response.result, 'action': wsquery['action'], 'complete': False})
                            else:
                                if interface == 'cli':
                                    output(f"\n{agent.name}:", response.llm_response)
                                else:
                                    await output({'type': wsquery['type'], 'content': response.llm_response, 'action': wsquery['action'], 'complete': False})

                        if response.tool_calls and not called_tools:
                            # (interface is threaded into call_tools so approval uses
                            # the right channel — CLI prompts, web/ws deny for now.)
                            # Record the assistant message that ISSUED the tool calls
                            # BEFORE the tool results, so the re-prompt is a valid
                            # OpenAI sequence (assistant.tool_calls -> tool results).
                            # Unconditional (like the tool-results append below): the
                            # loop needs both in history to rebuild the next prompt.
                            self.update_history({
                                'role': 'assistant',
                                'type': 'tool_calls',
                                'content': response.llm_response or '',
                                'tool_calls': response.tool_calls,
                            })
                            result: dict[Any, Any] | str = await agent.call_tools(response.tool_calls, interface=interface)
                            called_tools = True
                            tool_rounds += 1

                            if isinstance(result, dict) and result['type'] == 'tool_calls_result':
                                # If a tool call has a result, add it to history and rebuild the prompt
                                self.update_history(agent.process_prompt(result))
                                prompt = await agent.get_context_manager().build_prompt(agent, self)
                                continue  # Continue the loop to re-prompt the LLM with the tool result
                            else:
                                if interface == 'cli':
                                    output(result)
                                else:
                                    await output({'type': wsquery['type'], 'content': result, 'action': wsquery['action'], 'complete': False})

                        if response.artifacts:
                            if interface == 'ws':
                                await output({'type': wsquery['type'], 'content': '', 'action': wsquery['action'], 'artifacts': response.artifacts, 'complete': False})

                        await asyncio.sleep(0.01)

                    # Provider/transport error: already surfaced to the user above;
                    # don't persist it as a normal answer or re-prompt the tool loop.
                    if response and getattr(response, 'error', None):
                        break

                    # Persist the final assistant text only when this response did
                    # NOT issue tool calls (that assistant message was already saved
                    # with its tool_calls above).
                    if response and save_history and not response.tool_calls:
                        response_dict = {
                            'role': 'assistant',
                            'type': 'text',
                            'content': response.llm_response
                        }
                        self.update_history(response_dict)

                    if not called_tools:
                        # Calculate turn duration
                        turn_duration = time.monotonic() - turn_start_time
                        duration_str = format_duration(turn_duration)

                        # Store timing in history
                        if save_history:
                            self.update_history({
                                'role': 'system',
                                'type': 'turn_timing',
                                'content': f"Took {duration_str}",
                                'duration': turn_duration
                            })

                        # Display timing for CLI
                        if interface == 'cli':
                            print(f"\n[dim]Took {duration_str}[/dim]")

                        break # Exit loop if no tools were called

                except Exception as e:
                    logger.exception(e)
                    break # Exit on error

            # Calculate turn duration for all exit paths
            turn_duration = time.monotonic() - turn_start_time
            duration_str = format_duration(turn_duration)

            # Store timing in history (only if not already stored in 'not called_tools' block)
            if save_history and called_tools:
                self.update_history({
                    'role': 'system',
                    'type': 'turn_timing',
                    'content': f"Took {duration_str}",
                    'duration': turn_duration
                })

            # Display timing for CLI (only if not already displayed)
            if interface == 'cli' and called_tools:
                print(f"\n[dim]Took {duration_str}[/dim]")

            if save_history:
                await self.save()

            # Add to agent's memory
            if save_history and hasattr(agent, 'context_manager'):
                try:
                    await agent.context_manager.add_to_memory({
                        'role': 'user',
                        'type': 'text',
                        'content': message if isinstance(message, str) else str(message)
                    })
                    if response:
                        await agent.context_manager.add_to_memory({
                            'role': 'assistant',
                            'type': 'text',
                            'content': response.llm_response
                        })
                except Exception as e:
                    logger.error(f"Failed to add to memory: {e}")

        except Exception as e:
            logger.exception(e)

class SessionManager:
    """Manager for Session business logic"""

    def __init__(self, session: Session):
        self.session = session

    # Provide a convenience creator to match the uniform API requested.
    @staticmethod
    async def create(agent_id: str | None = None, team_id: str | None = None, task_id: str | None = None) -> 'Session':
        session = Session(agent_id=agent_id, team_id=team_id, task_id=task_id)
        await session.save()
        return session

# Note: the previous SessionManager.__call__ was a near-duplicate of
# Session.__call__ and had no callers; the single turn loop now lives in
# Session.__call__. SessionManager keeps only create() + the manager property.

# ---------------------------------------------------------------------------
# Attach SessionManager helpers to the Session model so that all management
# logic lives in SessionManager while existing call-sites can still use
# Session.load / list_sessions etc.
# ---------------------------------------------------------------------------

def _session_manager(self: Session) -> 'SessionManager':  # type: ignore[name-defined]
    return SessionManager(self)

Session.manager = property(_session_manager)  # type: ignore[attr-defined]

# Delegate class-level helpers
Session.load = staticmethod(Session.load)  # already exists, keep for compatibility
Session.list_sessions = staticmethod(Session.list_sessions)  # type: ignore[attr-defined]
# Note: Session.delete stays a classmethod (delete-by-id); do not re-wrap it.
