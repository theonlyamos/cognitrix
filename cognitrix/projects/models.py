"""The Project table (cognitrix/projects).

Persistence rule: ``save()`` once at creation, ``update_one`` after.
"""
from typing import Any

from odbms import Model
from pydantic import Field, field_validator

COMPUTERS = ('', 'sandbox')
"""'' follows each agent's own setting; 'sandbox' is the shared project
sandbox. Anything else is a paired PC's id."""


class Project(Model):
    user_id: str
    name: str
    description: str = ''
    instructions: str = ''
    """Added to the system prompt of every chat and task in the project."""
    color: str = ''
    """One of the theme accents ('' = default)."""
    agents: list[str] = Field(default_factory=list)
    """Agents that work in the project. The first is the default for new chats."""
    folders: list[dict[str, Any]] = Field(default_factory=list)
    """Working folders new chats start with ({path, access, name, primary}),
    on this server or in the sandbox."""
    knowledge: list[str] = Field(default_factory=list)
    """Knowledge collection ids every agent in the project can search."""
    computer: str = ''
    """'' (each agent's own), 'sandbox' (one shared sandbox user and desktop
    for every agent in the project) or a paired PC's id."""
    pc_folders: list[str] = Field(default_factory=list)
    """On a paired PC: the PC's shared folders the project uses (paths).
    Empty means all of them."""
    use_account_memory: bool = True
    """Agents in the project also see the person's account-wide memories."""
    pinned: bool = False
    archived: bool = False
    created: str = ''
    updated: str = ''
    """UTC 'YYYY-MM-DD HH:MM:SS'."""

    @field_validator('description', 'instructions', 'color', 'computer', 'created', 'updated', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)

    @field_validator('agents', 'knowledge', 'pc_folders', 'folders', mode='before')
    @classmethod
    def _list(cls, value):
        if value in (None, ''):
            return []
        if isinstance(value, str):
            import json
            try:
                value = json.loads(value)
            except ValueError:
                return []
        return list(value) if isinstance(value, (list, tuple)) else []

    @field_validator('use_account_memory', mode='before')
    @classmethod
    def _memory_default(cls, value):
        return True if value is None else bool(value)


MODELS = (Project,)
