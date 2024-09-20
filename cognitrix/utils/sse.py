from typing import Optional
import json
import logging
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
from cognitrix.agents import PromptGenerator
from cognitrix.agents.generators import TaskInstructor
from cognitrix.llms.session import Session
from cognitrix.agents import Agent
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
                            
                        if not session.agent_id:
                            session.agent_id = self.agent.id
                            session.save()
                        
                        if session.agent_id == self.agent.id:
                            loaded_agent = self.agent
                        else:
                            loaded_agent: Optional[Agent] = Agent.get(session.agent_id)
                        
                        if loaded_agent:
                            self.agent = loaded_agent

                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'get'})}
                    
                    elif action['action'] == 'delete':
                        session = await Session.load(session_id)
                        session.chat = []
                        session.save()
                        yield {'event': 'message', 'data': json.dumps({'type': 'chat_history', 'content': session.chat, 'agent_name': self.agent.name, 'action': 'delete'})}
                            
                elif action['type'] == 'sessions':
                    if action['action'] == 'list':
                        sessions = [sess.model_dump() for sess in await Session.list_sessions()]
                        yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'content': sessions, 'action': 'list'})}
                    
                    elif action['action'] == 'get':
                        agent_id = action['agent_id']
                        if agent_id:
                            loaded_agent = Agent.get(agent_id)
                            if loaded_agent:
                                self.agent = loaded_agent
                                session = await Session.get_by_agent_id(loaded_agent.id)
                                yield {'event': 'message', 'data': json.dumps({'type': 'sessions', 'agent_name': self.agent.name, 'content': session.dict(), 'action': 'get'})}
                
                elif action['type'] == 'generate':
                    default_prompt = action['prompt']
                    prompt = ''
                    name = action.get('name', '')
                    agent = self.agent
                    
                    if action == 'system_prompt':
                        agent = PromptGenerator(llm=agent.llm)
                        
                        prompt = "Agent Description"
                        if name:
                            prompt += f"""\n\nAgent Name: {name}"""
                        
                        prompt += f"""\n\n{default_prompt}"""
                    
                    elif action == 'task_instructions': 
                        agent = TaskInstructor(llm=agent.llm)
                        
                        prompt = ""
                        if name:
                            prompt += f"""\\nTask Title: {name}"""
                        
                        prompt += f"""\n\nTask Description: {default_prompt}"""
                        
                    async for response in agent.generate(prompt):
                        yield {'event': 'message', 'data': json.dumps({'type': 'generate', 'content': response.current_chunk, 'action': 'system_prompt'})}
                
                elif action['type'] == 'chat_message':
                    user_prompt = action['content']
                    async for response in self.agent.generate(user_prompt):
                        yield {'event': 'message', 'data': json.dumps({'type': 'generate', 'content': response.current_chunk, 'action': 'chat_message'})}

        return EventSourceResponse(event_generator())