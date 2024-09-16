import uuid
import asyncio
import logging
import logging
from rich import print
from datetime import datetime
from odbms import Model
from pydantic import Field
from typing import TYPE_CHECKING, Any, Callable, List, Literal, Optional, Dict, Self, Union


from cognitrix.llms.base import LLMResponse

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent

logger = logging.getLogger('cognitrix.log')

class Session(Model):
    chat: List[Dict[str, str]] = []
    """The chat history of the session"""
    
    datetime: str = (datetime.now()).strftime("%a %b %d %Y %H:%M:%S")
    """When the session was started"""
    
    agent_id: str = ""
    """The id of the agent that started the session"""
    
    task_id: Optional[str] = None
    """The id of the task that started the session"""
    
    started_at: Optional[str] = None
    """Started date of the task"""
    
    completed_at: Optional[str] = None
    """Completion date of the task"""
    
    pid: Optional[str] = None
    """Worker Id of task"""

    @classmethod
    async def load(cls, session_id: str) -> Self:
        """Load an existing session or create a new one if it doesn't exist"""
        session = cls.get(session_id)
        if not session:
            session = cls()
            session.save()
        return session

    @classmethod
    async def list_sessions(cls) -> List[Self]:
        return cls.all()

    @classmethod
    async def delete(cls, session_id: str):
        """Delete session by id"""
        return cls.remove({'id': session_id})

    def update_history(self, message: Dict[str, str]):
        self.chat.append(message)

    @property
    async def agent(self):
        try:
            agents = await Agent.list_agents()
            loaded_agents: list[Agent] = [agent for agent in agents if agent.id == self.agent_id]
            if len(loaded_agents):
                return loaded_agents[0]
        except Exception as e:
            logger.exception(e)
            return None
    
    @classmethod
    async def get_by_agent_id(cls, agent_id: str) -> Self:
        """Retrieve a session by agent_id"""
        session = cls.find_one({'agent_id': agent_id})
        if not session:
            session = cls(agent_id=agent_id)
            session.save()
        return session
    
    @classmethod
    async def get_by_task_id(cls, task_id: str) -> List[Self]:
        """Retrieve a session by task_id"""
        return cls.find({'task_id': task_id}) # type: ignore
    
    async def __call__(self, message: str|dict, agent: Union['Agent'], interface: Literal['cli', 'web'] = 'cli', streaming: bool = False, output: Callable = print, wsquery: Dict[str, str]= {}, save_history: bool = True):
        from cognitrix.agents.base import Agent
        
        system_prompt = agent.formatted_system_prompt()
        tool_calls: bool = False
        
        try:
            if not agent:
                raise Exception('Agent not initialized')
            
            while message:
                try:
                    full_prompt = agent.process_prompt(message)
                    message = ''
                    # response: LLMResponse | None = None
                    called_tools: bool = False
                    async for response in agent.llm(full_prompt, system_prompt, self.chat):   
                        if streaming:
                            if interface == 'cli':
                                output(f"{response.current_chunk}", end="")
                            else:
                                await output({'type': wsquery['type'], 'content': response.current_chunk, 'action': wsquery['action'], 'complete': False})
                        
                        if response.tool_calls and not called_tools and not response.text:
                            called_tools = True
                            result: dict[Any, Any] | str = await agent.call_tools(response.tool_calls)
                            
                            if isinstance(result, dict) and result['type'] == 'tool_calls_result':
                                message = result
                            else:
                                if interface == 'cli':
                                    output(result)
                                else:
                                    await output({'type': wsquery['type'], 'content': result, 'action': wsquery['action']})
                        
                        if response.artifacts:
                            if 'artifact' in response.artifacts.keys():
                                if interface == 'ws':
                                    await output({'type': wsquery['type'], 'content': '', 'action': wsquery['action'], 'artifacts': response.artifacts['artifact']})
                        
                        await asyncio.sleep(0.01)
                
                    if response and save_history:
                        self.update_history(full_prompt)
                        self.update_history({'role': agent.name, 'type': 'text', 'message': ''.join(response.chunks)})
                        
                        if response.text and not streaming:
                            if interface == 'cli':
                                output(f"\n{agent.name}:", response.text)
                            else:
                                await output({'type': wsquery['type'], 'content': response.text, 'action': wsquery['action'], 'complete': True})
                    
                    if not tool_calls:
                        streaming = False  
                    
                    self.save()
                    if not message:
                        break
                except Exception as e:
                    logger.warn(e)
                    continue
                
        except Exception as e:
            logger.exception(e)
