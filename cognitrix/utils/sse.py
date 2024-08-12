from typing import Optional
import json
import logging
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from cognitrix.agents import PromptGenerator
from cognitrix.llms.session import Session
from cognitrix.agents import Agent, AIAssistant
import asyncio

logger = logging.getLogger('cognitrix.log')

app = FastAPI()

class SSEManager:
    def __init__(self, agent):
        self.agent = agent
        self.action_queue = asyncio.Queue()

    async def sse_endpoint(self, request: Request):
        async def event_generator():
            while True:
                if await request.is_disconnected():
                    break

                try:
                    action = await asyncio.wait_for(self.action_queue.get(), timeout=1.0)
                    print('+++',action)
                except asyncio.TimeoutError:
                    yield {'event': 'ping', 'data': ''}
                    await asyncio.sleep(0.25)
                    continue

                if action['type'] == 'chat_history':
                    session_id = action['session_id']
                    
                    if action['action'] == 'get':
                        session = await Session.load(session_id)
                        loaded_agent: Optional[Agent] = await self.agent.get(session.agent_id)
                        if loaded_agent:
                            self.agent = loaded_agent
                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'get'})}
                    
                    elif action['action'] == 'delete':
                        session = await Session.load(session_id)
                        session.chat = []
                        loaded_agent: Optional[Agent] = await self.agent.get(session.agent_id)
                        if loaded_agent:
                            self.agent = loaded_agent
                            self.agent.llm.chat_history = session.chat
                            session.save()
                            await self.agent.save()
                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'delete'})}
                            
                elif action['type'] == 'sessions':
                    if action['action'] == 'list':
                        sessions = [sess.dict() for sess in Session.list_sessions()]
                        yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'content': sessions, 'action': 'list'})}
                    
                    elif action['action'] == 'get':
                        agent_id = action['agent_id']
                        if agent_id:
                            loaded_agent = await self.agent.get(agent_id)
                            if loaded_agent:
                                self.agent = loaded_agent
                                session = await loaded_agent.load_session()
                                yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'agent_name': self.agent.name, 'content': session.dict(), 'action': 'get'})}
                
                elif action['type'] == 'generate':
                    prompt = action['prompt']
                    name = action.get('name', '')
                    
                    if action['action'] == 'system_prompt':
                        agent = PromptGenerator(llm=self.agent.llm)
                        agent.llm.system_prompt = agent.system_prompt
                        
                        prompt = f"## Agent Description\n\n## Agent Name: {name}\n\n{prompt}"
                        
                    async for response in self.agent.generate(prompt):
                        yield {'event': 'message', 'data': json.dumps({'type': 'generate', 'content': response.current_chunk, 'action': 'system_prompt'})}
                
                elif action['type'] == 'chat_message':
                    user_prompt = action['content']
                    session = await Session.get_by_agent_id(self.agent.id)  # Ensure session is defined
                    async for response in self.agent.chat(user_prompt, session):
                        yield {'event': 'message', 'data': response}

        return EventSourceResponse(event_generator())