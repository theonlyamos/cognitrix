import time
from typing import Optional
from fastapi import WebSocket
import json
import logging
from cognitrix.utils import xml_to_dict
from starlette.websockets import WebSocketDisconnect
from cognitrix.agents import PromptGenerator
from cognitrix.agents import TaskInstructor

from cognitrix.llms.session import Session
from cognitrix.agents import Agent

logger = logging.getLogger('cognitrix.log')

class WebSocketManager:
    def __init__(self, agent):
        self.agent = agent
    
    async def websocket_endpoint(self, websocket: WebSocket):
        web_agent = self.agent
        await websocket.accept()
        session = await Session.get_by_agent_id(web_agent.id)
        try:
            self.websocket = websocket
            while True:
                data = await websocket.receive_text()
                query = json.loads(data)
                action = query.get('action', '')
                query_type = query['type']
                
                try:
                    if query_type == 'chat_history':
                        session_id = query['session_id']
                        
                        if action == 'get':
                            session = await Session.load(session_id)

                            loaded_agent: Optional[Agent] = await web_agent.get(session.agent_id)
                            if loaded_agent:
                                web_agent = loaded_agent
                            
                            chat_history = []
                            
                            for chat in session.chat:
                                chat_copy = chat.copy()
                                result = xml_to_dict(chat['message'])
                
                                if isinstance(result, dict):
                                    if isinstance(result['response'], dict):
                                        if 'artifacts' in result['response'].keys() and len(result['response']['artifacts'].keys()):
                                            chat_copy['artifacts'] = result['response']['artifacts']['artifact']
                                        if 'result' in result['response'].keys():
                                            chat_copy['message'] = result['response']['result']
                                            chat_history.append(chat_copy)
                                    else:
                                        chat_history.append(chat)
                                else:
                                    chat_history.append(chat)
                            
                            await websocket.send_json({'type': query_type, 'content': chat_history, 'agent_name': web_agent.name, 'action': action})
                        
                        elif action == 'delete':
                            session = await Session.load(session_id)
                            session.chat = []
                            session.save()
                            await websocket.send_json({'type': query_type, 'content': session.chat, 'agent_name': web_agent.name, 'action': action})
                                
                    elif query_type == 'sessions':
                        if action == 'list':
                            sessions = [sess.dict() for sess in await Session.list_sessions()]
                            await websocket.send_json({'type': query_type, 'content': sessions, 'action': action})
                        
                        elif action == 'get':
                            agent_id = query['agent_id']
                            loaded_agent = await web_agent.get(agent_id)
                            if loaded_agent:
                                web_agent = loaded_agent
                                self.websocket = websocket
                                session = await Session.get_by_agent_id(loaded_agent.id)
                                await websocket.send_json({'type': query_type, 'agent_name': web_agent.name, 'content': session.dict(), 'action': action})
                        
                        elif action == 'delete':
                            session_id = query['session_id']
                            await Session.delete(session_id)
                            sessions = [sess.dict() for sess in await Session.list_sessions()]
                            await websocket.send_json({'type': query_type, 'content': sessions, 'action': action})
                    
                    elif query_type == 'generate':
                        default_prompt = query['prompt']
                        prompt = ''
                        name = query.get('name', '')
                        agent = web_agent
                        
                        if action == 'system_prompt':
                            agent = PromptGenerator(llm=web_agent.llm)
                            # agent.llm.system_prompt = agent.system_prompt
                            
                            prompt = "Agent Description"
                            if name:
                                prompt += f"""\n\nAgent Name: {name}"""
                            
                            prompt += f"""\n\n{default_prompt}"""
                        
                        elif action == 'task_instructions': 
                            agent = TaskInstructor(llm=web_agent.llm)
                            
                            prompt = ""
                            if name:
                                prompt += f"""\\nTask Title: {name}"""
                            
                            prompt += f"""\n\nTask Description: {default_prompt}"""
                        
                        await session(prompt, agent, 'web', True, websocket.send_json, query, False)
                
                    else:
                        user_prompt = query['content']
                        await session(user_prompt, web_agent, 'web', True, websocket.send_json, query)
                        # await websocket.send_json({'type': 'chat_reply', 'content': response})
                except Exception as e:
                    logger.exception(e)
                    continue
                
        except WebSocketDisconnect:
            logger.warning('Websocket disconnected')
            self.websocket = None
            
        except Exception as e:
            logger.exception(e)
            self.websocket = None