from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, crud_scope, get_auth_context
from cognitrix.skills import personal
from cognitrix.skills.drafts import DraftError
from cognitrix.skills.manager import get_skill_manager

# Shared skills come from the builtin + user (~/.agents/skills) + project
# registries and are the same for everyone. Each person also has their own
# (skills/personal.py). The chat slash-menu lists both; the agent loads one
# with the `load_skill` tool when a `/name` message matches.
skills_api = APIRouter(
    prefix='/skills',
    dependencies=[Depends(crud_scope)]
)


def _me(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


@skills_api.get('')
async def list_skills(ctx: AuthContext = Depends(get_auth_context)):
    manager = get_skill_manager()
    mine = await personal.skills_of(_me(ctx))
    own_names = {row.name for row in mine}
    out = [
        {'name': row.name, 'description': row.description, 'category': 'personal',
         'argument_hint': '', 'tags': [], 'scope': 'mine'}
        for row in mine
    ]
    out.extend(
        {
            'name': s.name,
            'description': s.description,
            'category': s.category,
            'argument_hint': s.argument_hint or '',
            'tags': s.tags,
            'scope': 'everyone',
        }
        for s in await manager.list_skills()
        if getattr(s, 'user_invocable', True) and s.name not in own_names
    )
    return out


@skills_api.get('/mine')
async def my_skills(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    return {
        'skills': [personal.public(row) for row in await personal.skills_of(_me(ctx))],
        'can_share': await agent_access.is_admin(ctx.user),
    }


class SkillBody(BaseModel):
    name: str | None = None
    description: str | None = None
    body: str | None = None


@skills_api.post('/mine')
async def add_my_skill(body: SkillBody, ctx: AuthContext = Depends(get_auth_context)):
    try:
        row = await personal.create(_me(ctx), body.name or '', body.description or '', body.body or '')
    except DraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return personal.public(row)


async def _mine(skill_id: str, ctx: AuthContext) -> personal.PersonalSkill:
    row = await personal.PersonalSkill.get(skill_id)
    if row is None or str(row.user_id) != _me(ctx):
        raise HTTPException(status_code=404, detail='Skill not found')
    return row


@skills_api.patch('/mine/{skill_id}')
async def edit_my_skill(skill_id: str, body: SkillBody, ctx: AuthContext = Depends(get_auth_context)):
    row = await _mine(skill_id, ctx)
    try:
        row = await personal.update(row, name=body.name, description=body.description, body=body.body)
    except DraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return personal.public(row)


@skills_api.delete('/mine/{skill_id}')
async def delete_my_skill(skill_id: str, ctx: AuthContext = Depends(get_auth_context)):
    await personal.delete(await _mine(skill_id, ctx))
    return {'status': 'deleted'}


@skills_api.post('/mine/{skill_id}/share')
async def share_my_skill(skill_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Make one of my skills a shared one every agent on the server can load."""
    row = await _mine(skill_id, ctx)
    if not await agent_access.is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can share a skill with everyone.')
    try:
        manifest = await personal.share_with_everyone(row)
    except DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Couldn't share the skill: {exc}") from None
    return {'status': 'shared', 'name': manifest.name}
