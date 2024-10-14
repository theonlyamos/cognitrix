import time
from typing import Optional
from fastapi import WebSocket
import json
import logging
from cognitrix.teams.base import Team
from cognitrix.tools.base import Tool
from cognitrix.utils import xml_to_dict
from starlette.websockets import WebSocketDisconnect
from cognitrix.agents import PromptGenerator
from cognitrix.agents import TaskInstructor

from cognitrix.providers.session import Session
from cognitrix.agents import Agent
from cognitrix.prompts.generator import team_details_generator, agent_details_generator, task_details_generator

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
                            
                            if not session.agent_id:
                                session.agent_id = web_agent.id
                                session.save()
                            
                            if session.agent_id == web_agent.id:
                                loaded_agent = web_agent
                            else:
                                loaded_agent: Optional[Agent] = Agent.get(session.agent_id)
                            
                            if loaded_agent:
                                web_agent = loaded_agent
                            
                            chat_history = session.chat
                            
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
                            loaded_agent = web_agent.get(agent_id)
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
                            
                            prompt = "Agent Description"
                            if name:
                                prompt += f"""\n\nAgent Name: {name}"""
                            
                            prompt += f"""\n\n{default_prompt}"""
                            
                        if action == 'team_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = team_details_generator
                            available_agents = [agent.name for agent in Agent.all()]
                            agent.system_prompt = agent.system_prompt.replace("{agents}", "\n".join(available_agents))
                            
                            prompt += f"""{default_prompt}"""
                        
                        if action == 'agent_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = agent_details_generator
                            available_tools = [tool.name for tool in Tool.list_all_tools()]
                            agent.system_prompt = agent.system_prompt.replace("{available_tools}", "\n".join(available_tools))
                            
                            prompt = f"""{default_prompt}"""
                            
                        elif action == 'task_details': 
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = task_details_generator
                            
                            prompt = f"""{default_prompt}"""
                        
                        session.chat = []
                        await session(prompt, agent, 'web', True, websocket.send_json, query, False)
                
                    elif query_type == 'start_task':
                        task = query['task']
                        team_id = query['team_id']
                        team = Team.get(team_id)
                        if team:
                            task = team.create_task(task['title'], task['description'])
                            task_session = Session(team_id=team_id, task_id=task.id)
                            task_session.save()
                            await team.work_on_task(task.id, task_session, self)
                        else:
                            await websocket.send_json({'type': 'error', 'content': 'Team not found'})
                    # elif query_type == "websocket.receive":
                    #     if "text" in message:
                    #         data = message["text"]
                    #         query = json.loads(data)
                    #         action = query.get('action', '')
                    #         query_type = query['type']
                            
                    #         # Handle existing text-based messages
                    #         # ... (existing code for handling different query types)

                    #     elif "bytes" in message:
                    #         # Handle incoming audio data
                    #         audio_chunk = message["bytes"]
                    #         if not self.is_recording:
                    #             self.is_recording = True
                    #             self.audio_chunks = []
                            
                    #         self.audio_chunks.append(audio_chunk)

                    #         # Check if this is the last chunk (you may need to implement a way to signal the end of recording)
                    #         if len(audio_chunk) == 0 or (query_type == "audio" and action == "stop"):
                    #             self.is_recording = False
                    #             await self.process_audio()        
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

    async def send_team_message(self, sender: str, receiver: str, content: str):
        if self.websocket:
            await self.websocket.send_json({
                'type': 'team_message',
                'sender': sender,
                'receiver': receiver,
                'content': content
            })
        else:
            logger.warning("WebSocket is not connected. Unable to send team message.")