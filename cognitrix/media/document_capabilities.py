"""Server-minted, exact per-turn capabilities for managed documents."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from cognitrix.artifacts import DocumentArtifact
from cognitrix.media.document_storage import DocumentStorageRecord
from cognitrix.media.documents import decode_identity
from cognitrix.media.types import MediaAccessError, MediaOwnership
from cognitrix.tools.utils import DocumentCapability

MAX_TURN_DOCUMENT_CAPABILITIES = 20
_FILE_LEAF = re.compile(r'f_[0-9a-f]{32}(?:\.[a-z0-9]{1,16})?\Z')
_DIRECTORY_LEAF = re.compile(r'd_[0-9a-f]{64}_[0-9a-f]{32}\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _bounded_ids(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw)
        if not value or len(value) > 128 or value in seen:
            raise MediaAccessError('Document selection is invalid')
        seen.add(value)
        result.append(value)
        if len(result) > MAX_TURN_DOCUMENT_CAPABILITIES:
            raise MediaAccessError('Too many documents were selected')
    return tuple(result)


def _capability_from_document(
    document: DocumentArtifact,
    ownership: MediaOwnership,
    *,
    allowed_statuses: frozenset[str],
) -> DocumentCapability:
    if (
        str(document.session_id) != str(ownership.session_id)
        or str(document.user_id) != str(ownership.user_id)
        or str(document.agent_id) != str(ownership.agent_id)
        or document.status not in allowed_statuses
    ):
        raise MediaAccessError('Document is unavailable')
    relative = Path(str(document.storage_key))
    if (
        relative.is_absolute()
        or relative.drive
        or len(relative.parts) != 3
        or relative.parts[0] != 'uploads'
        or not _DIRECTORY_LEAF.fullmatch(relative.parts[1])
        or not _FILE_LEAF.fullmatch(relative.parts[2])
        or not _SHA256.fullmatch(str(document.sha256))
        or int(document.size_bytes) < 0
        or int(document.size_bytes) > 10 * 1024 * 1024
    ):
        raise MediaAccessError('Document is unavailable')
    identities = (
        document.tools_root_identity,
        document.uploads_identity,
        document.directory_identity,
        document.file_identity,
    )
    if any(value is None for value in identities):
        raise MediaAccessError('Document is unavailable')
    try:
        decode_identity(identities[0], directory=True)
        decode_identity(identities[1], directory=True)
        decode_identity(identities[2], directory=True)
        decode_identity(identities[3], directory=False)
    except Exception as exc:
        raise MediaAccessError('Document is unavailable') from exc
    return DocumentCapability(
        document_id=str(document.id),
        storage_key=relative.as_posix(),
        mime_type=str(document.mime_type),
        filename=document.filename,
        size_bytes=int(document.size_bytes),
        sha256=str(document.sha256),
        tools_root_identity=str(identities[0]),
        uploads_identity=str(identities[1]),
        directory_identity=str(identities[2]),
        file_identity=str(identities[3]),
    )


async def load_turn_document_capabilities(
    ownership: MediaOwnership,
    *,
    fresh_document_ids: Iterable[str] = (),
    adopted_document_ids: Iterable[str] = (),
) -> tuple[DocumentCapability, ...]:
    """Load only IDs explicitly authorized for this exact server-side turn."""
    fresh = _bounded_ids(fresh_document_ids)
    adopted = _bounded_ids(adopted_document_ids)
    if len(fresh) + len(adopted) > MAX_TURN_DOCUMENT_CAPABILITIES:
        raise MediaAccessError('Too many documents were selected')
    if set(fresh).intersection(adopted):
        raise MediaAccessError('Document selection is invalid')

    grants: list[DocumentCapability] = []
    for document_id in fresh:
        document = await DocumentArtifact.get(document_id)
        if document is None:
            raise MediaAccessError('Document is unavailable')
        grants.append(_capability_from_document(
            document,
            ownership,
            allowed_statuses=frozenset({'ready', 'adopted'}),
        ))
    for document_id in adopted:
        document = await DocumentArtifact.get(document_id)
        if document is None:
            raise MediaAccessError('Document is unavailable')
        grants.append(_capability_from_document(
            document,
            ownership,
            allowed_statuses=frozenset({'adopted'}),
        ))
    return tuple(grants)


def storage_record(capability: DocumentCapability) -> DocumentStorageRecord:
    """Reconstruct the exact identity-pinned storage read request."""
    relative = Path(capability.storage_key)
    if (
        relative.is_absolute()
        or relative.drive
        or len(relative.parts) != 3
        or relative.parts[0] != 'uploads'
        or not _DIRECTORY_LEAF.fullmatch(relative.parts[1])
        or not _FILE_LEAF.fullmatch(relative.parts[2])
    ):
        raise MediaAccessError('Document is unavailable')
    return DocumentStorageRecord(
        relative_path=relative.as_posix(),
        directory_leaf=relative.parts[1],
        file_leaf=relative.parts[2],
        expected_size=capability.size_bytes,
        expected_digest=capability.sha256,
        document_id=capability.document_id,
        tools_root_identity=decode_identity(
            capability.tools_root_identity, directory=True
        ),
        uploads_identity=decode_identity(
            capability.uploads_identity, directory=True
        ),
        directory_identity=decode_identity(
            capability.directory_identity, directory=True
        ),
        file_identity=decode_identity(capability.file_identity, directory=False),
    )


__all__ = [
    'MAX_TURN_DOCUMENT_CAPABILITIES',
    'load_turn_document_capabilities',
    'storage_record',
]
