"""
User interface components and initialization.
"""
import logging

logger = logging.getLogger('cognitrix.log')


async def start_web_ui(agent):
    """Initialize and start the web UI."""
    from ..api.main import app
    from fastapi import WebSocket
    import uvicorn
    from cognitrix.utils.ws import WebSocketManager
    
    ws_manager = WebSocketManager(agent)
    
    @app.middleware("http")
    async def add_middleware_data(request, call_next):
        request.state.agent = agent
        response = await call_next(request)
        return response
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: 'WebSocket'):
        await ws_manager.websocket_endpoint(websocket) # type: ignore
        
    # Start the web server
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def prompt_agent(assistant, prompt):
    """Send a prompt to the agent and get response."""
    from cognitrix.sessions.base import Session
    session = await Session.get_by_agent_id(assistant.id)
    if not session:
        session = Session(agent_id=assistant.id)
    await session(prompt, assistant)
    return session 