"""
User interface components and initialization.
"""
import logging

logger = logging.getLogger('cognitrix.log')


async def start_web_ui(agent):
    """Initialize and start the web UI."""
    import uvicorn
    from fastapi import WebSocket, Query
    from dotenv import load_dotenv

    from cognitrix.utils.ws import WebSocketManager
    from cognitrix.utils.sse import SSEManager
    from cognitrix.agents import Agent
    from cognitrix.agents.templates import ASSISTANT_SYSTEM_PROMPT
    from cognitrix.tools.base import ToolManager
    from cognitrix.providers import LLM

    from ..api.main import app
    from ..common.security import verify_token

    load_dotenv()

    # If no agent is provided, create a default one
    if agent is None:
        from cognitrix.config import settings
        
        # Check if any agents exist
        agents = await Agent.all()
        if not agents:
            logger.info("No agents found. Creating default 'Assistant' agent...")
            
            # Get default provider from settings
            provider = settings.ai_provider
            llm = LLM.load_llm(provider)
            
            if not llm:
                # Fallback to groq if default fails
                llm = LLM.load_llm('groq')
            
            if llm:
                # Get all available tools
                all_tools = ToolManager.list_all_tools()
                
                # Create default agent
                agent = await Agent.create_agent(
                    name="Assistant",
                    provider=provider,
                    system_prompt=ASSISTANT_SYSTEM_PROMPT,
                    tools=['all']
                )
                
                if agent:
                    agent.tools = all_tools
                    await agent.save()
                    logger.info(f"Created default agent: {agent.name} with {len(all_tools)} tools")
                else:
                    logger.error("Failed to create default agent")
            else:
                logger.error("Failed to load LLM for default agent")

    ws_manager = WebSocketManager(agent)
    sse_manager = SSEManager(agent)

    @app.middleware("http")
    async def add_middleware_data(request, call_next):
        request.state.agent = agent
        request.state.sse_manager = sse_manager
        response = await call_next(request)
        return response

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: 'WebSocket', token: str = Query(None)):
        if token:
            user = await verify_token(token)
            if user:
                await ws_manager.websocket_endpoint(websocket)  # type: ignore
                return
        await websocket.close(code=4003, reason="Unauthorized")

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