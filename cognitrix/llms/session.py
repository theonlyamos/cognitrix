import uuid
import asyncio
import logging
import logging
import aiofiles
from rich import print
from flask import json
from datetime import datetime
from pydantic import BaseModel, Field
from typing import IO, Any, Callable, List, Literal, Optional, Dict, Self

from cognitrix.agents import Agent
from cognitrix.agents import AIAssistant
from cognitrix.config import SESSIONS_FILE
from cognitrix.llms.base import LLMResponse

logger = logging.getLogger('cognitrix.log')

class Session(BaseModel):
    chat: List[Dict[str, str]] = []
    """The chat history of the session"""
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The session id"""
    
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
    async def _load_sessions_from_file(cls) -> Dict[str, Dict]:
        async with aiofiles.open(SESSIONS_FILE, 'r') as file:
            content = await file.read()
            return json.loads(content) if content else {}

    @classmethod
    async def _save_sessions_to_file(cls, sessions: Dict[str, Dict]):
        async with aiofiles.open(SESSIONS_FILE, 'w') as file:
            await file.write(json.dumps(sessions, indent=4))

    async def save(self):
        """Save current session"""
        sessions = await self._load_sessions_from_file()
        sessions[self.id] = self.dict()
        await self._save_sessions_to_file(sessions)
        return self.id

    @classmethod
    async def load(cls, session_id: Optional[str]) -> Self:
        """Load an existing session or create a new one if it doesn't exist"""
        sessions = await cls._load_sessions_from_file()
        session_data = None
        if session_id:
            session_data = sessions.get(session_id)
        
        if not session_data:
            new_session = cls()
            await new_session.save()
            session_data =  new_session.dict()
            
        return cls(**session_data)

    @classmethod
    async def list_sessions(cls) -> List[Self]:
        sessions = await cls._load_sessions_from_file()
        return [cls(**session_data) for session_data in sessions.values()]

    @classmethod
    async def delete(cls, session_id: str):
        """Delete session by id"""
        sessions = await cls._load_sessions_from_file()
        if session_id in sessions:
            del sessions[session_id]
            await cls._save_sessions_to_file(sessions)
            return True
        return False

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
        sessions = await cls._load_sessions_from_file()
        for session_data in sessions.values():
            if session_data.get('agent_id') == agent_id:
                return cls(**session_data)
        new_session = cls(agent_id=agent_id)
        await new_session.save()
        return new_session
    
    @classmethod
    async def get_by_task_id(cls, task_id: str) -> List[Self]:
        """Retrieve a session by task_id"""
        sessions = await cls._load_sessions_from_file()
        task_sessions: List[Self] = []
        for session_data in sessions.values():
            if session_data.get('task_id') == task_id:
                task_sessions.append(cls(**session_data))

        return task_sessions
    
    async def __call__(self, message: str|dict, agent: Agent|AIAssistant, interface: Literal['cli', 'web'] = 'cli', streaming: bool = False, output: Callable = print, wsquery: Dict[str, str]= {}, save_history: bool = True):
        system_prompt = agent.formatted_system_prompt()
        tool_calls: bool = False
        
        try:
            if not agent:
                raise Exception('Agent not initialized')
            
            while message:
                try:
                    full_prompt = agent.process_prompt(message)
                    message = ''
                    response: LLMResponse | None = None
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
                    await self.save()
                    if not message:
                        break
                except Exception as e:
                    logger.warn(e)
                    continue
                
        except Exception as e:
            logger.exception(e)
