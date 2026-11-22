"""A file an agent made, as the chat shows it.

Persistence rule (as elsewhere): ``save()`` once at creation, ``update_one``
after.
"""
from __future__ import annotations

import json
from typing import Any

from odbms import Model
from pydantic import field_validator

SERVER = 'server'
"""The machine Cognitrix runs on (the chat's folders)."""
SANDBOX = 'sandbox'
PC = 'pc'
SOURCES = (SERVER, SANDBOX, PC)


def _text(value: Any) -> str:
    return '' if value is None else str(value)


class ProducedFile(Model):
    owner_id: str
    """Principal key of the person the chat belongs to."""
    agent_id: str = ''
    session_id: str = ''
    task_id: str = ''
    run_id: str = ''
    name: str
    path: str = ''
    """Where the file lives (or lived) in its source."""
    source: str = SERVER
    device_id: str = ''
    """The paired PC, for source 'pc'."""
    mime_type: str = 'application/octet-stream'
    kind: str = 'other'
    size_bytes: int = 0
    mtime: float = 0.0
    shared: bool = False
    """True when the agent shared it with Share File; False when the end-of-turn look found it."""
    shown: bool = False
    """Shown as a card in the reply (shared, or picked as a likely result)."""
    caption: str = ''
    stored: bool = False
    """A copy is kept in Cognitrix (storage_key)."""
    storage_key: str = ''
    thumb_key: str = ''
    info_json: str = ''
    """Details for the preview: width, height, duration, pages, first rows..."""
    turn_at: float = 0.0
    """When the turn that made it started (epoch seconds)."""
    created: str = ''

    @field_validator('agent_id', 'session_id', 'task_id', 'run_id', 'path', 'device_id', 'caption',
                     'storage_key', 'thumb_key', 'info_json', 'created', mode='before')
    @classmethod
    def _text_default(cls, value):
        return _text(value)

    @field_validator('shared', 'shown', 'stored', mode='before')
    @classmethod
    def _bool(cls, value):
        return bool(value) and value not in ('0', 'false', 'False')

    @property
    def info(self) -> dict[str, Any]:
        try:
            value = json.loads(self.info_json or '{}')
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


MODELS = (ProducedFile,)
