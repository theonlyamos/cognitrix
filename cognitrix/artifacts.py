"""Session-owned local media artifacts used by rich tool results."""

from __future__ import annotations

import contextvars
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from odbms import Model
from pydantic import Field

from cognitrix.config import settings
from cognitrix.tools.utils import ArtifactRef

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('artifact_session_id', default=None)
_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('artifact_agent_id', default=None)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('artifact_user_id', default=None)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SESSION_ARTIFACTS = 20
MAX_SESSION_BYTES = 100 * 1024 * 1024


def set_session(session_id: str | None, agent_id: str | None = None, user_id: str | None = None):
    return _session_id.set(session_id), _agent_id.set(agent_id), _user_id.set(user_id)


def reset_session(token) -> None:
    session_token, agent_token, user_token = token
    _session_id.reset(session_token)
    _agent_id.reset(agent_token)
    _user_id.reset(user_token)


def current_session_id() -> str:
    return _session_id.get() or 'local'


class Artifact(Model):
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    storage_key: str
    origin: str | None = 'generated'
    vision_storage_key: str | None = None
    thumbnail_storage_key: str | None = None
    created_at: str | None = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mime_type: str = 'image/png'
    filename: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0
    prompt: str = ''
    source_artifact_id: str | None = None
    model: str = ''


def _root() -> Path:
    root = settings.workdir / 'artifacts' / 'images'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _storage_namespace(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


_VARIANT_KEY_FIELDS = {
    'original': 'storage_key',
    'vision': 'vision_storage_key',
    'thumbnail': 'thumbnail_storage_key',
}


def _variant_storage_key(
    artifact: Artifact, variant: Literal['original', 'vision', 'thumbnail']
) -> str | None:
    field = _VARIANT_KEY_FIELDS.get(variant)
    if field is None:
        raise ValueError('Invalid artifact variant')
    return getattr(artifact, field)


def _resolve_storage_key(storage_key: str | None) -> Path:
    if not storage_key:
        raise ValueError('Invalid artifact storage key')
    relative = Path(storage_key)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Invalid artifact storage key')
    root = _root().resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError('Invalid artifact storage key')
    return path


def variant_path(
    artifact: Artifact, variant: Literal['original', 'vision', 'thumbnail']
) -> Path:
    return _resolve_storage_key(_variant_storage_key(artifact, variant))


def absolute_path(artifact: Artifact) -> Path:
    return variant_path(artifact, 'original')


async def store_png(data: bytes, *, session_id: str | None = None, agent_id: str | None = None,
                    user_id: str | None = None,
                    prompt: str = '', source_artifact_id: str | None = None,
                    model: str = '', width: int | None = None, height: int | None = None) -> Artifact:
    from cognitrix.media.service import media_assets
    from cognitrix.media.types import MediaOwnership

    ownership = MediaOwnership(
        session_id=session_id or current_session_id(),
        agent_id=agent_id if agent_id is not None else _agent_id.get(),
        user_id=user_id if user_id is not None else _user_id.get(),
    )
    artifact_ref = await media_assets.store_generated_image(
        data,
        {
            'prompt': prompt,
            'source_artifact_id': source_artifact_id,
            'model': model,
            'width': width,
            'height': height,
        },
        ownership,
    )
    artifact = await Artifact.get(artifact_ref.id)
    if artifact is None:
        raise ValueError('Stored artifact metadata is unavailable')
    return artifact


async def source_image(artifact_id: str, session_id: str | None = None,
                       user_id: str | None = None) -> tuple[Artifact, bytes]:
    from cognitrix.media.service import media_assets
    from cognitrix.media.types import MediaOwnership

    resolved = await media_assets.resolve_image(
        artifact_id,
        MediaOwnership(
            session_id=session_id or current_session_id(),
            user_id=user_id if user_id is not None else _user_id.get(),
            agent_id=_agent_id.get(),
        ),
    )
    artifact = await Artifact.get(resolved.ref.id)
    if artifact is None:
        raise ValueError('Source artifact was not found')
    return artifact, resolved.data


async def delete_session_artifacts(session_id: str) -> None:
    """Remove a conversation's retained media when its chat is cleared/deleted."""
    from cognitrix.media.service import media_assets

    await media_assets.delete_session_media(session_id)


def ref(artifact: Artifact) -> ArtifactRef:
    return ArtifactRef(id=str(artifact.id), mime_type=artifact.mime_type, filename=artifact.filename,
                       width=artifact.width, height=artifact.height,
                       origin=getattr(artifact, 'origin', None) or 'generated')
