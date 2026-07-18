"""Session-owned local media artifacts used by rich tool results."""

from __future__ import annotations

import contextvars
import hashlib
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
        await Model.create_table.__func__(cls)
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
    """Trusted-local compatibility cleanup for legacy unbound image sessions."""
    from cognitrix.media.service import media_assets

    await media_assets.delete_session_media(session_id)


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
