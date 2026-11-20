"""Reading and writing projects, and how the API shows them.

Every read is by the person (``user_id``): one person's projects never show
up for another. Only the owner may change a project.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cognitrix.projects.models import Project

logger = logging.getLogger('cognitrix.projects')

FMT = '%Y-%m-%d %H:%M:%S'
MAX_PER_USER = 200
MAX_NAME = 80
MAX_DESCRIPTION = 300
MAX_INSTRUCTIONS = 20_000
MAX_AGENTS = 20
COLORS = ('', 'blue', 'green', 'amber', 'red', 'violet', 'teal', 'pink', 'slate')
INSTRUCTION_FILES = ('AGENTS.md', 'CLAUDE.md', '.cursorrules')
"""Read (first one found) into a new project's instructions when it starts
from a folder and none were typed."""


class ProjectError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now_str() -> str:
    return datetime.now(UTC).replace(tzinfo=None).strftime(FMT)


# ---------------------------------------------------------------- reading

async def projects_of(user_id: str, *, archived: bool | None = False) -> list[Project]:
    rows = list(await Project.find({'user_id': str(user_id)}) or [])
    if archived is not None:
        rows = [p for p in rows if bool(p.archived) == archived]
    rows.sort(key=lambda p: p.updated or p.created, reverse=True)
    rows.sort(key=lambda p: not p.pinned)
    return rows


async def project_of(user_id: Any, project_id: Any) -> Project | None:
    """The person's own project by id, or None."""
    if not user_id or not project_id:
        return None
    try:
        row = await Project.get(str(project_id))
    except Exception:
        row = None
    if row is None or str(row.user_id) != str(user_id):
        return None
    return row


# ---------------------------------------------------------------- cleaning

def _text(value: Any, limit: int) -> str:
    return str(value or '').strip()[:limit]


def clean_agents(value: Any) -> list[str]:
    out: list[str] = []
    for item in value or []:
        key = str(item or '').strip()
        if key and key not in out:
            out.append(key)
    return out[:MAX_AGENTS]


def clean_folders(value: Any) -> list[dict[str, Any]]:
    from cognitrix.common.folders import FolderError, folders_to_dicts, normalize_folders

    try:
        return folders_to_dicts(normalize_folders(value or [], validate=True))
    except FolderError as exc:
        raise ProjectError('bad_folder', str(exc)) from None


async def clean_knowledge(user_id: str, value: Any) -> list[str]:
    wanted = [str(v) for v in (value or []) if str(v or '').strip()]
    if not wanted:
        return []
    try:
        from cognitrix import knowledge

        mine = {str(c.id) for c in await knowledge.collections(str(user_id))}
    except Exception:
        logger.debug('Knowledge unavailable', exc_info=True)
        return []
    return [c for c in dict.fromkeys(wanted) if c in mine]


async def clean_computer(user_id: str, value: Any) -> str:
    computer = str(value or '').strip()
    if computer in ('', 'sandbox'):
        return computer
    from cognitrix.computer.paired.store import device_of

    if await device_of(user_id, computer) is None:
        raise ProjectError('unknown_computer', 'That computer is not one of your paired PCs.')
    return computer


def instructions_from(folders: list[dict[str, Any]]) -> str:
    """The first AGENTS.md / CLAUDE.md found in the project's folders."""
    for folder in folders:
        for name in INSTRUCTION_FILES:
            path = Path(str(folder.get('path') or '')) / name
            try:
                if path.is_file() and path.stat().st_size <= MAX_INSTRUCTIONS * 2:
                    return path.read_text(encoding='utf-8', errors='replace').strip()[:MAX_INSTRUCTIONS]
            except OSError:
                continue
    return ''


# ---------------------------------------------------------------- writing

async def create(user_id: str, data: dict[str, Any]) -> Project:
    name = _text(data.get('name'), MAX_NAME)
    if not name:
        raise ProjectError('name_required', 'A project needs a name.')
    existing = await projects_of(user_id, archived=None)
    if len(existing) >= MAX_PER_USER:
        raise ProjectError('too_many', f'You already have {MAX_PER_USER} projects.')
    folders = clean_folders(data.get('folders'))
    instructions = _text(data.get('instructions'), MAX_INSTRUCTIONS)
    if not instructions and data.get('read_instructions', True):
        instructions = instructions_from(folders)
    color = str(data.get('color') or '')
    stamp = now_str()
    row = Project(
        user_id=str(user_id), name=name,
        description=_text(data.get('description'), MAX_DESCRIPTION),
        instructions=instructions,
        color=color if color in COLORS else '',
        agents=clean_agents(data.get('agents')),
        folders=folders,
        knowledge=await clean_knowledge(user_id, data.get('knowledge')),
        computer=await clean_computer(user_id, data.get('computer')),
        pc_folders=[str(p) for p in (data.get('pc_folders') or []) if str(p or '').strip()][:20],
        use_account_memory=bool(data.get('use_account_memory', True)),
        created=stamp, updated=stamp,
    )
    await row.save()
    if not row.id:
        row = await Project.find_one({'user_id': str(user_id), 'created': stamp, 'name': name}) or row
    return row


async def update(row: Project, data: dict[str, Any]) -> Project:
    changes: dict[str, Any] = {}
    if 'name' in data:
        name = _text(data.get('name'), MAX_NAME)
        if not name:
            raise ProjectError('name_required', 'A project needs a name.')
        changes['name'] = name
    if 'description' in data:
        changes['description'] = _text(data.get('description'), MAX_DESCRIPTION)
    if 'instructions' in data:
        changes['instructions'] = _text(data.get('instructions'), MAX_INSTRUCTIONS)
    if 'color' in data:
        color = str(data.get('color') or '')
        changes['color'] = color if color in COLORS else ''
    if 'agents' in data:
        changes['agents'] = clean_agents(data.get('agents'))
    if 'folders' in data:
        changes['folders'] = clean_folders(data.get('folders'))
    if 'knowledge' in data:
        changes['knowledge'] = await clean_knowledge(row.user_id, data.get('knowledge'))
    if 'computer' in data:
        changes['computer'] = await clean_computer(row.user_id, data.get('computer'))
    if 'pc_folders' in data:
        changes['pc_folders'] = [str(p) for p in (data.get('pc_folders') or []) if str(p or '').strip()][:20]
    for flag in ('use_account_memory', 'pinned', 'archived'):
        if flag in data:
            changes[flag] = bool(data.get(flag))
    if not changes:
        return row
    changes['updated'] = now_str()
    await Project.update_one({'id': row.id}, changes)
    return await Project.get(row.id) or row


async def delete(row: Project, *, delete_chats: bool = False) -> int:
    """Remove a project. Its chats and tasks stay (out of the project) unless
    ``delete_chats``. Its project memories are removed. Returns chats touched."""
    from cognitrix.models.memory import Memory
    from cognitrix.sessions.base import Session

    sessions = list(await Session.find({'project_id': str(row.id)}) or [])
    for session in sessions:
        if str(session.user_id or '') != str(row.user_id):
            continue
        if delete_chats:
            await Session.delete_many({'id': session.id})
        else:
            await Session.update_one({'id': session.id}, {'project_id': None})
    try:
        from cognitrix.tasks.base import Task

        for task in await Task.find({'project_id': str(row.id)}) or []:
            await Task.update_one({'id': task.id}, {'project_id': None})
    except Exception:
        logger.debug('Could not clear task projects', exc_info=True)
    try:
        await Memory.delete_many({'user_id': str(row.user_id), 'project_id': str(row.id)})
    except Exception:
        logger.debug('Could not remove project memories', exc_info=True)
    await Project.delete_many({'id': row.id})
    return len(sessions)


async def move_chat(user_id: str, session_id: str, project_id: str | None) -> Any:
    """Put a chat in a project (or take it out with None). New-chat settings
    (folders, computer) are not changed for a chat that already ran."""
    from cognitrix.sessions.base import Session

    session = await Session.get(str(session_id))
    if session is None or str(session.user_id or '') != str(user_id):
        raise ProjectError('chat_not_found', 'Chat not found.', 404)
    if project_id:
        project = await project_of(user_id, project_id)
        if project is None:
            raise ProjectError('project_not_found', 'Project not found.', 404)
        changes: dict[str, Any] = {'project_id': str(project.id)}
        if not session.chat:
            changes.update(new_chat_settings(project))
    else:
        changes = {'project_id': None}
    await Session.update_one({'id': session.id}, changes)
    return await Session.get(session.id)


def new_chat_settings(project: Project) -> dict[str, Any]:
    """What a new chat in the project starts with."""
    out: dict[str, Any] = {}
    if project.folders:
        out['folders'] = list(project.folders)
    if project.computer == 'sandbox':
        out['run_in_sandbox'] = True
        out['computer'] = None
    elif project.computer:
        out['computer'] = project.computer
    return out


# ---------------------------------------------------------------- views

async def counts(user_id: str) -> dict[str, dict[str, Any]]:
    """Chats per project and when one last changed, for the cards."""
    from cognitrix.sessions.base import Session

    out: dict[str, dict[str, Any]] = {}
    try:
        rows = await Session.find({'user_id': str(user_id)}) or []
    except Exception:
        return out
    for session in rows:
        pid = str(getattr(session, 'project_id', '') or '')
        if not pid or getattr(session, 'archived', False) or getattr(session, 'incognito', False):
            continue
        entry = out.setdefault(pid, {'chats': 0, 'last': ''})
        entry['chats'] += 1
        when = str(getattr(session, 'datetime', '') or '')
        entry['last'] = max(entry['last'], when)
    return out


def view(row: Project, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'id': str(row.id),
        'name': row.name,
        'description': row.description,
        'instructions': row.instructions,
        'color': row.color or None,
        'agents': list(row.agents or []),
        'folders': list(row.folders or []),
        'knowledge': list(row.knowledge or []),
        'computer': row.computer or None,
        'pc_folders': list(row.pc_folders or []),
        'use_account_memory': bool(row.use_account_memory),
        'pinned': bool(row.pinned),
        'archived': bool(row.archived),
        'created': row.created,
        'updated': row.updated or row.created,
        **(extra or {}),
    }
