import time
from typing import Optional
from fastapi import WebSocket
import json
import logging
from starlette.websockets import WebSocketDisconnect
from cognitrix.agents.prompt_generator import PromptGenerator

from cognitrix.llms.session import Session
from cognitrix.agents import Agent, AIAssistant

logger = logging.getLogger('cognitrix.log')

class WebSocketManager:
    def __init__(self, agent):
        self.agent = agent

    async def websocket_endpoint(self, websocket: WebSocket):
        web_agent = self.agent
        await websocket.accept()
        session = await web_agent.load_session()
        try:
            self.websocket = websocket
            while True:
                data = await websocket.receive_text()
                query = json.loads(data)
                action = query.get('action', '')
                query_type = query['type']
                
                if query_type == 'chat_history':
                    session_id = query['session_id']
                    
                    if action == 'get':
                        session = await Session.load(session_id)

                        loaded_agent: Optional[Agent] = await web_agent.get(session.agent_id)
                        if loaded_agent:
                            web_agent = loaded_agent
                            web_agent.websocket = websocket

                        await websocket.send_json({'type': query_type, 'content': session.chat, 'agent_name': web_agent.name, 'action': action})
                    
                    elif action == 'delete':
                        session = await Session.load(session_id)
                        session.chat = []
                        loaded_agent: Optional[Agent] = await web_agent.get(session.agent_id)
                        if loaded_agent:
                            web_agent: Agent = loaded_agent
                            web_agent.llm.chat_history = session.chat
                            web_agent.save_session(session)
                            await web_agent.save()
                            await websocket.send_json({'type': query_type, 'content': session.chat, 'agent_name': web_agent.name, 'action': action})
                            
                elif query_type == 'sessions':
                    if action == 'list':
                        sessions = [sess.dict() for sess in Session.list_sessions()]
                        await websocket.send_json({'type': query_type, 'content': sessions, 'action': action})
                    
                    elif action == 'get':
                        agent_id = query['agent_id']
                        loaded_agent = await web_agent.get(agent_id)
                        if loaded_agent:
                            print(loaded_agent.name)
                            web_agent = loaded_agent
                            self.websocket = websocket
                            session = await loaded_agent.load_session()
                            await websocket.send_json({'type': query_type, 'agent_name': web_agent.name, 'content': session.dict(), 'action': action})
                
                elif query_type == 'generate':
                    default_prompt = query['prompt']
                    prompt = query['prompt']
                    name = query.get('name', '')
                    
                    if action == 'system_prompt':
                        agent = PromptGenerator(llm=web_agent.llm)
                        agent.llm.system_prompt = agent.prompt_template
                        
                        prompt = "## Agent Description"
                        if name:
                            prompt += f"""\n\n## Agent Name: {name}"""
                        
                        prompt += f"""\n\n{default_prompt}"""
                        
                    async for response in web_agent.generate(prompt):
                        await websocket.send_json({'type': query_type, 'content': response.current_chunk, 'action': action})
                        time.sleep(1)
                else:
                    user_prompt = query['content']
                    await web_agent.chat(user_prompt, session)
                    # await websocket.send_json({'type': 'chat_reply', 'content': response})
        except WebSocketDisconnect:
            logger.warning('Websocket disconnected')
            self.websocket = None
        except Exception as e:
            logger.exception(e)
            self.websocket = None