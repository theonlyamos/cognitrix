"""Audit log: who, and which agent, did what. Admins see everyone's rows and
can filter by person; everyone else sees only their own. Browser sessions only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from cognitrix import audit
from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only

audit_api = APIRouter(prefix='/audit')


@audit_api.get('')
async def list_events(
    person: str = Query('', max_length=200),
    agent: str = Query('', max_length=200),
    kind: str = Query('', max_length=40),
    outcome: str = Query('', max_length=40),
    q: str = Query('', max_length=200),
    before: str = Query('', max_length=10),
    limit: int = Query(100, ge=1, le=500),
    ctx: AuthContext = Depends(jwt_only),
):
    from cognitrix.models import User

    me = agent_access.owner_key(ctx.user)
    admin = await agent_access.is_admin(ctx.user)
    user_id: str | None = me
    if admin:
        user_id = person or None
    result = await audit.page(user_id=user_id, agent_id=agent, kind=kind if kind in audit.KINDS else '',
                              outcome=outcome, text=q, before=before, limit=limit)
    result['admin'] = admin
    if admin:
        result['people'] = [
            {'id': str(u.id), 'name': u.name, 'email': u.email}
            for u in sorted(await User.all(), key=lambda u: str(u.name or u.email).lower())
        ]
    return result
