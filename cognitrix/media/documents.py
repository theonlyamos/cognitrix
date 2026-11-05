"""Durable metadata and ownership rules for managed document uploads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cognitrix.artifacts import DocumentArtifact
from cognitrix.media import document_storage
from cognitrix.media.document_storage import DocumentStorageRecord
from cognitrix.media.secure_fs import FileIdentity
from cognitrix.media.types import MediaOwnership, MediaValidationError

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_SESSION_DOCUMENTS = 20
MAX_SESSION_DOCUMENT_BYTES = 100 * 1024 * 1024
DOCUMENT_PROMOTION_TTL_SECONDS = 5 * 60
DOCUMENT_RECONCILE_LEASE_SECONDS = 2 * 60
DOCUMENT_RECONCILE_BATCH_SIZE = 64
DOCUMENT_HISTORY_MARKER = '[User uploaded files, readable with your file tools:]'

logger = logging.getLogger('cognitrix.log')

_HEX_32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX_64 = re.compile(r'[0-9a-f]{64}\Z')
_MIME = re.compile(r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+\Z')
_FILE_LEAF = re.compile(r'f_[0-9a-f]{32}(?:\.[a-z0-9]{1,16})?\Z')
_SAFE_EXTENSION = re.compile(r'\.[a-z0-9]{1,16}\Z')
_SESSION_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def _shared_session_lock(session_id: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[session_id] = lock
    return lock


def ownership_directory_prefix(ownership: MediaOwnership) -> str:
    """Return the deterministic opaque owner prefix for a document directory."""
    if not ownership.session_id or not ownership.user_id or not ownership.agent_id:
        raise MediaValidationError('Document ownership requires session, user, and agent')
    payload = (
        f'{ownership.session_id}\0{ownership.user_id}\0{ownership.agent_id}'
    ).encode('utf-8')
    return f'd_{hashlib.sha256(payload).hexdigest()}_'


def encode_identity(identity: FileIdentity) -> str:
    kind = 'd' if identity.is_directory else 'f'
    return f'v1:{identity.volume}:{kind}:{identity.file_id.hex()}'


def decode_identity(value: str, *, directory: bool) -> FileIdentity:
    try:
        version, raw_volume, kind, raw_file_id = value.split(':', 3)
        volume = int(raw_volume)
        file_id = bytes.fromhex(raw_file_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise MediaValidationError('Invalid document storage identity') from exc
    if (
        version != 'v1'
        or kind != ('d' if directory else 'f')
        or not raw_file_id
        or file_id.hex() != raw_file_id.lower()
    ):
        raise MediaValidationError('Invalid document storage identity')
    return FileIdentity(volume=volume, file_id=file_id, is_directory=directory)


def normalize_mime_type(value: str | None) -> str:
    mime = str(value or '').split(';', 1)[0].strip().lower()
    return mime if _MIME.fullmatch(mime) else 'application/octet-stream'


def sniff_document_mime(snapshot: bytes, declared: str | None = None) -> str:
    """Conservatively classify the exact bounded bytes being retained."""
    if snapshot.startswith(b'%PDF-'):
        return 'application/pdf'
    if snapshot.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
        return 'application/zip'
    if b'\x00' in snapshot:
        return 'application/octet-stream'
    try:
        text = snapshot.decode('utf-8')
    except UnicodeDecodeError:
        return 'application/octet-stream'
    if any(ord(char) < 9 or 13 < ord(char) < 32 for char in text):
        return 'application/octet-stream'
    normalized = normalize_mime_type(declared)
    if normalized == 'application/json':
        try:
            json.loads(text)
        except (TypeError, ValueError):
            return 'text/plain'
        return normalized
    if normalized in {'text/plain', 'text/csv', 'text/markdown'}:
        return normalized
    return 'text/plain'


def document_file_extension(filename: str | None, mime_type: str) -> str:
    """Return one bounded extension for dispatch; never retain a path."""
    preferred = {
        'application/pdf': '.pdf',
        'application/json': '.json',
        'application/zip': '.zip',
        'text/csv': '.csv',
        'text/markdown': '.md',
        'text/plain': '.txt',
    }.get(mime_type)
    if preferred is not None:
        return preferred
    guessed = mimetypes.guess_extension(mime_type, strict=False) or ''
    guessed = guessed.lower()
    if _SAFE_EXTENSION.fullmatch(guessed):
        return guessed
    suffix = Path(str(filename or '')).suffix.lower()
    return suffix if _SAFE_EXTENSION.fullmatch(suffix) else ''


def _validate_document(document: DocumentArtifact) -> MediaOwnership:
    ownership = MediaOwnership(
        session_id=document.session_id,
        user_id=document.user_id,
        agent_id=document.agent_id,
    )
    prefix = ownership_directory_prefix(ownership)
    relative = Path(document.storage_key)
    if (
        relative.is_absolute()
        or relative.drive
        or '..' in relative.parts
        or len(relative.parts) != 3
        or relative.parts[0] != 'uploads'
        or not relative.parts[1].startswith(prefix)
        or not _HEX_32.fullmatch(relative.parts[1][len(prefix):])
        or not _FILE_LEAF.fullmatch(relative.parts[2])
    ):
        raise MediaValidationError('Document path is outside its ownership namespace')
    if document.origin not in {'uploaded', 'generated'}:
        raise MediaValidationError('Invalid document origin')
    if document.size_bytes < 0 or document.size_bytes > MAX_DOCUMENT_BYTES:
        raise MediaValidationError('Document exceeds the size limit')
    if not _HEX_64.fullmatch(document.sha256):
        raise MediaValidationError('Invalid document digest')
    if not _MIME.fullmatch(document.mime_type or ''):
        raise MediaValidationError('Invalid document MIME type')
    if document.filename is not None and (
        Path(document.filename).name != document.filename
        or len(document.filename) > 255
    ):
        raise MediaValidationError('Invalid document filename')
    if document.status not in {'intent', 'pending', 'ready', 'adopted', 'reconciling'}:
        raise MediaValidationError('Invalid document promotion status')
    if document.generation < 0:
        raise MediaValidationError('Invalid document ownership generation')
    if document.promotion_token and not _HEX_32.fullmatch(document.promotion_token):
        raise MediaValidationError('Invalid document promotion token')
    if document.status != 'adopted' and not document.promotion_token:
        raise MediaValidationError('Document promotion token is missing')
    if document.status in {'intent', 'pending', 'ready', 'reconciling'}:
        try:
            datetime.fromisoformat(str(document.expires_at))
        except (TypeError, ValueError) as exc:
            raise MediaValidationError('Invalid document promotion expiry') from exc
    if document.tools_root_identity is not None:
        decode_identity(document.tools_root_identity, directory=True)
    if document.uploads_identity is not None:
        decode_identity(document.uploads_identity, directory=True)
    if document.directory_identity is not None:
        decode_identity(document.directory_identity, directory=True)
    if document.file_identity is not None:
        decode_identity(document.file_identity, directory=False)
    if document.status in {'ready', 'adopted'} and (
        document.tools_root_identity is None
        or document.uploads_identity is None
        or document.directory_identity is None
        or document.file_identity is None
    ):
        raise MediaValidationError('Document storage identity is incomplete')
    return ownership


async def _save_settled(document: DocumentArtifact) -> None:
    mutation = asyncio.create_task(document.save())
    try:
        await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        mutation.result()
        raise cancelled


async def _update_settled(query: dict, values: dict) -> int:
    mutation = asyncio.create_task(DocumentArtifact.update_one(query, values))
    try:
        updated = await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        updated = mutation.result()
        raise cancelled
    return int(updated or 0)


async def _run_settled(operation):
    """Join a multi-write state transition before propagating cancellation."""
    mutation = asyncio.create_task(operation)
    try:
        return await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        mutation.result()
        raise cancelled


async def _delete_row_settled(
    document_id: str,
    *,
    status: str | None = None,
    promotion_token: str | None = None,
    generation: int | None = None,
    expires_at: str | None = None,
) -> None:
    query = {'id': str(document_id)}
    if status is not None:
        query['status'] = status
    if promotion_token is not None:
        query['promotion_token'] = promotion_token
    if generation is not None:
        query['generation'] = generation
    if expires_at is not None:
        query['expires_at'] = expires_at
    mutation = asyncio.create_task(
        DocumentArtifact.delete_many(query)
    )
    try:
        deleted = await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        deleted = mutation.result()
        if deleted == 0:
            raise RuntimeError('Document metadata deletion did not complete')
        raise cancelled
    if deleted == 0:
        raise RuntimeError('Document metadata deletion did not complete')


def _session_authority():
    from cognitrix.session_ownership import session_ownerships

    return session_ownerships


def _expiry(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (
        current + timedelta(seconds=DOCUMENT_PROMOTION_TTL_SECONDS)
    ).isoformat()


def _reconciliation_expiry(now: datetime) -> str:
    return (
        now + timedelta(seconds=DOCUMENT_RECONCILE_LEASE_SECONDS)
    ).isoformat()


def _delete_record(document: DocumentArtifact) -> DocumentStorageRecord:
    relative = Path(document.storage_key)
    return DocumentStorageRecord(
        relative_path=relative.as_posix(),
        directory_leaf=relative.parts[1],
        file_leaf=relative.parts[2],
        expected_size=document.size_bytes,
        expected_digest=document.sha256,
        document_id=str(document.id),
        tools_root_identity=(
            decode_identity(document.tools_root_identity, directory=True)
            if document.tools_root_identity is not None else None
        ),
        uploads_identity=(
            decode_identity(document.uploads_identity, directory=True)
            if document.uploads_identity is not None else None
        ),
        directory_identity=(
            decode_identity(document.directory_identity, directory=True)
            if document.directory_identity is not None else None
        ),
        file_identity=(
            decode_identity(document.file_identity, directory=False)
            if document.file_identity is not None else None
        ),
    )


async def _delete_document_file(document: DocumentArtifact) -> None:
    await document_storage.delete_document(_delete_record(document))


class DocumentAssetService:
    @staticmethod
    def _check_ownership(
        document: DocumentArtifact, ownership: MediaOwnership
    ) -> None:
        if (
            document.session_id != ownership.session_id
            or document.user_id != ownership.user_id
            or document.agent_id != ownership.agent_id
        ):
            raise MediaValidationError('Document belongs to a different owner')

    async def prepare_document_for_storage(
        self,
        document: DocumentArtifact,
        ownership: MediaOwnership,
    ) -> DocumentArtifact:
        """Persist intent, reserve quota, then acquire the storage lease."""
        self._check_ownership(document, ownership)
        authority = _session_authority()
        binding = await authority.require_active_owned(
            ownership.session_id,
            ownership.user_id,
            ownership.agent_id,
        )
        document.status = 'intent'
        document.generation = int(binding.generation)
        document.expires_at = document.expires_at or _expiry()
        _validate_document(document)
        # The ownership ledger CAS is the only quota authority. Counting rows
        # here can race another worker and can falsely reject recoverable rows.
        await _save_settled(document)
        reserved = False
        try:
            await authority.reserve_intent(
                ownership.session_id,
                ownership.user_id,
                ownership.agent_id,
                generation=document.generation,
                promotion_token=document.promotion_token,
                size_bytes=document.size_bytes,
                ttl_seconds=DOCUMENT_PROMOTION_TTL_SECONDS,
            )
            reserved = True
            changed = await _update_settled(
                {
                    'id': str(document.id),
                    'status': 'intent',
                    'promotion_token': document.promotion_token,
                    'generation': document.generation,
                },
                {'status': 'pending'},
            )
            if changed != 1:
                raise MediaValidationError('Document promotion intent changed')
            document.status = 'pending'
            # This CAS changes the ledger lease to ADOPTING and rechecks the
            # same active generation immediately before child creation.
            await authority.adopt_reservation(
                ownership.session_id,
                ownership.user_id,
                ownership.agent_id,
                generation=document.generation,
                promotion_token=document.promotion_token,
            )
            return document
        except BaseException:
            try:
                current = await DocumentArtifact.get(str(document.id))
                if current is not None and current.status in {'intent', 'pending'}:
                    await _delete_row_settled(
                        str(document.id),
                        status=current.status,
                        promotion_token=document.promotion_token,
                    )
            finally:
                if reserved:
                    await authority.release_reservation(
                        ownership.session_id,
                        ownership.user_id,
                        ownership.agent_id,
                        document.promotion_token,
                    )
            raise

    async def finalize_document_storage(
        self,
        document: DocumentArtifact,
        record: DocumentStorageRecord,
    ) -> DocumentArtifact:
        ownership = _validate_document(document)
        if (
            record.document_id != str(document.id)
            or record.relative_path != document.storage_key
            or record.tools_root_identity is None
            or record.uploads_identity is None
            or record.directory_identity is None
            or record.file_identity is None
        ):
            raise MediaValidationError('Document storage identity is incomplete')
        values = {
            'status': 'ready',
            'tools_root_identity': encode_identity(record.tools_root_identity),
            'uploads_identity': encode_identity(record.uploads_identity),
            'directory_identity': encode_identity(record.directory_identity),
            'file_identity': encode_identity(record.file_identity),
        }
        changed = await _update_settled(
            {
                'id': str(document.id),
                'status': 'pending',
                'promotion_token': document.promotion_token,
                'generation': document.generation,
            },
            values,
        )
        if changed != 1:
            raise MediaValidationError('Document promotion intent changed')
        for key, value in values.items():
            setattr(document, key, value)
        _validate_document(document)
        self._check_ownership(document, ownership)
        return document

    async def mark_documents_adopted(
        self,
        document_ids: list[str],
        ownership: MediaOwnership,
    ) -> None:
        authority = _session_authority()
        for document_id in document_ids:
            document = await DocumentArtifact.get(str(document_id))
            if document is None:
                raise MediaValidationError('Document promotion metadata is missing')
            _validate_document(document)
            self._check_ownership(document, ownership)

            async def adopt_exact_document() -> None:
                # Keep the ledger ADOPTING (which blocks clear/delete) until
                # the durable row is already ADOPTED. A crash in between is
                # repaired by the exact ledger reconciliation below.
                if document.status != 'adopted':
                    changed = await _update_settled(
                        {
                            'id': str(document.id),
                            'status': 'ready',
                            'promotion_token': document.promotion_token,
                            'generation': document.generation,
                        },
                        {'status': 'adopted', 'expires_at': None},
                    )
                    if changed != 1:
                        current = await DocumentArtifact.get(str(document.id))
                        if current is None or current.status != 'adopted':
                            raise MediaValidationError(
                                'Document adoption state changed'
                            )
                    document.status = 'adopted'
                    document.expires_at = None
                await authority.commit_reservation(
                    ownership.session_id,
                    ownership.user_id,
                    ownership.agent_id,
                    generation=document.generation,
                    promotion_token=document.promotion_token,
                )

            await _run_settled(adopt_exact_document())

    async def delete_document_metadata(
        self,
        document_id: str,
        ownership: MediaOwnership,
    ) -> None:
        document = await DocumentArtifact.get(str(document_id))
        if document is None:
            return
        _validate_document(document)
        self._check_ownership(document, ownership)
        status = document.status
        await _delete_row_settled(
            str(document.id),
            status=status,
            promotion_token=(document.promotion_token or None),
        )
        if not document.promotion_token:
            return
        authority = _session_authority()
        if status == 'adopted':
            await authority.release_document(
                ownership.session_id,
                ownership.user_id,
                ownership.agent_id,
                promotion_token=document.promotion_token,
                size_bytes=document.size_bytes,
            )
        else:
            await authority.release_reservation(
                ownership.session_id,
                ownership.user_id,
                ownership.agent_id,
                document.promotion_token,
            )

    async def delete_session_documents(self, ownership: MediaOwnership) -> None:
        if not ownership.session_id or not ownership.user_id or not ownership.agent_id:
            raise MediaValidationError('Exact document ownership is required')
        await _session_authority().require_owned(
            ownership.session_id, ownership.user_id, ownership.agent_id
        )
        async with _shared_session_lock(ownership.session_id):
            retained = await DocumentArtifact.find({
                'session_id': ownership.session_id,
                'user_id': ownership.user_id,
                'agent_id': ownership.agent_id,
            }) or []
            for document in retained:
                _validate_document(document)
                self._check_ownership(document, ownership)
                record = _delete_record(document)
                if record.directory_identity is None or record.file_identity is None:
                    inspected = await document_storage.inspect_document(record)
                    if inspected is not None:
                        await document_storage.delete_document(inspected)
                else:
                    await document_storage.delete_document(record)
                await self.delete_document_metadata(str(document.id), ownership)

    @staticmethod
    async def _session_contains_storage_key(document: DocumentArtifact) -> bool:
        from cognitrix.sessions.base import Session

        session = await Session.get(document.session_id)
        if session is None:
            return False
        for item in session.chat or []:
            if (
                str(item.get('role', '')).lower() == 'user'
                and item.get('type') == 'text'
            ):
                lines = str(item.get('content') or '').splitlines()
                if (
                    lines
                    and lines[0] == DOCUMENT_HISTORY_MARKER
                    and document.storage_key in lines[1:]
                ):
                    return True
        return False

    async def reconcile_expired(self, now: datetime | None = None) -> int:
        """Recover or remove expired pre-adoption document promotions."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        recovered = 0
        try:
            rows = await DocumentArtifact.reconciliation_candidates(
                expires_before=current.isoformat(),
                limit=DOCUMENT_RECONCILE_BATCH_SIZE,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            logger.exception('Failed closed while loading document intents')
            rows = []
        for candidate in rows:
            if candidate.status not in {'intent', 'pending', 'ready', 'reconciling'}:
                continue
            try:
                expires = datetime.fromisoformat(str(candidate.expires_at))
            except (TypeError, ValueError):
                logger.error('Retaining document with invalid promotion expiry')
                continue
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires > current:
                continue
            prior_status = candidate.status
            prior_expiry = candidate.expires_at
            lease_expiry = _reconciliation_expiry(current)
            changed = await _update_settled(
                {
                    'id': str(candidate.id),
                    'status': prior_status,
                    'promotion_token': candidate.promotion_token,
                    'generation': candidate.generation,
                    'expires_at': prior_expiry,
                },
                {
                    'status': 'reconciling',
                    'expires_at': lease_expiry,
                },
            )
            if changed != 1:
                continue
            candidate.status = 'reconciling'
            candidate.expires_at = lease_expiry
            try:
                ownership = _validate_document(candidate)
                inspected = await document_storage.inspect_document(
                    _delete_record(candidate)
                )
                adopted = (
                    inspected is not None
                    and inspected.file_identity is not None
                    and await self._session_contains_storage_key(candidate)
                )
                if adopted:
                    values = {
                        'status': 'ready',
                        'tools_root_identity': encode_identity(
                            inspected.tools_root_identity
                        ),
                        'uploads_identity': encode_identity(
                            inspected.uploads_identity
                        ),
                        'directory_identity': encode_identity(
                            inspected.directory_identity
                        ),
                        'file_identity': encode_identity(inspected.file_identity),
                    }
                    changed = await _update_settled(
                        {
                            'id': str(candidate.id),
                            'status': 'reconciling',
                            'promotion_token': candidate.promotion_token,
                            'generation': candidate.generation,
                            'expires_at': lease_expiry,
                        },
                        values,
                    )
                    if changed != 1:
                        raise MediaValidationError('Document recovery state changed')
                    for key, value in values.items():
                        setattr(candidate, key, value)
                    await self.mark_documents_adopted(
                        [str(candidate.id)], ownership
                    )
                else:
                    if inspected is not None:
                        await document_storage.delete_document(inspected)
                    await _delete_row_settled(
                        str(candidate.id),
                        status='reconciling',
                        promotion_token=candidate.promotion_token,
                        generation=candidate.generation,
                        expires_at=lease_expiry,
                    )
                    await _session_authority().release_reservation(
                        ownership.session_id,
                        ownership.user_id,
                        ownership.agent_id,
                        candidate.promotion_token,
                    )
                recovered += 1
            except asyncio.CancelledError:
                # The renewable lease remains durable; a later sweep resumes it.
                raise
            except BaseException:
                logger.exception('Failed closed while reconciling a document intent')
        recovered += await self._reconcile_orphaned_ledger_tokens()
        return recovered

    @staticmethod
    async def _reconcile_orphaned_ledger_tokens() -> int:
        """Release exact ledger tokens whose document row no longer exists.

        A cleanup can durably delete its row and then lose the following ledger
        CAS.  Intent-before-reservation ordering proves that a pending or
        adopting token without a row is also cleanup residue. Any row with the
        same token, even a malformed or foreign one, retains the ledger entry.
        """
        authority = _session_authority()
        try:
            bindings = await authority.reconciliation_bindings(
                limit=DOCUMENT_RECONCILE_BATCH_SIZE,
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            logger.exception('Failed closed while loading document quota ledgers')
            return 0
        released = 0
        for binding in bindings:
            for reservation in list(getattr(binding, 'reservations', None) or []):
                status = str(reservation.get('status') or '')
                if status not in {'pending', 'adopting', 'committed'}:
                    continue
                token = str(reservation.get('promotion_token') or '')
                if not token:
                    logger.error('Retaining malformed document quota reservation')
                    continue
                try:
                    rows = await DocumentArtifact.find({
                        'promotion_token': token,
                    }) or []
                    if rows:
                        if status == 'adopting' and len(rows) == 1:
                            row = rows[0]
                            try:
                                row_ownership = _validate_document(row)
                                exact_adopted = (
                                    row.status == 'adopted'
                                    and row_ownership.session_id == binding.session_id
                                    and row_ownership.user_id == binding.user_id
                                    and row_ownership.agent_id == binding.agent_id
                                    and int(row.generation)
                                    == int(reservation.get('generation'))
                                    and int(row.size_bytes)
                                    == int(reservation.get('size_bytes'))
                                )
                            except (TypeError, ValueError, MediaValidationError):
                                exact_adopted = False
                            if exact_adopted:
                                await authority.commit_reservation(
                                    binding.session_id,
                                    binding.user_id,
                                    binding.agent_id,
                                    generation=row.generation,
                                    promotion_token=token,
                                )
                                released += 1
                        continue
                    if status == 'committed':
                        try:
                            size_bytes = int(reservation.get('size_bytes'))
                        except (TypeError, ValueError):
                            logger.error(
                                'Retaining malformed committed document quota'
                            )
                            continue
                        if size_bytes < 0:
                            logger.error(
                                'Retaining malformed committed document quota'
                            )
                            continue
                        await authority.release_document(
                            binding.session_id,
                            binding.user_id,
                            binding.agent_id,
                            promotion_token=token,
                            size_bytes=size_bytes,
                        )
                    else:
                        await authority.release_reservation(
                            binding.session_id,
                            binding.user_id,
                            binding.agent_id,
                            token,
                        )
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    logger.exception(
                        'Failed closed while reconciling document quota'
                    )
                    continue
                released += 1
        return released


document_assets = DocumentAssetService()


__all__ = [
    'DocumentAssetService',
    'MAX_DOCUMENT_BYTES',
    'MAX_SESSION_DOCUMENTS',
    'MAX_SESSION_DOCUMENT_BYTES',
    'decode_identity',
    'document_assets',
    'document_file_extension',
    'encode_identity',
    'normalize_mime_type',
    'ownership_directory_prefix',
    'sniff_document_mime',
]
