from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...tools import Tool

tools_api = APIRouter(
    prefix='/tools'
)

@tools_api.get('')
async def list_tools():
    tools = Tool.list_all_tools()
    response = [tool.dict() for tool in tools]
    
    return response

@tools_api.get('/{tool_name}')
async def load_agent(tool_name: str):
    tool = Tool.get_by_name(tool_name)
    response = {}
    if tool:
        response = tool.dict()
    
    return JSONResponse(response)