import uuid
import asyncio
import logging
import logging
from rich import print
from datetime import datetime
from odbms import Model
from pydantic import Field
from typing import TYPE_CHECKING, Any, Callable, List, Literal, Optional, Dict, Self, Union


from cognitrix.providers.base import LLMResponse
# from cognitrix.teams.base import Team

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.teams.base import Team

logger = logging.getLogger('cognitrix.log')

class Session(Model):
    chat: List[Dict[str, Any]] = []
    """The chat history of the session"""
    
    datetime: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """When the session was started"""
    
    agent_id: Optional[str] = None
    """The id of the agent that started the session"""
    
    task_id: Optional[str] = None
    """The id of the task that started the session"""
    
    team_id: Optional[str] = None
    """The id of the team that started the session"""
    
    started_at: Optional[str] = None
    """Started date of the task"""
    
    completed_at: Optional[str] = None
    """Completion date of the task"""
    
    pid: Optional[str] = None
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
    async def list_sessions(cls) -> List[Self]:
        return await cls.all()

    @classmethod
    async def delete(cls, session_id: str):
        """Delete session by id"""
        return await cls.delete_one({'id': session_id})


    def update_history(self, message: Dict[str, str]):
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
    async def get_by_task_id(cls, task_id: str) -> List[Self]:
        """Retrieve a session by task_id"""
        return await cls.find({'task_id': task_id}) # type: ignore
    
    async def __call__(self, message: str|dict, agent: 'Agent', interface: Literal['cli', 'task', 'web', 'ws'] = 'cli', stream: bool = False, output: Callable = print, wsquery: Dict[str, str]= {}, save_history: bool = True):
        from cognitrix.agents.base import Agent
        
        system_prompt = agent.formatted_system_prompt()
        formatted_tools = [tool.to_dict_format() for tool in agent.tools if len(tool.to_dict_format().keys())]
        tool_calls: bool = False
        
        try:
            while message:
                try:
                    full_prompt = agent.process_prompt(message)
                    message = ''
                    response: LLMResponse = LLMResponse()
                    called_tools: bool = False
                    
                    async for response in agent.llm(full_prompt, system_prompt, self.chat, stream=stream, tools=formatted_tools):
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
                                message = result
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
                        self.update_history(full_prompt)
                        response_dict = {
                            'role': agent.name,
                            'type': 'text',
                            'content': response.llm_response
                        }
                        self.update_history(response_dict)
                        
                        # if response.result and not stream:
                        #     if interface == 'cli':
                        #         output(f"\n{agent.name}:", response.result)
                        #     else:
                        #         await output({'type': wsquery['type'], 'content': response.result, 'action': wsquery['action'], 'complete': True})
                    
                    if not tool_calls:
                        stream = False  
                    if save_history:
                        await self.save()
                    if not message:
                        break
                except Exception as e:
                    logger.exception(e)
                    continue
                
        except Exception as e:
            logger.exception(e)
