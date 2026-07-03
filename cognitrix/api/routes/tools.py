from fastapi import APIRouter

from ...tools.base import ToolManager

tools_api = APIRouter(
    prefix='/tools'
)


@tools_api.get('')
async def list_tools():
    # Return plain dicts so FastAPI's encoder handles datetime/UUID fields.
    return [tool.dict() for tool in ToolManager.list_all_tools()]


@tools_api.get('/{tool_name}')
async def load_tool(tool_name: str):
    tool = ToolManager.get_by_name(tool_name)
    return tool.dict() if tool else {}
