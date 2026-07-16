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
    run_id: str | None = None
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


def _same_identity(actual: str | None, expected: str | None) -> bool:
    """Compare persisted identities without treating missing as a wildcard."""
    if actual is None or expected is None:
        return actual is expected
    return str(actual) == str(expected)


async def bound_task_run_artifact(
    artifact_id: str,
    *,
    run_id: str,
    user_id: str | None,
) -> Artifact | None:
    """Load an artifact only when its durable provenance exactly matches.

    Result envelopes and tool events are untrusted references.  The Artifact
    row is the authority for both ownership and metadata; legacy rows without
    run provenance deliberately fail closed.
    """
    artifact = await Artifact.get(str(artifact_id))
    if artifact is None:
        return None
    if not _same_identity(artifact.run_id, run_id):
        return None
    if not _same_identity(artifact.user_id, user_id):
        return None
    return artifact


async def store_png(data: bytes, *, session_id: str | None = None, agent_id: str | None = None,
                    user_id: str | None = None,
                    run_id: str | None = None,
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
    from cognitrix.tools.utils import current_execution_context

    execution = current_execution_context()
    # A durable turn's authority is immutable. Explicit helper arguments are
    # retained only for non-task import/migration callers and cannot rebind an
    # artifact created inside a run to another run or user.
    artifact_run_id = execution.run_id if execution.run_id is not None else run_id
    artifact_user_id = (
        execution.user_id
        if execution.run_id is not None
        else user_id or _user_id.get()
    )
    artifact = Artifact(session_id=session, run_id=artifact_run_id,
                        agent_id=agent_id or _agent_id.get(),
                        user_id=artifact_user_id, storage_key=key, mime_type='image/png',
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
    from cognitrix.tools.utils import current_execution_context

    execution = current_execution_context()
    if execution.run_id is not None:
        if artifact.run_id != execution.run_id:
            raise ValueError('Source artifact belongs to a different task run')
        if execution.task_id is None:
            raise ValueError('Source artifact task authority is unavailable')
        from cognitrix.tasks.run import TaskRun

        run = await TaskRun.get(execution.run_id)
        if run is None or str(run.task_id) != str(execution.task_id):
            raise ValueError('Source artifact belongs to a different task')
    else:
        if artifact.run_id is not None:
            raise ValueError('Source artifact belongs to a different task run')
        if artifact.session_id != (session_id or current_session_id()):
            raise ValueError('Source artifact belongs to a different session')
    expected_user = (
        execution.user_id
        if execution.run_id is not None or execution.user_id is not None
        else user_id or _user_id.get()
    )
    if artifact.user_id != expected_user:
        raise ValueError('Source artifact belongs to a different user')
    path = absolute_path(artifact)
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    if size > MAX_IMAGE_BYTES:
        raise ValueError('Source image exceeds the 10MB limit')
    data = await asyncio.to_thread(path.read_bytes)
    return artifact, data


async def delete_session_artifacts(
    session_id: str,
    *,
    user_id: str | None,
) -> None:
    """Remove one owner's ordinary media when its chat is cleared/deleted."""
    owner_id = str(user_id).strip() if user_id is not None else ''
    if not owner_id:
        # Legacy ownerless rows cannot be safely attributed to a web caller.
        return
    session = session_id
    artifacts = await Artifact.find({'session_id': session}) or []
    for artifact in artifacts:
        # TaskRun artifacts are durable run resources.  An ephemeral step
        # session id can collide with (or be forged as) an ordinary chat id,
        # so session cleanup must never authorize their deletion.
        if (
            artifact.run_id is not None
            or artifact.user_id is None
            or str(artifact.user_id) != owner_id
        ):
            continue
        try:
            await asyncio.to_thread(absolute_path(artifact).unlink, missing_ok=True)
        except (OSError, ValueError):
            pass
        # Delete the exact row already classified as an ordinary artifact.
        # A broad session-id delete would race with creation of a durable
        # artifact and would also erase legacy co-located run artifacts.
        await Artifact.delete_many({'id': artifact.id})
    directory = _root() / _storage_namespace(session)
    try:
        await asyncio.to_thread(directory.rmdir)
    except OSError:
        pass


def ref(artifact: Artifact) -> ArtifactRef:
    return ArtifactRef(id=str(artifact.id), mime_type=artifact.mime_type, filename=artifact.filename,
                       width=artifact.width, height=artifact.height)
