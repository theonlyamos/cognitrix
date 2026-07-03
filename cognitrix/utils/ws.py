import asyncio
import json
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from cognitrix.agents import Agent, PromptGenerator
from cognitrix.prompts.generator import agent_generator, task_details_generator, team_details_generator
from cognitrix.sessions.base import Session
from cognitrix.tasks.base import Task
from cognitrix.tasks.handler import handle_multi_step_task, is_multi_step_task
from cognitrix.teams.base import Team
from cognitrix.tools.base import Tool

logger = logging.getLogger('cognitrix.log')

class WebSocketManager:
    def __init__(self, agent):
        self.agent = agent
        # Strong refs to fire-and-forget background tasks so the event loop
        # can't GC them mid-run; done-callback surfaces failures.
        self._bg_tasks: set[asyncio.Task] = set()
        from cognitrix.utils.core import register_websocket_manager
        register_websocket_manager(agent.id, self)

    def _spawn_bg(self, coro, websocket: 'WebSocket | None' = None):
        """Launch a background task, retaining a strong reference and logging
        (and optionally notifying the client of) any exception it raises."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)

        def _done(t: asyncio.Task):
            self._bg_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.exception("Background task failed", exc_info=exc)
                if websocket is not None:
                    # Track the notify task too, so it isn't GC'd before it sends.
                    notify = asyncio.create_task(
                        websocket.send_json({'type': 'error', 'content': f'Task failed: {exc}'})
                    )
                    self._bg_tasks.add(notify)
                    notify.add_done_callback(self._bg_tasks.discard)

        task.add_done_callback(_done)
        return task

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
                                await session.save()

                            if session.agent_id == web_agent.id:
                                loaded_agent = web_agent
                            else:
                                loaded_agent: Agent | None = await Agent.get(session.agent_id)

                            if loaded_agent:
                                web_agent = loaded_agent

                            chat_history = session.chat

                            await websocket.send_json({'type': query_type, 'content': chat_history, 'agent_name': web_agent.name, 'action': action})

                        elif action == 'delete':
                            session = await Session.load(session_id)
                            session.chat = []
                            await session.save()
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
                            available_agents = [agent.name for agent in await Agent.all()]
                            agent.system_prompt = agent.system_prompt.replace("{agents}", "\n".join(available_agents))

                            prompt += f"""{default_prompt}"""

                        if action == 'agent_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = agent_generator
                            available_tools = [tool.name for tool in Tool.list_all_tools()]
                            agent.system_prompt = agent.system_prompt.replace("{available_tools}", "\n".join(available_tools))

                            prompt = f"""{default_prompt}"""

                        elif action == 'task_details':
                            agent = PromptGenerator(llm=web_agent.llm)
                            agent.system_prompt = task_details_generator

                            prompt = f"""{default_prompt}"""

                        session.chat = []
                        await session(prompt, agent, interface='web', stream=True,
                                      output=websocket.send_json, wsquery=query, save_history=False)

                    elif query_type == 'multistep':
                        prompt = query.get('prompt', '')

                        if is_multi_step_task(prompt):
                            # Handle as multi-step task
                            await websocket.send_json({
                                'type': 'status',
                                'content': 'Planning multi-step task...'
                            })

                            try:
                                result = await handle_multi_step_task(
                                    prompt,
                                    web_agent,
                                    session,
                                    web_agent.llm,
                                    stream=False,
                                    interface='ws',
                                )
                                await websocket.send_json({
                                    'type': 'multistep_result',
                                    'content': result
                                })
                            except Exception as e:
                                await websocket.send_json({
                                    'type': 'error',
                                    'content': f'Multi-step task failed: {str(e)}'
                                })
                        else:
                            # Fall back to regular session
                            await session(prompt, web_agent, interface='web', stream=True,
                                          output=websocket.send_json, wsquery=query, save_history=False)

                    elif query_type == 'start_task':
                        task = query['task']
                        task_id = query.get('task_id', '')
                        team_id = query['team_id']
                        team = await Team.get(team_id)
                        if team:
                            if task_id:
                                task = await Task.get(task_id)
                            else:
                                task = await team.create_task(task['title'], task['description'])
                            if task:
                                # from cognitrix.utils.core import register_websocket_manager
                                # ws_proxy = WebSocketManagerProxy(self.agent.id)
                                # register_websocket_manager(task.id, self)
                                # result = run_team_task.delay(team_id, task.id)
                                # task.pid = result.id
                                # task.save()
                                await team.assign_task(task_id=task.id)
                                task_session = Session(team_id=team.id, task_id=task.id)
                                await task_session.save()
                                # work_on_task is a staticmethod taking `team`
                                # first — call it explicitly, not as team.work_on_task
                                # (which would misbind team to the task id).
                                self._spawn_bg(Team.work_on_task(team, task.id, task_session, self), websocket)
                            else:
                                await websocket.send_json({'type': 'error', 'content': 'Task not found'})
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
                        await session(user_prompt, web_agent, interface='web', stream=True,
                                      output=websocket.send_json, wsquery=query)
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

class WebSocketManagerProxy:
    """A serializable proxy for WebSocketManager that stores only the essential data"""
    def __init__(self, task_id: str):
        self.task_id = task_id

    async def send_team_message(self, sender: str, receiver: str, content: str):
        # Find the active WebSocketManager instance and delegate the message
        from cognitrix.utils.core import get_websocket_manager
        ws_manager = get_websocket_manager(self.task_id)
        if ws_manager:
            await ws_manager.send_team_message(sender, receiver, content)
