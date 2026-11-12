"""Skills that belong to one person.

Shared skills live in folders on the server (``SkillManager``) and every
agent of every person can load them, so only an admin may add one. A personal
skill is a row here: only its owner's chats and tasks see it, and its owner
can add, edit and delete it without asking anyone.

A personal skill may not take the name of a shared one. If a shared skill
with the same name is added later, the person's own still wins for them.
"""
from __future__ import annotations

import contextvars
from typing import Any

from odbms import Model

from cognitrix.skills.drafts import DraftError, clean

MAX_PER_PERSON = 200

# Summaries of the current person's skills for the turn that is running, so
# the system prompt can list them next to the shared ones.
personal_skills_ctx: contextvars.ContextVar[tuple[dict[str, str], ...]] = contextvars.ContextVar(
    'personal_skills', default=())


class PersonalSkill(Model):
    user_id: str
    """Principal key of the person the skill belongs to."""
    name: str
    description: str
    body: str
    author: str = ''
    """The agent that wrote it, when it was learned from work."""


async def skills_of(user_id: str | None) -> list[PersonalSkill]:
    if not user_id:
        return []
    try:
        rows = list(await PersonalSkill.find({'user_id': str(user_id)}) or [])
    except Exception as exc:
        if 'no such table' in str(exc).lower():
            return []
        raise
    return sorted(rows, key=lambda row: row.name)


async def get(user_id: str | None, name: str) -> PersonalSkill | None:
    if not user_id or not name:
        return None
    wanted = str(name).strip().lstrip('/').lower()
    return next((row for row in await skills_of(user_id) if row.name == wanted), None)


async def _shared_exists(name: str) -> bool:
    from cognitrix.skills.manager import get_skill_manager

    try:
        return await get_skill_manager().get_skill(name) is not None
    except Exception:
        return False


async def name_taken(user_id: str, name: str) -> bool:
    """True when the person can't use this name: a shared skill or one of theirs has it."""
    return await _shared_exists(name) or await get(user_id, name) is not None


async def create(user_id: str, name: str, description: str, body: str, *, author: str = '') -> PersonalSkill:
    name, description, body = clean(name, description, body)
    mine = await skills_of(user_id)
    if len(mine) >= MAX_PER_PERSON:
        raise DraftError('You have a lot of skills already. Delete some you no longer use first.')
    if any(row.name == name for row in mine):
        raise DraftError(f"You already have a skill called '{name}'")
    if await _shared_exists(name):
        raise DraftError(f"A shared skill called '{name}' already exists. Pick another name.")
    row = PersonalSkill(user_id=str(user_id), name=name, description=description, body=body, author=author)
    await row.save()
    return row


async def update(row: PersonalSkill, *, name: str | None = None, description: str | None = None,
                 body: str | None = None) -> PersonalSkill:
    new_name, new_description, new_body = clean(
        name if name is not None else row.name,
        description if description is not None else row.description,
        body if body is not None else row.body,
    )
    if new_name != row.name and await name_taken(row.user_id, new_name):
        raise DraftError(f"A skill called '{new_name}' already exists. Pick another name.")
    await PersonalSkill.update_one(
        {'id': row.id}, {'name': new_name, 'description': new_description, 'body': new_body})
    row.name, row.description, row.body = new_name, new_description, new_body
    return row


async def delete(row: PersonalSkill) -> None:
    await PersonalSkill.delete_many({'id': str(row.id)})


async def share_with_everyone(row: PersonalSkill) -> Any:
    """Turn a personal skill into a shared one (the caller checks for admin)."""
    from cognitrix.skills.manager import get_skill_manager

    if await _shared_exists(row.name):
        raise DraftError(f"A shared skill called '{row.name}' already exists. Rename yours first.")
    manifest = await get_skill_manager().create_skill(
        row.name, row.description, row.body, author=row.author or '', category='learned')
    await delete(row)
    return manifest


def manifest_of(row: PersonalSkill) -> Any:
    from cognitrix.skills.models import SkillManifest

    return SkillManifest(name=row.name, description=row.description, body=row.body,
                         author=row.author or '', category='personal')


def public(row: PersonalSkill) -> dict[str, Any]:
    data = row.json()
    created = data.get('created_at')
    return {
        'id': data.get('id'),
        'name': row.name,
        'description': row.description,
        'body': row.body,
        'author': row.author or '',
        'created_at': str(created).replace('T', ' ')[:19] if created else None,
    }


async def summaries(user_id: str | None) -> tuple[dict[str, str], ...]:
    return tuple({'name': row.name, 'description': row.description} for row in await skills_of(user_id))
