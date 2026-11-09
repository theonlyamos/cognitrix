"""Usage: how many tokens the signed-in person's agents used and roughly what
that cost. Browser sessions only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from cognitrix import usage
from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only

usage_api = APIRouter(prefix='/usage')


@usage_api.get('')
async def get_usage(days: int = Query(30, ge=1, le=365), ctx: AuthContext = Depends(jwt_only)):
    owner = agent_access.owner_key(ctx.user)
    result = await usage.summary(owner, days)
    names = {str(a.id): a.name for a in await agent_access.visible_agents(owner)}
    for row in result['by_agent']:
        row['agent_name'] = names.get(row['agent_id']) or ('Deleted agent' if row['agent_id'] else 'Other')
    return result
