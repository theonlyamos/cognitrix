"""Session-owned local media artifacts used by rich tool results."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import uuid
from pathlib import Path

from odbms import Model

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


def absolute_path(artifact: Artifact) -> Path:
    path = (_root() / artifact.storage_key).resolve()
    if _root().resolve() not in path.parents:
        raise ValueError('Invalid artifact storage key')
    return path


async def store_png(data: bytes, *, session_id: str | None = None, agent_id: str | None = None,
                    user_id: str | None = None,
                    prompt: str = '', source_artifact_id: str | None = None,
                    model: str = '', width: int | None = None, height: int | None = None) -> Artifact:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Generated image exceeds the 10MB limit')
    session = session_id or current_session_id()
    namespace = _storage_namespace(session)

    def _quota_and_write(path: Path) -> None:
        existing = list(path.parent.glob('*.png')) if path.parent.exists() else []
        if len(existing) >= MAX_SESSION_ARTIFACTS:
            raise ValueError('This session has reached its retained image limit')
        retained = sum(item.stat().st_size for item in existing)
        if retained + len(data) > MAX_SESSION_BYTES:
            raise ValueError('This session has reached its retained image storage limit')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    artifact_id = str(uuid.uuid4())
    key = f'{namespace}/{artifact_id}.png'
    path = _root() / key
    await asyncio.to_thread(_quota_and_write, path)
    artifact = Artifact(session_id=session, agent_id=agent_id or _agent_id.get(),
                        user_id=user_id or _user_id.get(), storage_key=key, mime_type='image/png',
                        filename=f'image-{artifact_id[:8]}.png', width=width, height=height,
                        size_bytes=len(data), prompt=prompt[:1000], source_artifact_id=source_artifact_id,
                        model=model)
    await artifact.save()
    return artifact


async def source_image(artifact_id: str, session_id: str | None = None,
                       user_id: str | None = None) -> tuple[Artifact, bytes]:
    artifact = await Artifact.get(artifact_id)
    if artifact is None:
        raise ValueError('Source artifact was not found')
    if artifact.session_id != (session_id or current_session_id()):
        raise ValueError('Source artifact belongs to a different session')
    expected_user = user_id or _user_id.get()
    if artifact.user_id != expected_user:
        raise ValueError('Source artifact belongs to a different user')
    path = absolute_path(artifact)
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    if size > MAX_IMAGE_BYTES:
        raise ValueError('Source image exceeds the 10MB limit')
    data = await asyncio.to_thread(path.read_bytes)
    return artifact, data


async def delete_session_artifacts(session_id: str) -> None:
    """Remove a conversation's retained media when its chat is cleared/deleted."""
    session = session_id
    artifacts = await Artifact.find({'session_id': session}) or []
    for artifact in artifacts:
        try:
            await asyncio.to_thread(absolute_path(artifact).unlink, missing_ok=True)
        except (OSError, ValueError):
            pass
    await Artifact.delete_many({'session_id': session})
    directory = _root() / _storage_namespace(session)
    try:
        await asyncio.to_thread(directory.rmdir)
    except OSError:
        pass


def ref(artifact: Artifact) -> ArtifactRef:
    return ArtifactRef(id=str(artifact.id), mime_type=artifact.mime_type, filename=artifact.filename,
                       width=artifact.width, height=artifact.height)
