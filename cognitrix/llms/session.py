import uuid
import asyncio
import logging
import logging
from rich import print
from flask import json
from datetime import datetime
from pydantic import BaseModel, Field
from typing import IO, Any, Callable, List, Literal, Optional, Dict

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
    
    def save(self):
        """Save the current state of the session to disk"""
        sessions = Session.list_sessions()
        updated_sessions = []
        loaded_session: Optional[Session] = None
        loaded_session_index: int = 0
        
        for index, session in enumerate(sessions):
            if session.id == self.id:
                loaded_session = session
                loaded_session_index = index
        
        if loaded_session:
            sessions[loaded_session_index] = self
        else:
            sessions.append(self)
        
            
        for session in sessions:
            updated_sessions.append(session.dict())
            
        with open(SESSIONS_FILE, 'w') as file:
            json.dump(updated_sessions, file, indent=4)

    @staticmethod
    def list_sessions() -> List['Session']:
        """List all available sessions from disk"""
        sessions = []
        try:
            with open(SESSIONS_FILE, 'r') as file:
                content = file.read()
                sessions = json.loads(content)if content else []
                return [Session(**session) for session in sessions]
        except Exception as e:
            logging.exception(e)
            return []

    @classmethod
    async def load(cls, session_id: str):
        try:
            sessions = cls.list_sessions()
            loaded_sessions: list[Session] = [session for session in sessions if session.id == session_id]
            if len(loaded_sessions):
                session = loaded_sessions[-1]
                return session
            else:
                return Session()
        except Exception as e:
            logging.exception(e)
            return Session()
        
    @classmethod
    async def get_by_agent_id(cls, agent_id: str):
        try:
            sessions = cls.list_sessions()
            loaded_sessions: list[Session] = [session for session in sessions if session.agent_id == agent_id]
            
            if len(loaded_sessions):
                session = loaded_sessions[-1]
                return session
            else:
                return Session(agent_id=agent_id)
        except Exception as e:
            logging.exception(e)
            return Session()
    
    def update_history(self, message: Dict[str, str]):
        self.chat.append(message)

    async def agent(self):
        try:
            agents = await Agent.list_agents()
            loaded_agents: list[Agent] = [agent for agent in agents if agent.id == self.agent_id]
            if len(loaded_agents):
                return loaded_agents[0]
        except Exception as e:
            logger.exception(e)
            return None
    
    async def __call__(self, message: str|dict, agent: Agent|AIAssistant, interface: Literal['cli', 'web'] = 'cli', streaming: bool = False, output: Callable = print, wsquery: Dict[str, str]= {}):
        system_prompt = agent.formatted_system_prompt()
        tool_calls: bool = False
        
        try:
            if not agent:
                raise Exception('Agent not initialized')
            
            while message:
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
                    await asyncio.sleep(0.1)
            
                if response:
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
            logger.exception(e)