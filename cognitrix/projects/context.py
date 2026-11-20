"""The project of the running turn, and what the turn takes from it.

``Session.__call__`` sets :data:`current_project` for a chat or task turn in a
project. Code that runs inside the turn (memory, knowledge search, the
sandbox, the paired PC) reads it from here instead of having the project
threaded through every call.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger('cognitrix.projects')


@dataclass(frozen=True)
class TurnProject:
    """A snapshot of the project for one turn."""
    id: str
    user_id: str
    name: str
    instructions: str = ''
    agents: tuple[str, ...] = ()
    folders: tuple[dict[str, Any], ...] = ()
    knowledge: tuple[str, ...] = ()
    computer: str = ''
    pc_folders: tuple[str, ...] = ()
    use_account_memory: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, row: Any) -> TurnProject:
        return cls(
            id=str(row.id), user_id=str(row.user_id), name=str(row.name or ''),
            instructions=str(row.instructions or ''),
            agents=tuple(str(a) for a in (row.agents or [])),
            folders=tuple(dict(f) for f in (row.folders or []) if isinstance(f, dict)),
            knowledge=tuple(str(k) for k in (row.knowledge or [])),
            computer=str(row.computer or ''),
            pc_folders=tuple(str(p) for p in (row.pc_folders or [])),
            use_account_memory=bool(row.use_account_memory),
        )

    @property
    def shared_sandbox(self) -> bool:
        """Every agent in the project works as one sandbox user on one desktop."""
        return self.computer == 'sandbox'

    @property
    def sandbox_name(self) -> str:
        return f'project-{self.id}'


current_project: ContextVar[TurnProject | None] = ContextVar('current_project', default=None)


def get() -> TurnProject | None:
    return current_project.get(None)


def set_project(project: TurnProject | None):
    return current_project.set(project)


def reset(token) -> None:
    try:
        current_project.reset(token)
    except ValueError:
        current_project.set(None)


async def for_session(session: Any, user_id: Any = None) -> TurnProject | None:
    """The project a chat (or the task it runs for) belongs to, or None."""
    owner = str(getattr(session, 'user_id', None) or user_id or '')
    project_id = str(getattr(session, 'project_id', None) or '')
    if not project_id and getattr(session, 'task_id', None):
        try:
            from cognitrix.tasks.base import Task

            task = await Task.get(str(session.task_id))
            project_id = str(getattr(task, 'project_id', None) or '') if task is not None else ''
            owner = owner or str(getattr(task, 'owner_id', None) or '')
        except Exception:
            logger.debug('Could not read the task project', exc_info=True)
    if not project_id or not owner:
        return None
    from cognitrix.projects.store import project_of

    row = await project_of(owner, project_id)
    return TurnProject.of(row) if row is not None and not row.archived else None


# ---------------------------------------------------------------- what the turn uses

def sandbox_identity(agent_id: Any) -> Any:
    """Whose desktop, Linux user and ~/work folder an agent uses in the sandbox.

    In a project set to the shared sandbox, every agent of the project uses
    the project's (``project-<id>``); otherwise each agent has its own."""
    project = get()
    if project is not None and project.shared_sandbox:
        return project.sandbox_name
    return agent_id


def prompt_note(project: TurnProject | None = None) -> str:
    project = project or get()
    if project is None:
        return ''
    lines = [f'## Project: {project.name}',
             'This chat is part of the project above. Its instructions apply to all work here.']
    if project.instructions.strip():
        lines += ['', project.instructions.strip()]
    if project.shared_sandbox:
        lines += ['', 'Every agent in this project shares one sandbox computer: the same desktop, '
                      'Linux user and ~/work folder. Expect files other project agents made.']
    return '\n'.join(lines)


def knowledge_for(agent: Any) -> list[str]:
    """The agent's own knowledge collections plus the project's."""
    own = [str(c) for c in (getattr(agent, 'knowledge', None) or [])]
    project = get()
    if project is None:
        return own
    return list(dict.fromkeys([*own, *project.knowledge]))


def narrow_pc_folders(folders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A paired PC's shared folders, limited to the ones the project uses."""
    project = get()
    if project is None or not project.pc_folders or not project.computer \
            or project.computer == 'sandbox':
        return folders
    wanted = {_norm(p) for p in project.pc_folders}
    return [f for f in folders if _norm(str(f.get('path') or '')) in wanted]


def _norm(path: str) -> str:
    return path.replace('\\', '/').rstrip('/').lower()
