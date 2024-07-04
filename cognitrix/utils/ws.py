from fastapi import WebSocket
import json
import logging
from starlette.websockets import WebSocketDisconnect

from cognitrix.llms.session import Session

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
                
                if query['type'] == 'chat_history':
                    session_id = query['session_id']
                    session = await Session.load(session_id)

                    loaded_agent = await web_agent.get(session.agent_id)
                    if loaded_agent:
                        web_agent = loaded_agent
                        self.websocket = websocket

                    await websocket.send_json({'type': 'chat_history', 'content': session.chat, 'agent_name': web_agent.name})
                elif query['type'] == 'sessions':
                    if query['action'] == 'list':
                        sessions = [sess.dict() for sess in Session.list_sessions()]
                        await websocket.send_json({'type': 'sessions', 'action': 'list', 'content': sessions})
                    elif query['action'] == 'get':
                        agent_id = query['agent_id']
                        loaded_agent = await web_agent.get(agent_id)
                        if loaded_agent:
                            web_agent = loaded_agent
                            self.websocket = websocket
                            session = await loaded_agent.load_session()
                            await websocket.send_json({'type': 'sessions', 'action': 'get', 'agent_name': web_agent.name, 'session': session.dict()})
                elif query['type'] == 'generate':
                    prompt = query['prompt']
                    async for response in web_agent.generate(prompt):
                        await websocket.send_json({'type': 'generate_response', 'data': response.text})
                else:
                    user_prompt = query['content']
                    response = await web_agent.chat(user_prompt, session)
                    await websocket.send_json({'type': 'chat_reply', 'content': response})
        except WebSocketDisconnect:
            logger.warning('Websocket disconnected')
            self.websocket = None
        except Exception as e:
            logger.exception(e)
            self.websocket = None