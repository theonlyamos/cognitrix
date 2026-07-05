from fastapi import APIRouter, Depends

from cognitrix.common.security import crud_scope
from cognitrix.skills.manager import get_skill_manager

# Skills come from the builtin + user (~/.agents/skills) + project registries.
# The chat slash-menu only lists user-invocable skills; the agent loads them via
# the `load_skill` tool when a `/name` message matches.
skills_api = APIRouter(
    prefix='/skills',
    dependencies=[Depends(crud_scope)]
)


@skills_api.get('')
async def list_skills():
    manager = get_skill_manager()
    skills = await manager.list_skills()
    return [
        {
            'name': s.name,
            'description': s.description,
            'category': s.category,
            'argument_hint': s.argument_hint or '',
            'tags': s.tags,
        }
        for s in skills
        if getattr(s, 'user_invocable', True)
    ]
