from fastapi import APIRouter, Depends

from cognitrix.common.security import crud_scope

from ...safety.permissions import default_behavior
from ...tools.base import ToolManager

# Previously unauthenticated — bringing tools under the same auth as every
# other resource router (the web UI already sends the JWT).
tools_api = APIRouter(
    prefix='/tools',
    dependencies=[Depends(crud_scope)]
)


@tools_api.get('')
async def list_tools():
    # Return plain dicts so FastAPI's encoder handles datetime/UUID fields.
    # default_permission tells the agent form what "Default" means per tool.
    return [
        {**tool.dict(), 'default_permission': default_behavior(tool)}
        for tool in ToolManager.list_all_tools()
    ]


@tools_api.get('/{tool_name}')
async def load_tool(tool_name: str):
    tool = ToolManager.get_by_name(tool_name)
    return tool.dict() if tool else {}
