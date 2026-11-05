"""Session-owned local media artifacts used by rich tool results."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from odbms import DBMS, Model
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
    run_id: str | None = None
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


class DocumentArtifact(Model):
    """Durable ownership and cleanup metadata for a managed document."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    storage_key: str
    status: str = 'adopted'
    promotion_token: str = ''
    generation: int = 0
    expires_at: str | None = None
    origin: str = 'uploaded'
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mime_type: str = 'application/octet-stream'
    filename: str | None = None
    size_bytes: int = 0
    sha256: str = ''
    tools_root_identity: str | None = None
    uploads_identity: str | None = None
    directory_identity: str | None = None
    file_identity: str | None = None

    @classmethod
    async def create_table(cls):
        """Create durable document metadata and its recovery-critical indexes."""
        base_create = Model.create_table.__func__
        if inspect.iscoroutinefunction(base_create):
            await base_create(cls)
        else:
            base_create_async = getattr(Model, '_create_table_async', None)
            if base_create_async is None:
                raise RuntimeError('ODBMS does not expose an awaitable schema hook')
            await base_create_async.__func__(cls)
        database = DBMS.Database
        if database is None:
            raise RuntimeError('Database not initialized')
        table = cls.table_name()
        dbms = getattr(database, 'dbms', '')
        if dbms in {'sqlite', 'postgresql'}:
            await database.query(
                f'CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_storage_key '
                f'ON {table} (storage_key)'
            )
            await database.query(
                f'CREATE INDEX IF NOT EXISTS ix_{table}_owner '
                f'ON {table} (session_id, user_id, agent_id)'
            )
            await database.query(
                f'CREATE INDEX IF NOT EXISTS ix_{table}_reconcile '
                f'ON {table} (status, expires_at)'
            )
            await database.query(
                f'CREATE INDEX IF NOT EXISTS ix_{table}_promotion_token '
                f'ON {table} (promotion_token)'
            )
            return
        if dbms == 'mongodb':
            mongo_database = getattr(database, 'db', None)
            if mongo_database is None:
                raise RuntimeError('MongoDB document metadata is unavailable')
            collection = mongo_database[table]
            await collection.create_index(
                [('storage_key', 1)],
                name=f'ux_{table}_storage_key',
                unique=True,
            )
            await collection.create_index(
                [('session_id', 1), ('user_id', 1), ('agent_id', 1)],
                name=f'ix_{table}_owner',
            )
            await collection.create_index(
                [('status', 1), ('expires_at', 1)],
                name=f'ix_{table}_reconcile',
            )
            await collection.create_index(
                [('promotion_token', 1)],
                name=f'ix_{table}_promotion_token',
            )
            return
        if dbms == 'mysql':
            await cls._create_mysql_indexes(database, table)
            return
        raise RuntimeError(f'Unsupported document metadata backend: {dbms!r}')

    @staticmethod
    async def _create_mysql_indexes(database, table: str) -> None:
        """Create MySQL indexes after an information_schema existence check."""
        pool = getattr(database, '_pool', None)
        if pool is None:
            raise RuntimeError('MySQL document metadata is unavailable')
        specs = (
            (
                f'ux_{table}_storage_key',
                f'CREATE UNIQUE INDEX ux_{table}_storage_key '
                f'ON {table} (storage_key(191))',
            ),
            (
                f'ix_{table}_owner',
                f'CREATE INDEX ix_{table}_owner '
                f'ON {table} '
                f'(session_id(191), user_id(191), agent_id(191))',
            ),
            (
                f'ix_{table}_reconcile',
                f'CREATE INDEX ix_{table}_reconcile '
                f'ON {table} (status(32), expires_at(64))',
            ),
            (
                f'ix_{table}_promotion_token',
                f'CREATE INDEX ix_{table}_promotion_token '
                f'ON {table} (promotion_token(64))',
            ),
        )
        async with pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for index_name, statement in specs:
                    await cursor.execute(
                        'SELECT 1 FROM information_schema.statistics '
                        'WHERE table_schema = DATABASE() '
                        'AND table_name = %s AND index_name = %s LIMIT 1',
                        (table, index_name),
                    )
                    if await cursor.fetchone() is None:
                        await cursor.execute(statement)

    @classmethod
    async def _create_table_async(cls):
        await cls.create_table()

    @classmethod
    async def reconciliation_candidates(
        cls,
        *,
        expires_before: str,
        limit: int,
    ) -> list['DocumentArtifact']:
        """Load one bounded, indexed batch of expired promotion rows."""
        bounded_limit = max(1, min(256, int(limit)))
        database = DBMS.Database
        if database is None:
            raise RuntimeError('Database not initialized')
        statuses = ('intent', 'pending', 'ready', 'reconciling')
        if getattr(database, 'dbms', '') == 'sqlite':
            table = cls.table_name()
            cursor = await database.query(
                f'''SELECT * FROM {table}
                    WHERE status IN ('intent', 'pending', 'ready', 'reconciling')
                      AND expires_at IS NOT NULL
                      AND datetime(expires_at) <= datetime(:expires_before)
                    ORDER BY datetime(expires_at), id
                    LIMIT :limit''',
                {
                    'expires_before': expires_before,
                    'limit': bounded_limit,
                },
            )
            rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else (cursor or [])
            return [cls(**cls.normalise(dict(row))) for row in rows]

        # ODBMS' SQL backends do not expose a portable LIMIT through Model.
        # Keep the maintenance mutation batch bounded everywhere; MongoDB's
        # own find implementation additionally caps each status query.
        candidates: list[DocumentArtifact] = []
        for status in statuses:
            candidates.extend(await cls.find({'status': status}) or [])
        due = [
            row for row in candidates
            if row.expires_at is not None and str(row.expires_at) <= expires_before
        ]
        due.sort(key=lambda row: (str(row.expires_at), str(row.id)))
        return due[:bounded_limit]


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


def variant_path(
    artifact: Artifact, variant: Literal['original', 'vision', 'thumbnail']
) -> Path:
    return _resolve_storage_key(_variant_storage_key(artifact, variant))


def absolute_path(artifact: Artifact) -> Path:
    return variant_path(artifact, 'original')


async def store_png(data: bytes, *, session_id: str | None = None, agent_id: str | None = None,
                    user_id: str | None = None,
                    run_id: str | None = None,
                    prompt: str = '', source_artifact_id: str | None = None,
                    model: str = '', width: int | None = None, height: int | None = None) -> Artifact:
    from cognitrix.media.service import media_assets
    from cognitrix.media.types import MediaOwnership

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
    ownership = MediaOwnership(
        session_id=session_id or current_session_id(),
        agent_id=agent_id if agent_id is not None else _agent_id.get(),
        user_id=artifact_user_id,
        run_id=artifact_run_id,
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
    if artifact.run_id != artifact_run_id:
        artifact.run_id = artifact_run_id
        await artifact.save()
    return artifact


async def source_image(artifact_id: str, session_id: str | None = None,
                       user_id: str | None = None) -> tuple[Artifact, bytes]:
    from cognitrix.media.service import media_assets
    from cognitrix.media.types import MediaOwnership

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

    if execution.run_id is not None:
        try:
            data = await asyncio.to_thread(absolute_path(artifact).read_bytes)
        except (OSError, ValueError) as exc:
            raise ValueError('Source artifact data is unavailable') from exc
    else:
        resolved = await media_assets.resolve_image(
            artifact_id,
            MediaOwnership(
                session_id=session_id or current_session_id(),
                user_id=user_id if user_id is not None else _user_id.get(),
                agent_id=_agent_id.get(),
            ),
        )
        data = resolved.data
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Source image exceeds the 10MB limit')
    return artifact, data


async def delete_session_artifacts(
    session_id: str,
    *,
    user_id: str | None = None,
) -> None:
    """Remove one owner's ordinary media when its chat is cleared/deleted."""
    owner_id = str(user_id).strip() if user_id is not None else ''
    if not owner_id:
        # Missing ownership is never authority to delete co-located durable
        # TaskRun artifacts or legacy ownerless rows.
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
        for variant in _VARIANT_KEY_FIELDS:
            try:
                await asyncio.to_thread(
                    variant_path(artifact, variant).unlink,
                    missing_ok=True,
                )
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


async def delete_owned_session_artifacts(
    *,
    session_id: str,
    user_id: str,
    agent_id: str,
    generation: int,
) -> None:
    """Delete all artifacts under one exact, rotated lifecycle authority."""
    from cognitrix.media.documents import document_assets
    from cognitrix.media.service import media_assets
    from cognitrix.media.types import MediaOwnership
    from cognitrix.session_ownership import (
        OwnershipConflict,
        OwnershipState,
        session_ownerships,
    )

    binding = await session_ownerships.require_owned(
        session_id,
        user_id,
        agent_id,
    )
    if (
        binding.generation != int(generation)
        or binding.state not in {OwnershipState.CLEARING, OwnershipState.DELETING}
    ):
        raise OwnershipConflict('Session cleanup authority is stale')
    ownership = MediaOwnership(
        session_id=session_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    await media_assets.delete_session_media(session_id, ownership)
    await document_assets.delete_session_documents(ownership)


def ref(artifact: Artifact) -> ArtifactRef:
    return ArtifactRef(id=str(artifact.id), mime_type=artifact.mime_type, filename=artifact.filename,
                       width=artifact.width, height=artifact.height,
                       origin=getattr(artifact, 'origin', None) or 'generated')
