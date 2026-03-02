import asyncio
import logging
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
        """Delete session by id"""
        return await cls.delete(session_id)


    def update_history(self, message: dict[str, Any]):
        self.chat.append(message)

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
        session = await cls.find_one({'agent_id': agent_id})
        if not session:
            session = cls(agent_id=agent_id)
            await session.save()
        return session


    @classmethod
    async def get_by_task_id(cls, task_id: str) -> list[Self]:
        """Retrieve a session by task_id"""
        return await cls.find({'task_id': task_id}) # type: ignore

    async def __call__(self, message: str|dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws'] = 'cli', stream: bool = False, output: Callable = print, wsquery: dict[str, str] | None= None, save_history: bool = True):

        # Add the new message to the history before building the prompt
        if wsquery is None:
            wsquery = {}
        if save_history:
            self.update_history(agent.process_prompt(message))

        # Build the context-aware prompt using the manager
        prompt = agent.get_context_manager().build_prompt(agent, self)

        formatted_tools = [tool.to_dict_format() for tool in agent.tools if len(tool.to_dict_format().keys())]

        try:
            while True:  # The loop now primarily handles tool calls, not initial message processing
                try:
                    response: LLMResponse = LLMResponse()
                    called_tools: bool = False

                    # The prompt is now a complete history, not a single message
                    llm_result = await agent.llm(prompt, stream=stream, tools=formatted_tools)

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

                        if response.result:
                            if not stream:
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
                            result: dict[Any, Any] | str = await agent.call_tools(response.tool_calls)
                            called_tools = True

                            if isinstance(result, dict) and result['type'] == 'tool_calls_result':
                                # If a tool call has a result, add it to history and rebuild the prompt
                                self.update_history(agent.process_prompt(result))
                                prompt = agent.get_context_manager().build_prompt(agent, self)
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

                    if response and save_history:
                        response_dict = {
                            'role': agent.name,
                            'type': 'text',
                            'content': response.llm_response
                        }
                        self.update_history(response_dict)

                    if not called_tools:
                        break # Exit loop if no tools were called

                except Exception as e:
                    logger.exception(e)
                    break # Exit on error

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
                            'role': agent.name,
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

    async def __call__(self, message: str | dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws'] = 'cli', stream: bool = False, output: Callable = print, wsquery: dict[str, str] | None = None, save_history: bool = True):
        # This is a temporary fix for the agent's process_prompt method which is not yet refactored.
        if wsquery is None:
            wsquery = {}
        agent_manager = agent.manager # type: ignore

        if save_history:
            self.session.update_history(agent_manager.process_prompt(message))

        prompt = agent.get_context_manager().build_prompt(agent, self.session)

        formatted_tools = [tool.to_dict_format() for tool in agent.tools if len(tool.to_dict_format().keys())]

        try:
            while True:
                response: LLMResponse = LLMResponse()
                called_tools: bool = False

                llm_result = await agent.llm(prompt, stream=stream, tools=formatted_tools)

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
                            await output({'type': wsquery.get('type'), 'content': response.current_chunk, 'action': wsquery.get('action'), 'complete': False})

                    if not stream and response.result:
                        if interface == 'cli':
                            output(f"\n{agent.name}:", response.result)
                        else:
                            await output({'type': wsquery.get('type'), 'content': response.result, 'action': wsquery.get('action'), 'complete': False})

                    if response.tool_calls and not called_tools:
                        result = await agent_manager.call_tools(response.tool_calls)
                        called_tools = True

                        if isinstance(result, dict) and result.get('type') == 'tool_calls_result':
                            self.session.update_history(agent_manager.process_prompt(result))
                            prompt = agent.get_context_manager().build_prompt(agent, self.session)
                            continue
                        else:
                            if interface == 'cli':
                                output(result)
                            else:
                                await output({'type': wsquery.get('type'), 'content': result, 'action': wsquery.get('action'), 'complete': False})

                    if response.artifacts:
                        if interface == 'ws':
                            await output({'type': wsquery.get('type'), 'content': '', 'action': wsquery.get('action'), 'artifacts': response.artifacts, 'complete': False})

                    await asyncio.sleep(0.01)

                if response and save_history:
                    self.session.update_history({
                        'role': agent.name,
                        'type': 'text',
                        'content': response.llm_response
                    })

                if not called_tools:
                    break

            if save_history:
                await self.session.save()

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
                            'role': agent.name,
                            'type': 'text',
                            'content': response.llm_response
                        })
                except Exception as e:
                    logger.error(f"Failed to add to memory: {e}")

        except Exception as e:
            logger.exception(e)

# ---------------------------------------------------------------------------
# Attach SessionManager helpers to the Session model so that all management
# logic lives in SessionManager while existing call-sites can still use
# Session.load / list_sessions etc.                                           
# ---------------------------------------------------------------------------

def _session_manager(self: Session) -> 'SessionManager':  # type: ignore[name-defined]
    return SessionManager(self)

setattr(Session, 'manager', property(_session_manager))  # type: ignore[attr-defined]

# Delegate class-level helpers
setattr(Session, 'load', staticmethod(Session.load))  # already exists, keep for compatibility
setattr(Session, 'list_sessions', staticmethod(Session.list_sessions))  # type: ignore[attr-defined]
setattr(Session, 'delete', staticmethod(Session.delete))  # type: ignore[attr-defined]
