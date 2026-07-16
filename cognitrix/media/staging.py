"""Bounded, short-lived staging for browser chat attachments."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from fastapi import HTTPException

from cognitrix.config import settings
from cognitrix.media.service import media_assets
from cognitrix.media.types import MediaOwnership, MediaValidationError
from cognitrix.tools.utils import ArtifactRef

from . import secure_fs
from .types import StagedAttachment

CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 20
STAGING_LEASE_SECONDS = 3600.0
ATTACHMENT_CLEANUP_ATTEMPTS = 3
# Each admitted batch reserves two cleanup obligations: one for its durable
# staging directory and one for a possible promoted-media rollback. The strict
# 128-unit process cap therefore admits at most 64 new attachment batches while
# retaining every unresolved obligation already accepted by this process.
MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS = 128
ATTACHMENT_MAINTENANCE_SWEEP_SECONDS = 60.0
ATTACHMENT_MAINTENANCE_RETRY_INITIAL_SECONDS = 0.25
ATTACHMENT_MAINTENANCE_RETRY_MAX_SECONDS = 60.0
STAGING_MANIFEST_LEAF = 'manifest_v1'
STAGING_MANIFEST_MAX_BYTES = 64 * 1024
STAGING_MANIFEST_MAX_RECORDS = 1 + (2 * MAX_UPLOAD_COUNT)
STAGING_RECOVERY_PREFIX = 'recovery_'
STAGING_RECOVERY_MAX_BYTES = STAGING_MANIFEST_MAX_BYTES + (16 * 1024)
_OPAQUE_LEAF = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')

_ACTIVE_BATCHES: set[Path] = set()
_BATCH_LEASES: dict[Path, tuple[str, float | None]] = {}
_BATCH_OBJECTS: dict[Path, 'StagedAttachmentSet'] = {}
_ACTIVE_LOCK = threading.RLock()
_PENDING_ROLLBACKS: dict[str, tuple['PromotedAttachments', MediaOwnership]] = {}
_PENDING_STAGING_CLEANUPS: dict[str, 'StagedAttachmentSet'] = {}
_PENDING_CLEANUP_LOCK = threading.RLock()
_ATTACHMENT_CLEANUP_OBLIGATION_UNITS = 0
_ACTIVE_CLEANUP_RESERVATION_TOKENS: set[str] = set()
_ATTACHMENT_MAINTENANCE_TASK: asyncio.Task | None = None
_ATTACHMENT_MAINTENANCE_WAKE: asyncio.Event | None = None
_ATTACHMENT_MAINTENANCE_LOOP: asyncio.AbstractEventLoop | None = None
_T = TypeVar('_T')
logger = logging.getLogger('cognitrix.log')


class AttachmentCleanupError(RuntimeError):
    """A bounded attachment cleanup could not be completed."""

    def __init__(self, causes: list[BaseException] | None = None) -> None:
        super().__init__('Attachment cleanup did not complete')
        self.causes = tuple(causes or ())


class _SweepRecoveryRequired(RuntimeError):
    """A sweep crossed its durable mutation boundary and must be resumed."""


class _AttachmentCleanupReservation:
    """Process-local units retained until accepted cleanup work is resolved."""

    def __init__(self, *, staging: bool, rollback: bool) -> None:
        token = uuid4().hex
        while token in _ACTIVE_CLEANUP_RESERVATION_TOKENS:
            token = uuid4().hex
        _ACTIVE_CLEANUP_RESERVATION_TOKENS.add(token)
        self.token = token
        self._staging = staging
        self._rollback = rollback

    def has_staging(self) -> bool:
        with _PENDING_CLEANUP_LOCK:
            return self._staging

    def has_rollback(self) -> bool:
        with _PENDING_CLEANUP_LOCK:
            return self._rollback

    def release_staging(self) -> None:
        global _ATTACHMENT_CLEANUP_OBLIGATION_UNITS
        with _PENDING_CLEANUP_LOCK:
            if not self._staging:
                return
            self._staging = False
            _ATTACHMENT_CLEANUP_OBLIGATION_UNITS -= 1
            if not self._rollback:
                _ACTIVE_CLEANUP_RESERVATION_TOKENS.discard(self.token)

    def release_rollback(self) -> None:
        global _ATTACHMENT_CLEANUP_OBLIGATION_UNITS
        with _PENDING_CLEANUP_LOCK:
            if not self._rollback:
                return
            self._rollback = False
            _ATTACHMENT_CLEANUP_OBLIGATION_UNITS -= 1
            if not self._staging:
                _ACTIVE_CLEANUP_RESERVATION_TOKENS.discard(self.token)

    def release_all(self) -> None:
        self.release_staging()
        self.release_rollback()


def _attachment_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail='Attachment processing is temporarily unavailable.',
    )


def _reserve_attachment_batch_cleanup() -> _AttachmentCleanupReservation:
    global _ATTACHMENT_CLEANUP_OBLIGATION_UNITS
    with _PENDING_CLEANUP_LOCK:
        if (
            _ATTACHMENT_CLEANUP_OBLIGATION_UNITS + 2
            > MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS
        ):
            raise _attachment_unavailable()
        _ATTACHMENT_CLEANUP_OBLIGATION_UNITS += 2
        return _AttachmentCleanupReservation(staging=True, rollback=True)


def _try_reserve_cleanup_unit(
    *,
    staging: bool = False,
    rollback: bool = False,
) -> _AttachmentCleanupReservation | None:
    """Compatibility admission for cleanup objects not created by staging."""
    global _ATTACHMENT_CLEANUP_OBLIGATION_UNITS
    if staging == rollback:
        raise ValueError('Exactly one cleanup unit must be requested')
    with _PENDING_CLEANUP_LOCK:
        if (
            _ATTACHMENT_CLEANUP_OBLIGATION_UNITS + 1
            > MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS
        ):
            return None
        _ATTACHMENT_CLEANUP_OBLIGATION_UNITS += 1
        return _AttachmentCleanupReservation(
            staging=staging,
            rollback=rollback,
        )


def _attachment_cleanup_obligation_count() -> int:
    with _PENDING_CLEANUP_LOCK:
        return _ATTACHMENT_CLEANUP_OBLIGATION_UNITS


def _normalize_windows_path_spelling(path: str | os.PathLike[str]) -> Path:
    value = os.fspath(path)
    if os.name == 'nt' and value.startswith('\\\\?\\'):
        tail = value[4:]
        if len(tail) >= 3 and tail[1] == ':' and tail[2] in {'\\', '/'}:
            value = tail
        elif tail.upper().startswith('UNC\\'):
            value = '\\\\' + tail[4:]
    return Path(value)


def _staging_root() -> Path:
    return _normalize_windows_path_spelling(
        (settings.workdir / 'staging' / 'chat-media').resolve()
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_absolute(path: Path) -> Path:
    """Normalize dot segments without resolving links or reparse points."""
    return _normalize_windows_path_spelling(
        os.path.abspath(os.fspath(path))
    )


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _remove_staged_batch_capability(staged: 'StagedAttachmentSet') -> None:
    if staged._root_identity is None or staged._batch_identity is None:
        raise MediaValidationError('Staged cleanup identity is unavailable')
    try:
        with secure_fs.open_root(_staging_root()) as root_capability:
            if root_capability.identity != staged._root_identity:
                raise secure_fs.CapabilityError()
            try:
                batch_capability = root_capability.open_directory(
                    staged.batch_dir.name,
                    expected_identity=staged._batch_identity,
                )
            except FileNotFoundError:
                return
            with batch_capability:
                for leaf, identity in list(staged._entry_identities.items()):
                    try:
                        batch_capability.delete_file(
                            leaf,
                            expected_identity=identity,
                        )
                    except FileNotFoundError:
                        pass
                if staged._manifest_identity is not None:
                    try:
                        batch_capability.delete_file(
                            STAGING_MANIFEST_LEAF,
                            expected_identity=staged._manifest_identity,
                        )
                    except FileNotFoundError:
                        pass
            root_capability.delete_directory(
                staged.batch_dir.name,
                expected_identity=staged._batch_identity,
            )
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError('Staged attachment cleanup failed') from exc


async def _run_thread_joined(func: Callable[..., _T], *args: Any) -> _T:
    """Wait for started filesystem work before propagating cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if worker.done() and not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                pass
        raise


async def _run_awaitable_joined(operation):
    """Settle an async close/cleanup operation before cancellation escapes."""
    worker = asyncio.create_task(operation)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if worker.done() and not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                pass
        raise


async def _settle_cleanup_attempt(
    operation,
) -> tuple[asyncio.CancelledError | None, BaseException | None]:
    """Settle one cleanup attempt without hiding failure behind cancellation."""
    worker = asyncio.create_task(operation)
    cancelled: asyncio.CancelledError | None = None
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError as exc:
        # A worker that cancelled itself is a failed cleanup, not an outer
        # cancellation. Otherwise keep settling the owned cleanup attempt.
        if not worker.cancelled():
            cancelled = exc
        if not worker.done():
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError as repeated:
                    cancelled = cancelled or repeated
                    continue
                except BaseException:
                    break
    except BaseException:
        # Retrieve the worker exception below so it can be retried/retained.
        pass

    if worker.cancelled():
        return cancelled, asyncio.CancelledError()
    try:
        worker.result()
    except BaseException as exc:
        return cancelled, exc
    return cancelled, None


async def _open_capability_joined(func: Callable[..., _T], *args: Any) -> _T:
    """Close a capability opened by a worker if cancellation wins handoff."""
    worker = asyncio.create_task(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        handle = None
        if worker.done() and not worker.cancelled():
            try:
                handle = worker.result()
            except BaseException:
                pass
        if handle is not None:
            try:
                await _run_thread_joined(handle.close)
            except asyncio.CancelledError:
                # The joined close has settled; preserve the original cancel.
                pass
        raise cancelled


def _safe_filename(value: Any) -> str:
    name = str(value or 'file').replace('\\', '/').split('/')[-1]
    name = ''.join(char for char in name if char >= ' ' and char != '\x7f').strip()
    return name if name not in {'', '.', '..'} else 'file'


def _declared_mime(value: Any) -> str:
    mime = str(value or 'application/octet-stream').strip()
    return (mime or 'application/octet-stream')[:255]


def _limit_error() -> HTTPException:
    return HTTPException(status_code=413, detail='Attachment exceeds the size limit.')


def _invalid_attachment() -> HTTPException:
    return HTTPException(status_code=400, detail='Invalid attachment data.')


@dataclass
class StagedAttachmentSet:
    batch_dir: Path
    entries: list[StagedAttachment]
    _root_identity: secure_fs.FileIdentity | None = field(
        default=None, init=False, repr=False
    )
    _batch_identity: secure_fs.FileIdentity | None = field(
        default=None, init=False, repr=False
    )
    _entry_identities: dict[str, secure_fs.FileIdentity] = field(
        default_factory=dict, init=False, repr=False
    )
    _manifest_capability: secure_fs.FileCapability | None = field(
        default=None, init=False, repr=False
    )
    _manifest_identity: secure_fs.FileIdentity | None = field(
        default=None, init=False, repr=False
    )
    _manifest_bytes: int = field(default=0, init=False, repr=False)
    _manifest_records: int = field(default=0, init=False, repr=False)
    _expires_at: float | None = field(default=None, init=False, repr=False)
    _cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _cleaned: bool = field(default=False, init=False, repr=False)
    _claimed: bool = field(default=False, init=False, repr=False)
    _cleanup_reservation: _AttachmentCleanupReservation | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _rollback_reservation_transferred: bool = field(
        default=False, init=False, repr=False, compare=False
    )
    _reservation_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )

    def _attach_cleanup_reservation(
        self,
        reservation: _AttachmentCleanupReservation,
    ) -> None:
        with self._reservation_lock:
            if self._cleanup_reservation is not None:
                raise RuntimeError('Attachment cleanup reservation already exists')
            self._cleanup_reservation = reservation

    def _transfer_rollback_reservation(
        self,
    ) -> _AttachmentCleanupReservation | None:
        with self._reservation_lock:
            reservation = self._cleanup_reservation
            if self._rollback_reservation_transferred:
                raise RuntimeError('Attachment rollback reservation already transferred')
            if reservation is not None and not reservation.has_rollback():
                raise RuntimeError('Attachment rollback reservation is unavailable')
            self._rollback_reservation_transferred = True
            return reservation

    def _release_unused_rollback_reservation(self) -> None:
        with self._reservation_lock:
            if self._rollback_reservation_transferred:
                return
            reservation = self._cleanup_reservation
        if reservation is not None:
            reservation.release_rollback()

    def _release_staging_reservation(self) -> None:
        with self._reservation_lock:
            reservation = self._cleanup_reservation
        if reservation is not None:
            reservation.release_staging()

    def _release_removed_reservations(self) -> None:
        self._release_unused_rollback_reservation()
        self._release_staging_reservation()

    def claim_now(self) -> None:
        """Atomically transfer a queued batch to the SSE consumer."""
        batch = _lexical_absolute(self.batch_dir)
        with _ACTIVE_LOCK:
            state = _BATCH_LEASES.get(batch)
            if state is None:
                raise MediaValidationError('Staged attachments are unavailable')
            phase, expires_at = state
            if self._claimed and phase == 'claimed':
                return
            if phase != 'queued':
                raise MediaValidationError('Staged attachments are unavailable')
            # Even an elapsed queued lease may be claimed if the sweep has not
            # atomically reserved it yet. This avoids deleting a dequeued batch.
            _BATCH_LEASES[batch] = ('claimed', None)
            self._claimed = True

    async def claim(self) -> None:
        self.claim_now()

    async def seal_manifest(self) -> None:
        capability, self._manifest_capability = self._manifest_capability, None
        if capability is not None:
            await _run_thread_joined(capability.close)

    async def cleanup(self) -> None:
        """Delete this batch exactly once; repeated calls are harmless."""
        async with self._cleanup_lock:
            # Calling cleanup ends the possibility of promotion unless that
            # rollback unit was already synchronously transferred.
            self._release_unused_rollback_reservation()
            if self._cleaned:
                self._release_staging_reservation()
                return
            root = _staging_root()
            batch = _lexical_absolute(self.batch_dir)
            if batch.parent != root or not _is_within(batch, root):
                raise ValueError('Refusing to clean a path outside the staging root')
            cancelled: asyncio.CancelledError | None = None
            try:
                try:
                    await self.seal_manifest()
                except asyncio.CancelledError as exc:
                    cancelled = exc
                try:
                    await _run_thread_joined(
                        _remove_staged_batch_capability, self
                    )
                except FileNotFoundError:
                    pass
                except asyncio.CancelledError as exc:
                    # The joined worker has already settled; finish the
                    # ownership transition before cancellation escapes.
                    cancelled = cancelled or exc
                if not _path_lexists(batch):
                    self._cleaned = True
                    self._release_staging_reservation()
                else:
                    raise OSError('Staged attachment cleanup did not remove the batch')
            finally:
                # cleanup() is the ownership release point. If deletion failed,
                # leave the batch inactive so a later cleanup/TTL sweep can retry.
                with _ACTIVE_LOCK:
                    _ACTIVE_BATCHES.discard(batch)
                    _BATCH_LEASES.pop(batch, None)
                    _BATCH_OBJECTS.pop(batch, None)
                    self._claimed = False
            if cancelled is not None:
                raise cancelled


def _manifest_line(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n'
    ).encode('utf-8')


def _manifest_done_record(
    leaf: str,
    size: int,
    digest: str,
    identity: secure_fs.FileIdentity,
) -> dict[str, Any]:
    return {
        'file_id': identity.file_id.hex(),
        'leaf': leaf,
        'op': 'done',
        'sha256': digest,
        'size': size,
        'volume': identity.volume,
    }


def _identity_from_unsettled_creation(
    error: secure_fs.CreatedChildCleanupError,
    *,
    expected_leaf: str,
    directory: bool,
) -> secure_fs.FileIdentity:
    if (
        error.leaf != expected_leaf
        or error.is_directory is not directory
        or error.identity.is_directory is not directory
    ):
        raise secure_fs.CapabilityError() from error
    return error.identity


def _create_pinned_batch(
    root: Path,
    leaf: str,
    created: dict[str, Any],
    created_at: float,
) -> None:
    with secure_fs.open_root(root) as root_capability:
        created['root'] = root_capability.identity
        try:
            batch_capability = root_capability.create_directory(leaf)
        except secure_fs.CreatedChildCleanupError as exc:
            created['batch'] = _identity_from_unsettled_creation(
                exc,
                expected_leaf=leaf,
                directory=True,
            )
            raise
        with batch_capability:
            created['batch'] = batch_capability.identity
            try:
                manifest = batch_capability.create_file(STAGING_MANIFEST_LEAF)
            except secure_fs.CreatedChildCleanupError as exc:
                created['manifest_identity'] = _identity_from_unsettled_creation(
                    exc,
                    expected_leaf=STAGING_MANIFEST_LEAF,
                    directory=False,
                )
                raise
            # Publish the pinned capability/identity to the caller before the
            # first manifest write so any partial-create failure remains
            # cleanup-addressable and capacity-accounted.
            created['manifest'] = manifest
            created['manifest_identity'] = manifest.identity
            try:
                header = _manifest_line({
                    'created_at': created_at,
                    'expires_at': created_at + STAGING_LEASE_SECONDS,
                    'version': 1,
                })
                manifest.write_bytes(
                    header,
                    max_bytes=STAGING_MANIFEST_MAX_BYTES,
                )
            except BaseException:
                manifest.close()
                raise
            created['manifest_bytes'] = len(header)
            created['manifest_records'] = 1
            created['expires_at'] = created_at + STAGING_LEASE_SECONDS


async def _create_batch(
    *,
    user_key: str,
    stream_id: str,
    cleanup_reservation: _AttachmentCleanupReservation,
) -> StagedAttachmentSet:
    root = _staging_root()
    await _run_thread_joined(root.mkdir, 0o700, True, True)
    user_hash = hashlib.sha256(str(user_key).encode('utf-8')).hexdigest()[:16]
    stream_hash = hashlib.sha256(str(stream_id).encode('utf-8')).hexdigest()[:16]
    batch = _lexical_absolute(root / f'{user_hash}-{stream_hash}-{uuid4().hex}')
    if batch.parent != root:
        raise RuntimeError('Unsafe staging batch path')
    with _ACTIVE_LOCK:
        _ACTIVE_BATCHES.add(batch)
        _BATCH_LEASES[batch] = ('writing', None)
    identities: dict[str, Any] = {}
    created_at = time.time()
    try:
        await _run_thread_joined(
            _create_pinned_batch,
            root,
            batch.name,
            identities,
            created_at,
        )
        root_identity = identities['root']
        batch_identity = identities['batch']
    except BaseException as primary_error:
        cleanup_error: BaseException | None = None
        root_identity = identities.get('root')
        batch_identity = identities.get('batch')
        if root_identity is not None and batch_identity is not None:
            created = StagedAttachmentSet(batch_dir=batch, entries=[])
            created._root_identity = root_identity
            created._batch_identity = batch_identity
            created._manifest_capability = identities.get('manifest')
            created._manifest_identity = identities.get('manifest_identity')
            created._attach_cleanup_reservation(cleanup_reservation)
            with _ACTIVE_LOCK:
                _BATCH_OBJECTS[batch] = created
            try:
                await cleanup_staged_attachments(created)
            except BaseException as exc:
                cleanup_error = exc
        with _ACTIVE_LOCK:
            _ACTIVE_BATCHES.discard(batch)
            _BATCH_LEASES.pop(batch, None)
            _BATCH_OBJECTS.pop(batch, None)
        if cleanup_error is not None:
            raise AttachmentCleanupError(
                [primary_error, cleanup_error]
            ) from cleanup_error
        raise
    staged = StagedAttachmentSet(batch_dir=batch, entries=[])
    staged._root_identity = root_identity
    staged._batch_identity = batch_identity
    staged._manifest_capability = identities['manifest']
    staged._manifest_identity = identities['manifest_identity']
    staged._manifest_bytes = identities['manifest_bytes']
    staged._manifest_records = identities['manifest_records']
    staged._expires_at = identities['expires_at']
    staged._attach_cleanup_reservation(cleanup_reservation)
    with _ACTIVE_LOCK:
        _BATCH_OBJECTS[batch] = staged
    return staged


def _mark_queued(batch: StagedAttachmentSet) -> None:
    path = _lexical_absolute(batch.batch_dir)
    with _ACTIVE_LOCK:
        state = _BATCH_LEASES.get(path)
        if state is None or state[0] != 'writing':
            raise RuntimeError('Staging ownership was lost before enqueue')
        if batch._expires_at is None:
            raise RuntimeError('Staging expiry is unavailable')
        _BATCH_LEASES[path] = ('queued', batch._expires_at)


async def _close_uploads(files: list[Any]) -> None:
    cancelled: asyncio.CancelledError | None = None
    for upload in files:
        close = getattr(upload, 'close', None)
        if close is None:
            continue
        try:
            result = close()
            if hasattr(result, '__await__'):
                await _run_awaitable_joined(result)
        except asyncio.CancelledError as exc:
            # Close every parsed part even when the request task is cancelled.
            cancelled = cancelled or exc
        except BaseException:
            # Starlette owns the request parts too; closing is best-effort and
            # must not hide a staging/limit failure.
            pass
    if cancelled is not None:
        raise cancelled


def _create_staged_file_capability(
    batch: StagedAttachmentSet,
    leaf: str,
) -> secure_fs.FileCapability:
    if batch._root_identity is None or batch._batch_identity is None:
        raise MediaValidationError('Staged attachments are unavailable')
    with secure_fs.open_root(_staging_root()) as root_capability:
        if root_capability.identity != batch._root_identity:
            raise MediaValidationError('Staging root changed')
        with root_capability.open_directory(
            batch.batch_dir.name,
            expected_identity=batch._batch_identity,
        ) as batch_capability:
            try:
                file_capability = batch_capability.create_file(leaf)
            except secure_fs.CreatedChildCleanupError as exc:
                batch._entry_identities[leaf] = (
                    _identity_from_unsettled_creation(
                        exc,
                        expected_leaf=leaf,
                        directory=False,
                    )
                )
                raise
            batch._entry_identities[leaf] = file_capability.identity
            return file_capability


def _append_manifest_record(
    batch: StagedAttachmentSet,
    record: dict[str, Any],
) -> None:
    capability = batch._manifest_capability
    if capability is None or batch._manifest_identity is None:
        raise MediaValidationError('Staging manifest is unavailable')
    payload = _manifest_line(record)
    if batch._manifest_bytes + len(payload) > STAGING_MANIFEST_MAX_BYTES:
        raise MediaValidationError('Staging manifest exceeds its size limit')
    if batch._manifest_records >= STAGING_MANIFEST_MAX_RECORDS:
        raise MediaValidationError('Staging manifest has too many records')
    capability.write_bytes(payload, max_bytes=len(payload))
    if capability.refresh_identity() != batch._manifest_identity:
        raise MediaValidationError('Staging manifest changed')
    batch._manifest_bytes += len(payload)
    batch._manifest_records += 1


async def _open_destination(batch: StagedAttachmentSet, index: int):
    leaf = f'{index:02d}-{uuid4().hex}'
    destination = _lexical_absolute(batch.batch_dir / leaf)
    if destination.parent != batch.batch_dir:
        raise RuntimeError('Unsafe staging destination path')
    await _run_thread_joined(
        _append_manifest_record,
        batch,
        {'op': 'plan', 'leaf': leaf},
    )
    handle = await _open_capability_joined(
        _create_staged_file_capability, batch, leaf
    )
    return destination, handle


async def _close_handle(handle) -> None:
    await _run_thread_joined(handle.close)


async def _copy_upload(
    upload: Any,
    *,
    batch: StagedAttachmentSet,
    index: int,
    total_so_far: int,
) -> int:
    destination, handle = await _open_destination(batch, index)
    size = 0
    digest = hashlib.sha256()
    try:
        while True:
            chunk = await upload.read(CHUNK_BYTES)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise _invalid_attachment()
            size += len(chunk)
            if size > MAX_UPLOAD_FILE_BYTES or total_so_far + size > MAX_UPLOAD_TOTAL_BYTES:
                raise _limit_error()
            payload = bytes(chunk)
            digest.update(payload)
            await _run_thread_joined(
                lambda: handle.write_bytes(payload, max_bytes=len(payload))
            )
    finally:
        await _close_handle(handle)
    await _run_thread_joined(
        _append_manifest_record,
        batch,
        _manifest_done_record(
            destination.name,
            size,
            digest.hexdigest(),
            batch._entry_identities[destination.name],
        ),
    )
    batch.entries.append(StagedAttachment(
        path=destination,
        filename=_safe_filename(getattr(upload, 'filename', None)),
        declared_mime=_declared_mime(getattr(upload, 'content_type', None)),
        size_bytes=size,
    ))
    return size


async def stage_upload_files(
    files,
    *,
    user_key: str,
    stream_id: str,
) -> StagedAttachmentSet:
    """Stream multipart UploadFiles into a bounded active staging batch."""
    uploads = list(files or [])
    try:
        cleanup_reservation = _reserve_attachment_batch_cleanup()
    except HTTPException:
        await _close_uploads(uploads)
        raise
    batch: StagedAttachmentSet | None = None
    try:
        await sweep_stale_staging()
        if len(uploads) > MAX_UPLOAD_COUNT:
            raise _limit_error()
        batch = await _create_batch(
            user_key=user_key,
            stream_id=stream_id,
            cleanup_reservation=cleanup_reservation,
        )
        total = 0
        for index, upload in enumerate(uploads):
            if not callable(getattr(upload, 'read', None)):
                raise _invalid_attachment()
            total += await _copy_upload(
                upload, batch=batch, index=index, total_so_far=total
            )
        await _close_uploads(uploads)
        await batch.seal_manifest()
        _mark_queued(batch)
        return batch
    except BaseException:
        try:
            await _close_uploads(uploads)
        finally:
            if batch is None:
                # A partial-create cleanup releases the unused rollback unit
                # but retains its staging unit on failure. Only a reservation
                # still holding rollback was never transferred to cleanup.
                if cleanup_reservation.has_rollback():
                    cleanup_reservation.release_all()
            else:
                await cleanup_staged_attachments(batch)
        raise


def _parse_data_url(data_url: Any) -> tuple[str, str] | None:
    if not isinstance(data_url, str) or not data_url.startswith('data:'):
        return None
    try:
        metadata, payload = data_url.split(',', 1)
    except ValueError:
        return None
    parts = metadata[5:].split(';')
    if not parts or 'base64' not in {part.lower() for part in parts[1:]}:
        return None
    return _declared_mime(parts[0]), payload


def _decoded_length(payload: str) -> int:
    """Compute decoded size without allocating or decoding the payload."""
    length = len(payload)
    if length == 0:
        return 0
    if length % 4:
        raise _invalid_attachment()
    padding = 2 if payload.endswith('==') else (1 if payload.endswith('=') else 0)
    return (length // 4) * 3 - padding


def _decode_data_url(data_url: str) -> bytes | None:
    """Compatibility decoder retained for callers outside the staging path."""
    parsed = _parse_data_url(data_url)
    if parsed is None:
        return None
    try:
        return base64.b64decode(parsed[1], validate=True)
    except (binascii.Error, ValueError):
        return None


async def _write_legacy_payload(
    payload: str,
    *,
    batch: StagedAttachmentSet,
    index: int,
    filename: str,
    mime: str,
    expected_size: int,
) -> None:
    destination, handle = await _open_destination(batch, index)
    written = 0
    digest = hashlib.sha256()
    encoded_chunk = (CHUNK_BYTES // 3) * 4
    encoded_chunk = max(4, encoded_chunk - (encoded_chunk % 4))
    try:
        for offset in range(0, len(payload), encoded_chunk):
            part = payload[offset:offset + encoded_chunk]
            try:
                decoded = base64.b64decode(part, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise _invalid_attachment() from exc
            written += len(decoded)
            digest.update(decoded)
            if written > expected_size:
                raise _limit_error()
            await _run_thread_joined(
                lambda: handle.write_bytes(decoded, max_bytes=len(decoded))
            )
    finally:
        await _close_handle(handle)
    if written != expected_size:
        raise _invalid_attachment()
    await _run_thread_joined(
        _append_manifest_record,
        batch,
        _manifest_done_record(
            destination.name,
            written,
            digest.hexdigest(),
            batch._entry_identities[destination.name],
        ),
    )
    batch.entries.append(StagedAttachment(
        path=destination,
        filename=filename,
        declared_mime=mime,
        size_bytes=written,
    ))


async def stage_legacy_data_urls(
    attachments,
    *,
    user_key: str,
    stream_id: str,
) -> StagedAttachmentSet:
    """Incrementally decode legacy JSON data URLs into the staging lifecycle."""
    values = list(attachments or [])
    cleanup_reservation = _reserve_attachment_batch_cleanup()
    batch: StagedAttachmentSet | None = None
    try:
        await sweep_stale_staging()
        if len(values) > MAX_UPLOAD_COUNT:
            raise _limit_error()

        prepared: list[tuple[str, str, str, int]] = []
        total = 0
        for attachment in values:
            if not isinstance(attachment, dict):
                raise _invalid_attachment()
            parsed = _parse_data_url(attachment.get('dataUrl'))
            if parsed is None:
                raise _invalid_attachment()
            mime, payload = parsed
            size = _decoded_length(payload)
            total += size
            if size > MAX_UPLOAD_FILE_BYTES or total > MAX_UPLOAD_TOTAL_BYTES:
                raise _limit_error()
            prepared.append((
                _safe_filename(attachment.get('name')),
                mime,
                payload,
                size,
            ))

        batch = await _create_batch(
            user_key=user_key,
            stream_id=stream_id,
            cleanup_reservation=cleanup_reservation,
        )
        for index, (filename, mime, payload, size) in enumerate(prepared):
            await _write_legacy_payload(
                payload,
                batch=batch,
                index=index,
                filename=filename,
                mime=mime,
                expected_size=size,
            )
        await batch.seal_manifest()
        _mark_queued(batch)
        return batch
    except BaseException:
        if batch is None:
            if cleanup_reservation.has_rollback():
                cleanup_reservation.release_all()
        else:
            await cleanup_staged_attachments(batch)
        raise


@dataclass(frozen=True)
class _UploadDestination:
    path: Path
    tools_root_identity: secure_fs.FileIdentity
    uploads_identity: secure_fs.FileIdentity


@dataclass(frozen=True)
class PromotedAttachments:
    image_refs: list[ArtifactRef]
    document_paths: list[dict[str, str]]
    _rollback_records: list['_DocumentRollbackRecord'] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )
    _record_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )
    _cleanup_reservation: _AttachmentCleanupReservation | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def release_promoted_attachment_reservation(
    promoted: PromotedAttachments,
) -> None:
    """Release rollback capacity after durable adoption or completed rollback."""
    reservation = promoted._cleanup_reservation
    if reservation is not None:
        reservation.release_rollback()


@dataclass(frozen=True)
class _DocumentRollbackRecord:
    relative_path: str
    directory_leaf: str
    file_leaf: str
    expected_size: int
    expected_digest: str
    tools_root_identity: secure_fs.FileIdentity | None = None
    uploads_identity: secure_fs.FileIdentity | None = None
    directory_identity: secure_fs.FileIdentity | None = None
    file_identity: secure_fs.FileIdentity | None = None


@dataclass(frozen=True)
class _PinnedAttachment:
    staged: StagedAttachment
    snapshot: bytes
    identity: secure_fs.FileIdentity


def _verify_pinned_source(
    pinned: _PinnedAttachment,
    staged: StagedAttachmentSet,
) -> None:
    if staged._root_identity is None or staged._batch_identity is None:
        raise MediaValidationError('Staged attachments are unavailable')
    try:
        with secure_fs.open_root(_staging_root()) as root_capability:
            if root_capability.identity != staged._root_identity:
                raise MediaValidationError('Staging root changed')
            with root_capability.open_directory(
                staged.batch_dir.name,
                expected_identity=staged._batch_identity,
            ) as batch_capability:
                with batch_capability.open_file(
                    pinned.staged.path.name,
                    expected_identity=pinned.identity,
                ) as file_capability:
                    if file_capability.refresh_identity() != pinned.identity:
                        raise MediaValidationError('Staged attachment changed')
    except (OSError, secure_fs.CapabilityError) as exc:
        raise MediaValidationError('Staged attachment changed') from exc


def _after_preflight_hook(_entries: list[_PinnedAttachment]) -> None:
    """Deterministic test seam for source replacement races."""


def _record_document_rollback(
    promoted: PromotedAttachments,
    relative_path: str,
    *,
    directory_leaf: str,
    file_leaf: str,
    expected_size: int,
    expected_digest: str,
    tools_root_identity: secure_fs.FileIdentity | None = None,
    uploads_identity: secure_fs.FileIdentity | None = None,
    directory_identity: secure_fs.FileIdentity | None = None,
    file_identity: secure_fs.FileIdentity | None = None,
) -> None:
    replacement = _DocumentRollbackRecord(
        relative_path=relative_path,
        directory_leaf=directory_leaf,
        file_leaf=file_leaf,
        expected_size=expected_size,
        expected_digest=expected_digest,
        tools_root_identity=tools_root_identity,
        uploads_identity=uploads_identity,
        directory_identity=directory_identity,
        file_identity=file_identity,
    )
    with promoted._record_lock:
        for index, record in enumerate(promoted._rollback_records):
            if record.relative_path == relative_path:
                promoted._rollback_records[index] = replacement
                return
        promoted._rollback_records.append(replacement)


def _preflight_staged_entries(staged: StagedAttachmentSet) -> list[_PinnedAttachment]:
    root = _staging_root()
    raw_batch = Path(staged.batch_dir)
    batch = _lexical_absolute(raw_batch)
    if not raw_batch.is_absolute() or raw_batch != batch:
        raise MediaValidationError('Staged attachments are unavailable')
    if batch.parent != root or not _is_within(batch, root):
        raise MediaValidationError('Staged attachments are unavailable')

    entries = list(staged.entries)
    if len(entries) > MAX_UPLOAD_COUNT:
        raise MediaValidationError('Staged attachments exceed the count limit')
    total = 0
    if staged._root_identity is None or staged._batch_identity is None:
        raise MediaValidationError('Staged attachments are unavailable')
    pinned: list[_PinnedAttachment] = []
    try:
        with secure_fs.open_root(root) as root_capability:
            if root_capability.identity != staged._root_identity:
                raise MediaValidationError('Staging root changed')
            with root_capability.open_directory(
                batch.name,
                expected_identity=staged._batch_identity,
            ) as batch_capability:
                for entry in entries:
                    raw_path = Path(entry.path)
                    path = _lexical_absolute(raw_path)
                    if not raw_path.is_absolute() or raw_path != path:
                        raise MediaValidationError('Staged attachments are unavailable')
                    if path.parent != batch or not _is_within(path, batch):
                        raise MediaValidationError('Staged attachments are unavailable')
                    if entry.size_bytes < 0:
                        raise MediaValidationError('Staged attachment size changed')
                    if entry.size_bytes > MAX_UPLOAD_FILE_BYTES:
                        raise MediaValidationError(
                            'Staged attachment exceeds the size limit'
                        )
                    total += entry.size_bytes
                    if total > MAX_UPLOAD_TOTAL_BYTES:
                        raise MediaValidationError(
                            'Staged attachments exceed the total size limit'
                        )
                    expected = staged._entry_identities.get(path.name)
                    if expected is None:
                        raise MediaValidationError('Staged attachment identity is missing')
                    with batch_capability.open_file(
                        path.name,
                        expected_identity=expected,
                    ) as file_capability:
                        snapshot = file_capability.read_bytes(
                            max_bytes=MAX_UPLOAD_FILE_BYTES
                        )
                        if (
                            len(snapshot) != entry.size_bytes
                            or file_capability.refresh_identity() != expected
                        ):
                            raise MediaValidationError('Staged attachment changed')
                    pinned.append(_PinnedAttachment(entry, snapshot, expected))
    except (OSError, secure_fs.CapabilityError) as exc:
        raise MediaValidationError('Staged attachments are unavailable') from exc
    return pinned


def _tools_upload_root() -> _UploadDestination:
    tools_root = _lexical_absolute(settings.tools_root)
    uploads = _lexical_absolute(tools_root / 'uploads')
    if uploads.parent != tools_root:
        raise MediaValidationError('Upload destination is unavailable')
    tools_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with secure_fs.open_root(tools_root) as tools_capability:
            tools_root_identity = tools_capability.identity
            if tools_capability.refresh_identity() != tools_root_identity:
                raise MediaValidationError('Upload destination changed')
            try:
                uploads_capability = tools_capability.open_directory('uploads')
            except FileNotFoundError:
                try:
                    uploads_capability = tools_capability.create_directory('uploads')
                except FileExistsError:
                    uploads_capability = tools_capability.open_directory('uploads')
            with uploads_capability:
                uploads_identity = uploads_capability.identity
                if (
                    uploads_capability.refresh_identity()
                    != uploads_identity
                ):
                    raise MediaValidationError('Upload destination changed')
    except (OSError, secure_fs.CapabilityError) as exc:
        raise MediaValidationError('Upload destination is unavailable') from exc
    return _UploadDestination(
        path=uploads,
        tools_root_identity=tools_root_identity,
        uploads_identity=uploads_identity,
    )


def _before_secure_document_create_hook(
    _uploads: Path,
    _directory_leaf: str,
    _file_leaf: str,
) -> None:
    """Deterministic test seam before handle-relative destination creation."""


def _create_document_transaction(
    snapshot: bytes,
    destination: _UploadDestination,
    directory_leaf: str,
    file_leaf: str,
    expected_size: int,
    promoted: PromotedAttachments,
    relative_path: str,
    expected_digest: str,
) -> None:
    if len(snapshot) != expected_size or len(snapshot) > MAX_UPLOAD_FILE_BYTES:
        raise MediaValidationError('Staged attachment changed')
    try:
        with secure_fs.open_root(destination.path) as root_capability:
            if (
                root_capability.identity != destination.uploads_identity
                or root_capability.refresh_identity()
                != destination.uploads_identity
            ):
                raise secure_fs.CapabilityError()
            with root_capability.create_directory(
                directory_leaf
            ) as directory_capability:
                _record_document_rollback(
                    promoted,
                    relative_path,
                    directory_leaf=directory_leaf,
                    file_leaf=file_leaf,
                    expected_size=expected_size,
                    expected_digest=expected_digest,
                    tools_root_identity=destination.tools_root_identity,
                    uploads_identity=destination.uploads_identity,
                    directory_identity=directory_capability.identity,
                )
                with directory_capability.create_file(file_leaf) as output:
                    # Record identity before writing so cancellation or a
                    # partial write remains safely rollback-addressable.
                    _record_document_rollback(
                        promoted,
                        relative_path,
                        directory_leaf=directory_leaf,
                        file_leaf=file_leaf,
                        expected_size=expected_size,
                        expected_digest=expected_digest,
                        tools_root_identity=destination.tools_root_identity,
                        uploads_identity=destination.uploads_identity,
                        directory_identity=directory_capability.identity,
                        file_identity=output.identity,
                    )
                    output.write_bytes(snapshot, max_bytes=MAX_UPLOAD_FILE_BYTES)
                    if output.refresh_identity() != output.identity:
                        raise secure_fs.CapabilityError()
                if (
                    directory_capability.refresh_identity()
                    != directory_capability.identity
                ):
                    raise secure_fs.CapabilityError()
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError('Upload destination is unavailable') from exc


def _resolve_upload_root_for_cleanup(
    record: _DocumentRollbackRecord,
) -> Path:
    """Open the recorded roots without creating or repairing any namespace."""
    if record.tools_root_identity is None or record.uploads_identity is None:
        raise MediaValidationError('Promoted document cleanup identity is missing')
    tools_root = _lexical_absolute(settings.tools_root)
    uploads = _lexical_absolute(tools_root / 'uploads')
    if uploads.parent != tools_root:
        raise MediaValidationError('Promoted document cleanup root changed')
    try:
        with secure_fs.open_root(tools_root) as tools_capability:
            if (
                tools_capability.identity != record.tools_root_identity
                or tools_capability.refresh_identity()
                != record.tools_root_identity
            ):
                raise secure_fs.CapabilityError()
            with tools_capability.open_directory(
                'uploads',
                expected_identity=record.uploads_identity,
            ) as uploads_capability:
                if (
                    uploads_capability.refresh_identity()
                    != record.uploads_identity
                ):
                    raise secure_fs.CapabilityError()
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError(
            'Promoted document cleanup root changed'
        ) from exc
    return uploads


def _delete_secure_document(record: _DocumentRollbackRecord) -> None:
    expected_path = (
        Path('uploads') / record.directory_leaf / record.file_leaf
    ).as_posix()
    if record.relative_path != expected_path:
        raise MediaValidationError('Invalid promoted document path')
    try:
        uploads = _resolve_upload_root_for_cleanup(record)
        with secure_fs.open_root(uploads) as root_capability:
            if (
                root_capability.identity != record.uploads_identity
                or root_capability.refresh_identity()
                != record.uploads_identity
            ):
                raise secure_fs.CapabilityError()
            if record.directory_identity is None:
                # Creation may fail before the directory identity is journaled.
                # Absence is a completed rollback; an unexpected directory is
                # retained because deleting it without identity would be unsafe.
                try:
                    unexpected = root_capability.open_directory(
                        record.directory_leaf
                    )
                except FileNotFoundError:
                    return
                with unexpected:
                    pass
                raise secure_fs.CapabilityError()
            try:
                directory_capability = root_capability.open_directory(
                    record.directory_leaf,
                    expected_identity=record.directory_identity,
                )
            except FileNotFoundError:
                return
            with directory_capability:
                if (
                    directory_capability.refresh_identity()
                    != record.directory_identity
                ):
                    raise secure_fs.CapabilityError()
                if record.file_identity is not None:
                    try:
                        directory_capability.delete_file(
                            record.file_leaf,
                            expected_identity=record.file_identity,
                        )
                    except FileNotFoundError:
                        pass
            root_capability.delete_directory(
                record.directory_leaf,
                expected_identity=record.directory_identity,
            )
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError('Promoted document cleanup failed') from exc


async def _rollback_promoted_attachments(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> None:
    """Remove a promoted batch after a pre-scheduling stop or failure."""
    errors: list[BaseException] = []
    with promoted._record_lock:
        rollback_records = {
            record.relative_path: record for record in promoted._rollback_records
        }
    if promoted.image_refs:
        try:
            await media_assets.delete_artifacts(
                [item.id for item in promoted.image_refs], ownership
            )
        except BaseException as exc:
            errors.append(exc)
            logger.exception('Failed to roll back promoted image artifacts')
    for document in reversed(promoted.document_paths):
        try:
            record = rollback_records.get(document['path'])
            if record is None:
                raise MediaValidationError(
                    'Promoted document cleanup identity is missing'
                )
            await _run_thread_joined(_delete_secure_document, record)
        except BaseException as exc:
            errors.append(exc)
            logger.exception('Failed to roll back promoted document')
    if errors:
        raise errors[0]


def _rollback_key(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> str:
    reservation = promoted._cleanup_reservation
    if reservation is not None:
        return f'{reservation.token}-rollback'
    values = (
        ownership.session_id,
        ownership.user_id,
        ownership.agent_id,
        tuple(item.id for item in promoted.image_refs),
        tuple(item.get('path', '') for item in promoted.document_paths),
    )
    return hashlib.sha256(repr(values).encode('utf-8')).hexdigest()


def _staging_cleanup_key(staged: StagedAttachmentSet) -> str:
    reservation = getattr(staged, '_cleanup_reservation', None)
    if isinstance(reservation, _AttachmentCleanupReservation):
        return f'{reservation.token}-staging'
    batch_dir = getattr(staged, 'batch_dir', None)
    if batch_dir is None:
        return f'object-{id(staged)}'
    return hashlib.sha256(
        os.fspath(_lexical_absolute(batch_dir)).encode('utf-8')
    ).hexdigest()


def pending_attachment_cleanup_count() -> int:
    with _PENDING_CLEANUP_LOCK:
        return len(_PENDING_ROLLBACKS) + len(_PENDING_STAGING_CLEANUPS)


def _ensure_staging_cleanup_reservation(
    staged: Any,
) -> _AttachmentCleanupReservation | None:
    object_lock = getattr(staged, '_reservation_lock', None)
    lock = (
        object_lock
        if isinstance(object_lock, type(_PENDING_CLEANUP_LOCK))
        else _PENDING_CLEANUP_LOCK
    )
    with lock:
        reservation = getattr(staged, '_cleanup_reservation', None)
        if (
            isinstance(reservation, _AttachmentCleanupReservation)
            and reservation.has_staging()
        ):
            return reservation
        if getattr(staged, '_cleaned', False):
            return reservation
        reservation = _try_reserve_cleanup_unit(staging=True)
        if reservation is None:
            return None
        try:
            setattr(staged, '_cleanup_reservation', reservation)
        except (AttributeError, TypeError):
            reservation.release_staging()
            return None
        return reservation


def _ensure_promoted_cleanup_reservation(
    promoted: PromotedAttachments,
) -> _AttachmentCleanupReservation | None:
    with promoted._record_lock:
        reservation = promoted._cleanup_reservation
        if reservation is not None and reservation.has_rollback():
            return reservation
        reservation = _try_reserve_cleanup_unit(rollback=True)
        if reservation is None:
            return None
        object.__setattr__(promoted, '_cleanup_reservation', reservation)
        return reservation


async def cleanup_staged_attachments(staged: StagedAttachmentSet) -> None:
    reservation = _ensure_staging_cleanup_reservation(staged)
    key = _staging_cleanup_key(staged)
    release_unused = getattr(
        staged,
        '_release_unused_rollback_reservation',
        None,
    )
    if callable(release_unused):
        release_unused()
    errors: list[BaseException] = []
    cancelled: asyncio.CancelledError | None = None
    for _attempt in range(ATTACHMENT_CLEANUP_ATTEMPTS):
        attempt_cancelled, error = await _settle_cleanup_attempt(staged.cleanup())
        cancelled = cancelled or attempt_cancelled
        if error is None:
            with _PENDING_CLEANUP_LOCK:
                if _PENDING_STAGING_CLEANUPS.get(key) is staged:
                    _PENDING_STAGING_CLEANUPS.pop(key, None)
            if reservation is not None:
                reservation.release_staging()
            if cancelled is not None:
                raise cancelled
            return
        errors.append(error)
        logger.error(
            'Staged attachment cleanup attempt failed',
            exc_info=(type(error), error, error.__traceback__),
        )
    if reservation is None or not reservation.has_staging():
        errors.append(RuntimeError('Staging cleanup capacity is unavailable'))
        raise AttachmentCleanupError(errors) from errors[-1]
    newly_retained = False
    with _PENDING_CLEANUP_LOCK:
        existing = _PENDING_STAGING_CLEANUPS.get(key)
        if existing is not None and existing is not staged:
            errors.append(RuntimeError('Staging cleanup key is already retained'))
            raise AttachmentCleanupError(errors) from errors[-1]
        if existing is None:
            _PENDING_STAGING_CLEANUPS[key] = staged
            newly_retained = True
    if newly_retained:
        _schedule_pending_cleanup_drain()
    raise AttachmentCleanupError(errors) from errors[-1]


async def rollback_promoted_attachments(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> None:
    """Retry and retain a cancellation-settled promoted-media rollback."""
    reservation = _ensure_promoted_cleanup_reservation(promoted)
    key = _rollback_key(promoted, ownership)
    errors: list[BaseException] = []
    cancelled: asyncio.CancelledError | None = None
    for _attempt in range(ATTACHMENT_CLEANUP_ATTEMPTS):
        attempt_cancelled, error = await _settle_cleanup_attempt(
            _rollback_promoted_attachments(promoted, ownership)
        )
        cancelled = cancelled or attempt_cancelled
        if error is None:
            with _PENDING_CLEANUP_LOCK:
                current = _PENDING_ROLLBACKS.get(key)
                if current is not None and current[0] is promoted:
                    _PENDING_ROLLBACKS.pop(key, None)
            release_promoted_attachment_reservation(promoted)
            if cancelled is not None:
                raise cancelled
            return
        errors.append(error)
        logger.error(
            'Promoted attachment rollback attempt failed',
            exc_info=(type(error), error, error.__traceback__),
        )
    if reservation is None or not reservation.has_rollback():
        errors.append(RuntimeError('Rollback cleanup capacity is unavailable'))
        raise AttachmentCleanupError(errors) from errors[-1]
    newly_retained = False
    with _PENDING_CLEANUP_LOCK:
        existing = _PENDING_ROLLBACKS.get(key)
        if existing is not None and existing[0] is not promoted:
            errors.append(RuntimeError('Rollback cleanup key is already retained'))
            raise AttachmentCleanupError(errors) from errors[-1]
        if existing is None:
            _PENDING_ROLLBACKS[key] = (promoted, ownership)
            newly_retained = True
    if newly_retained:
        _schedule_pending_cleanup_drain()
    raise AttachmentCleanupError(errors) from errors[-1]


async def retry_pending_attachment_cleanups() -> int:
    """Retry retained cleanup work; return the number still pending."""
    with _PENDING_CLEANUP_LOCK:
        rollbacks = list(_PENDING_ROLLBACKS.values())
        staged_batches = list(_PENDING_STAGING_CLEANUPS.values())
    for promoted, ownership in rollbacks:
        try:
            await rollback_promoted_attachments(promoted, ownership)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Pending promoted attachment cleanup remains blocked')
    for staged in staged_batches:
        try:
            await cleanup_staged_attachments(staged)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Pending staging cleanup remains blocked')
    return pending_attachment_cleanup_count()


async def _attachment_maintenance_wait(delay: float) -> None:
    wake = _ATTACHMENT_MAINTENANCE_WAKE
    if wake is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(wake.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass
    finally:
        wake.clear()


async def _run_attachment_maintenance(
    *,
    wait: Callable[[float], Any] = _attachment_maintenance_wait,
    clock: Callable[[], float] = time.monotonic,
    cycles: int | None = None,
) -> None:
    """Run the one-per-process stale sweep and bounded retry supervisor."""
    next_sweep = clock()
    retry_delay = ATTACHMENT_MAINTENANCE_RETRY_INITIAL_SECONDS
    completed_cycles = 0
    while cycles is None or completed_cycles < cycles:
        now = clock()
        if now >= next_sweep:
            try:
                await sweep_stale_staging()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Attachment maintenance sweep failed')
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            next_sweep = clock() + ATTACHMENT_MAINTENANCE_SWEEP_SECONDS

        pending = pending_attachment_cleanup_count()
        if pending:
            try:
                pending = await retry_pending_attachment_cleanups()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Attachment maintenance retry failed')
                pending = pending_attachment_cleanup_count()
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
        current = clock()
        sweep_delay = max(0.0, next_sweep - current)
        if pending:
            delay = min(retry_delay, sweep_delay)
            retry_delay = min(
                ATTACHMENT_MAINTENANCE_RETRY_MAX_SECONDS,
                max(
                    ATTACHMENT_MAINTENANCE_RETRY_INITIAL_SECONDS,
                    retry_delay * 2,
                ),
            )
        else:
            retry_delay = ATTACHMENT_MAINTENANCE_RETRY_INITIAL_SECONDS
            delay = sweep_delay

        completed_cycles += 1
        if cycles is not None and completed_cycles >= cycles:
            return
        await wait(delay)


def start_attachment_maintenance() -> asyncio.Task | None:
    """Start or wake the single maintenance task owned by this process."""
    global _ATTACHMENT_MAINTENANCE_TASK
    global _ATTACHMENT_MAINTENANCE_WAKE
    global _ATTACHMENT_MAINTENANCE_LOOP
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = _ATTACHMENT_MAINTENANCE_TASK
    owner_loop = _ATTACHMENT_MAINTENANCE_LOOP
    if owner_loop is None and task is not None:
        get_loop = getattr(task, 'get_loop', None)
        if callable(get_loop):
            owner_loop = get_loop()
    if task is not None and not task.done():
        if owner_loop is loop:
            if _ATTACHMENT_MAINTENANCE_WAKE is not None:
                _ATTACHMENT_MAINTENANCE_WAKE.set()
            return task
        if owner_loop is not None and (
            owner_loop.is_closed() or not owner_loop.is_running()
        ):
            _ATTACHMENT_MAINTENANCE_TASK = None
            _ATTACHMENT_MAINTENANCE_WAKE = None
            _ATTACHMENT_MAINTENANCE_LOOP = None
        else:
            raise RuntimeError(
                'Attachment maintenance is active on another event loop'
            )
    elif task is not None:
        _ATTACHMENT_MAINTENANCE_TASK = None
        _ATTACHMENT_MAINTENANCE_WAKE = None
        _ATTACHMENT_MAINTENANCE_LOOP = None
    _ATTACHMENT_MAINTENANCE_WAKE = asyncio.Event()
    task = loop.create_task(_run_attachment_maintenance())
    _ATTACHMENT_MAINTENANCE_TASK = task
    _ATTACHMENT_MAINTENANCE_LOOP = loop

    def done(completed: asyncio.Task) -> None:
        global _ATTACHMENT_MAINTENANCE_TASK
        global _ATTACHMENT_MAINTENANCE_WAKE
        global _ATTACHMENT_MAINTENANCE_LOOP
        if (
            _ATTACHMENT_MAINTENANCE_TASK is completed
            and _ATTACHMENT_MAINTENANCE_LOOP is loop
        ):
            _ATTACHMENT_MAINTENANCE_TASK = None
            _ATTACHMENT_MAINTENANCE_WAKE = None
            _ATTACHMENT_MAINTENANCE_LOOP = None
        try:
            error = completed.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error(
                'Attachment maintenance task stopped unexpectedly',
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(done)
    return task


async def stop_attachment_maintenance() -> None:
    """Cancel and join the process-owned maintenance task."""
    global _ATTACHMENT_MAINTENANCE_TASK
    global _ATTACHMENT_MAINTENANCE_WAKE
    global _ATTACHMENT_MAINTENANCE_LOOP
    task = _ATTACHMENT_MAINTENANCE_TASK
    if task is None:
        return
    loop = asyncio.get_running_loop()
    owner_loop = _ATTACHMENT_MAINTENANCE_LOOP
    if owner_loop is None:
        get_loop = getattr(task, 'get_loop', None)
        if callable(get_loop):
            owner_loop = get_loop()
    if owner_loop is not loop:
        if owner_loop is not None and (
            owner_loop.is_closed() or not owner_loop.is_running()
        ):
            _ATTACHMENT_MAINTENANCE_TASK = None
            _ATTACHMENT_MAINTENANCE_WAKE = None
            _ATTACHMENT_MAINTENANCE_LOOP = None
            return
        raise RuntimeError(
            'Attachment maintenance is active on another event loop'
        )
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if _ATTACHMENT_MAINTENANCE_TASK is task:
            _ATTACHMENT_MAINTENANCE_TASK = None
            _ATTACHMENT_MAINTENANCE_WAKE = None
            _ATTACHMENT_MAINTENANCE_LOOP = None


def _schedule_pending_cleanup_drain() -> None:
    # Compatibility name retained for tests and older callers. Pending work is
    # now supervised until resolution rather than abandoned after three sleeps.
    start_attachment_maintenance()


async def _rollback_joined(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> None:
    await rollback_promoted_attachments(promoted, ownership)


async def promote_staged_attachments(
    staged: StagedAttachmentSet,
    ownership: MediaOwnership,
) -> PromotedAttachments:
    """Transactionally promote one claimed batch into session-owned storage."""
    promoted = PromotedAttachments(image_refs=[], document_paths=[])
    primary_error: BaseException | None = None
    primary_traceback = None
    try:
        await staged.claim()
        object.__setattr__(
            promoted,
            '_cleanup_reservation',
            staged._transfer_rollback_reservation(),
        )
        entries = await _run_thread_joined(_preflight_staged_entries, staged)
        _after_preflight_hook(entries)
        documents: list[_PinnedAttachment] = []
        for pinned in entries:
            await _run_thread_joined(
                _verify_pinned_source, pinned, staged
            )
            entry = pinned.staged
            image_ref = await media_assets.ingest_staged_image_if_recognized(
                entry, ownership, snapshot=pinned.snapshot
            )
            if image_ref is None:
                documents.append(pinned)
            else:
                promoted.image_refs.append(image_ref)

        if documents:
            destination = await _run_thread_joined(_tools_upload_root)
            for pinned in documents:
                await _run_thread_joined(
                    _verify_pinned_source,
                    pinned,
                    staged,
                )
                document = pinned.staged
                directory_leaf = f'd_{uuid4().hex}'
                file_leaf = f'f_{uuid4().hex}'
                filename = _safe_filename(document.filename)
                relative_value = (
                    Path('uploads') / directory_leaf / file_leaf
                ).as_posix()
                # Journal the planned opaque path before cancellation-settled
                # creation. A missing target is harmless during rollback.
                promoted.document_paths.append({
                    'name': filename,
                    'path': relative_value,
                })
                expected_digest = hashlib.sha256(pinned.snapshot).hexdigest()
                _record_document_rollback(
                    promoted,
                    relative_value,
                    directory_leaf=directory_leaf,
                    file_leaf=file_leaf,
                    expected_size=document.size_bytes,
                    expected_digest=expected_digest,
                    tools_root_identity=destination.tools_root_identity,
                    uploads_identity=destination.uploads_identity,
                )
                _before_secure_document_create_hook(
                    destination.path, directory_leaf, file_leaf
                )
                await _run_thread_joined(
                    _create_document_transaction,
                    pinned.snapshot,
                    destination,
                    directory_leaf,
                    file_leaf,
                    document.size_bytes,
                    promoted,
                    relative_value,
                    expected_digest,
                )
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__

    try:
        await cleanup_staged_attachments(staged)
    except BaseException as exc:
        if primary_error is None:
            primary_error = exc
            primary_traceback = exc.__traceback__
        else:
            logger.exception('Failed to clean staged attachment batch')

    if primary_error is not None:
        if promoted.image_refs or promoted.document_paths:
            try:
                await _rollback_joined(promoted, ownership)
            except BaseException as rollback_error:
                raise AttachmentCleanupError(
                    [primary_error, rollback_error]
                ) from rollback_error
        else:
            release_promoted_attachment_reservation(promoted)
        raise primary_error.with_traceback(primary_traceback)
    if not promoted.image_refs and not promoted.document_paths:
        release_promoted_attachment_reservation(promoted)
    return promoted


@dataclass(frozen=True)
class _ManifestEntry:
    identity: secure_fs.FileIdentity | None = None
    size: int | None = None
    digest: str | None = None


@dataclass(frozen=True)
class _ParsedManifest:
    created_at: float
    expires_at: float
    entries: dict[str, _ManifestEntry]


@dataclass(frozen=True)
class _SweepInspection:
    root_identity: secure_fs.FileIdentity
    batch_identity: secure_fs.FileIdentity
    manifest_identity: secure_fs.FileIdentity | None
    manifest_bytes: bytes | None
    manifest: _ParsedManifest | None
    recovery_identity: secure_fs.FileIdentity | None = None
    recovery_bytes: bytes | None = None
    recovery_children: dict[str, secure_fs.FileIdentity] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class _ParsedRecoveryJournal:
    root_identity: secure_fs.FileIdentity
    batch_identity: secure_fs.FileIdentity
    manifest_identity: secure_fs.FileIdentity
    batch_leaf: str
    children: dict[str, secure_fs.FileIdentity]
    manifest_bytes: bytes
    manifest: _ParsedManifest


def _parse_staging_manifest(data: bytes) -> _ParsedManifest:
    if len(data) > STAGING_MANIFEST_MAX_BYTES:
        raise MediaValidationError('Staging manifest exceeds its size limit')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise MediaValidationError('Staging manifest is invalid') from exc
    lines = text.splitlines()
    if not lines or len(lines) > STAGING_MANIFEST_MAX_RECORDS:
        raise MediaValidationError('Staging manifest is invalid')
    try:
        records = [json.loads(line) for line in lines]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaValidationError('Staging manifest is invalid') from exc
    header = records[0]
    if not isinstance(header, dict) or set(header) != {
        'created_at', 'expires_at', 'version'
    } or header.get('version') != 1:
        raise MediaValidationError('Staging manifest is invalid')
    try:
        created_at = float(header['created_at'])
        expires_at = float(header['expires_at'])
    except (TypeError, ValueError) as exc:
        raise MediaValidationError('Staging manifest is invalid') from exc
    if (
        not (created_at > 0 and expires_at > created_at)
        or expires_at - created_at > STAGING_LEASE_SECONDS + 1.0
    ):
        raise MediaValidationError('Staging manifest is invalid')

    entries: dict[str, _ManifestEntry] = {}
    for record in records[1:]:
        if not isinstance(record, dict):
            raise MediaValidationError('Staging manifest is invalid')
        operation = record.get('op')
        leaf = record.get('leaf')
        if (
            not isinstance(leaf, str)
            or not _OPAQUE_LEAF.fullmatch(leaf)
            or leaf == STAGING_MANIFEST_LEAF
        ):
            raise MediaValidationError('Staging manifest is invalid')
        if operation == 'plan':
            if set(record) != {'leaf', 'op'} or leaf in entries:
                raise MediaValidationError('Staging manifest is invalid')
            entries[leaf] = _ManifestEntry()
            continue
        if operation != 'done' or set(record) != {
            'file_id', 'leaf', 'op', 'sha256', 'size', 'volume'
        }:
            raise MediaValidationError('Staging manifest is invalid')
        planned = entries.get(leaf)
        size = record.get('size')
        digest = record.get('sha256')
        file_id = record.get('file_id')
        volume = record.get('volume')
        if (
            planned is None
            or planned.identity is not None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_UPLOAD_FILE_BYTES
            or not isinstance(digest, str)
            or re.fullmatch(r'[0-9a-f]{64}', digest) is None
            or not isinstance(file_id, str)
            or re.fullmatch(r'[0-9a-f]{32}', file_id) is None
            or not isinstance(volume, int)
            or isinstance(volume, bool)
            or volume < 0
        ):
            raise MediaValidationError('Staging manifest is invalid')
        entries[leaf] = _ManifestEntry(
            identity=secure_fs.FileIdentity(
                volume=volume,
                file_id=bytes.fromhex(file_id),
                is_directory=False,
            ),
            size=size,
            digest=digest,
        )
    if len(entries) > MAX_UPLOAD_COUNT:
        raise MediaValidationError('Staging manifest is invalid')
    return _ParsedManifest(created_at, expires_at, entries)


def _recovery_leaf(batch_leaf: str) -> str:
    return STAGING_RECOVERY_PREFIX + hashlib.sha256(
        batch_leaf.encode('ascii')
    ).hexdigest()[:32]


def _identity_manifest_value(identity: secure_fs.FileIdentity) -> dict[str, Any]:
    return {
        'file_id': identity.file_id.hex(),
        'is_directory': identity.is_directory,
        'volume': identity.volume,
    }


def _identity_from_manifest_value(
    value: Any,
    *,
    is_directory: bool,
) -> secure_fs.FileIdentity:
    if not isinstance(value, dict) or set(value) != {
        'file_id', 'is_directory', 'volume'
    }:
        raise MediaValidationError('Staging recovery journal is invalid')
    file_id = value.get('file_id')
    volume = value.get('volume')
    if (
        not isinstance(file_id, str)
        or re.fullmatch(r'[0-9a-f]{32}', file_id) is None
        or not isinstance(volume, int)
        or isinstance(volume, bool)
        or volume < 0
        or value.get('is_directory') is not is_directory
    ):
        raise MediaValidationError('Staging recovery journal is invalid')
    return secure_fs.FileIdentity(
        volume=volume,
        file_id=bytes.fromhex(file_id),
        is_directory=is_directory,
    )


def _recovery_journal_bytes(
    leaf: str,
    inspection: _SweepInspection,
    deletions: list[tuple[str, secure_fs.FileIdentity]],
) -> bytes:
    if inspection.manifest_identity is None or inspection.manifest_bytes is None:
        raise MediaValidationError('Staging recovery identity is unavailable')
    header = _manifest_line({
        'batch': _identity_manifest_value(inspection.batch_identity),
        'batch_leaf': leaf,
        'children': {
            child_leaf: _identity_manifest_value(identity)
            for child_leaf, identity in sorted(deletions)
        },
        'manifest': _identity_manifest_value(inspection.manifest_identity),
        'root': _identity_manifest_value(inspection.root_identity),
        'version': 2,
    })
    payload = header + inspection.manifest_bytes
    if len(payload) > STAGING_RECOVERY_MAX_BYTES:
        raise MediaValidationError('Staging recovery journal is too large')
    return payload


def _parse_recovery_journal(
    data: bytes,
    *,
    expected_leaf: str | None = None,
) -> _ParsedRecoveryJournal:
    if len(data) > STAGING_RECOVERY_MAX_BYTES:
        raise MediaValidationError('Staging recovery journal is too large')
    header_bytes, separator, manifest_bytes = data.partition(b'\n')
    if not separator or not header_bytes or not manifest_bytes:
        raise MediaValidationError('Staging recovery journal is invalid')
    try:
        header = json.loads(header_bytes.decode('utf-8'))
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaValidationError('Staging recovery journal is invalid') from exc
    if (
        not isinstance(header, dict)
        or set(header) != {
            'batch',
            'batch_leaf',
            'children',
            'manifest',
            'root',
            'version',
        }
        or header.get('version') != 2
    ):
        raise MediaValidationError('Staging recovery journal is invalid')
    batch_leaf = header.get('batch_leaf')
    if (
        not isinstance(batch_leaf, str)
        or not _OPAQUE_LEAF.fullmatch(batch_leaf)
        or batch_leaf.startswith(STAGING_RECOVERY_PREFIX)
        or (expected_leaf is not None and batch_leaf != expected_leaf)
    ):
        raise MediaValidationError('Staging recovery journal is invalid')
    root_identity = _identity_from_manifest_value(
        header.get('root'),
        is_directory=True,
    )
    batch_identity = _identity_from_manifest_value(
        header.get('batch'),
        is_directory=True,
    )
    manifest_identity = _identity_from_manifest_value(
        header.get('manifest'),
        is_directory=False,
    )
    manifest = _parse_staging_manifest(manifest_bytes)
    children_value = header.get('children')
    if not isinstance(children_value, dict) or len(children_value) > MAX_UPLOAD_COUNT:
        raise MediaValidationError('Staging recovery journal is invalid')
    children: dict[str, secure_fs.FileIdentity] = {}
    for child_leaf, identity_value in children_value.items():
        if (
            not isinstance(child_leaf, str)
            or not _OPAQUE_LEAF.fullmatch(child_leaf)
            or child_leaf not in manifest.entries
        ):
            raise MediaValidationError('Staging recovery journal is invalid')
        identity = _identity_from_manifest_value(
            identity_value,
            is_directory=False,
        )
        recorded = manifest.entries[child_leaf].identity
        if recorded is not None and recorded != identity:
            raise MediaValidationError('Staging recovery journal is invalid')
        children[child_leaf] = identity
    return _ParsedRecoveryJournal(
        root_identity=root_identity,
        batch_identity=batch_identity,
        manifest_identity=manifest_identity,
        batch_leaf=batch_leaf,
        children=children,
        manifest_bytes=manifest_bytes,
        manifest=manifest,
    )


def _read_recovery_inspection(
    root_capability: secure_fs.DirectoryCapability,
    leaf: str,
    batch_identity: secure_fs.FileIdentity,
) -> _SweepInspection | None:
    try:
        journal_capability = root_capability.open_file(_recovery_leaf(leaf))
    except FileNotFoundError:
        return None
    with journal_capability:
        journal_identity = journal_capability.identity
        journal_bytes = journal_capability.read_bytes(
            max_bytes=STAGING_RECOVERY_MAX_BYTES
        )
        if journal_capability.refresh_identity() != journal_identity:
            raise secure_fs.CapabilityError()
    recovery = _parse_recovery_journal(
        journal_bytes,
        expected_leaf=leaf,
    )
    if (
        recovery.root_identity != root_capability.identity
        or recovery.batch_identity != batch_identity
    ):
        raise secure_fs.CapabilityError()
    return _SweepInspection(
        root_identity=recovery.root_identity,
        batch_identity=recovery.batch_identity,
        manifest_identity=recovery.manifest_identity,
        manifest_bytes=recovery.manifest_bytes,
        manifest=recovery.manifest,
        recovery_identity=journal_identity,
        recovery_bytes=journal_bytes,
        recovery_children=recovery.children,
    )


def _inspect_manifest_batch(
    root: Path,
    leaf: str,
    prefer_recovery: bool = False,
) -> _SweepInspection | None:
    if (
        not _OPAQUE_LEAF.fullmatch(leaf)
        or leaf == STAGING_MANIFEST_LEAF
        or leaf.startswith(STAGING_RECOVERY_PREFIX)
    ):
        return None
    with secure_fs.open_root(root) as root_capability:
        root_identity = root_capability.identity
        if root_capability.refresh_identity() != root_identity:
            raise secure_fs.CapabilityError()
        try:
            batch_capability = root_capability.open_directory(leaf)
        except FileNotFoundError:
            return None
        batch_identity = batch_capability.identity
        with batch_capability:
            if batch_capability.refresh_identity() != batch_identity:
                raise secure_fs.CapabilityError()
            recovery_error: MediaValidationError | None = None
            try:
                recovery_inspection = _read_recovery_inspection(
                    root_capability,
                    leaf,
                    batch_identity,
                )
            except MediaValidationError as exc:
                # A torn journal can only be repaired after the surviving
                # manifest and every known child reproduce its exact payload.
                recovery_error = exc
            else:
                if recovery_inspection is not None:
                    return recovery_inspection
            if prefer_recovery:
                if recovery_error is not None:
                    raise recovery_error
                raise MediaValidationError(
                    'Staging recovery journal is unavailable'
                )
            try:
                manifest_capability = batch_capability.open_file(
                    STAGING_MANIFEST_LEAF
                )
            except FileNotFoundError:
                manifest_capability = None
            if manifest_capability is None:
                if recovery_error is not None:
                    raise recovery_error
                return _SweepInspection(
                    root_identity=root_identity,
                    batch_identity=batch_identity,
                    manifest_identity=None,
                    manifest_bytes=None,
                    manifest=None,
                )
            with manifest_capability:
                manifest_identity = manifest_capability.identity
                manifest_bytes = manifest_capability.read_bytes(
                    max_bytes=STAGING_MANIFEST_MAX_BYTES
                )
                if manifest_capability.refresh_identity() != manifest_identity:
                    raise secure_fs.CapabilityError()
                manifest = _parse_staging_manifest(manifest_bytes)
        return _SweepInspection(
            root_identity=root_identity,
            batch_identity=batch_identity,
            manifest_identity=manifest_identity,
            manifest_bytes=manifest_bytes,
            manifest=manifest,
        )


def _delete_empty_unmanifested_batch(
    root: Path,
    leaf: str,
    inspection: _SweepInspection,
) -> bool:
    if inspection.manifest is not None:
        raise ValueError('Expected an unmanifested batch')
    with secure_fs.open_root(root) as root_capability:
        if (
            root_capability.identity != inspection.root_identity
            or root_capability.refresh_identity() != inspection.root_identity
        ):
            raise secure_fs.CapabilityError()
        try:
            batch_capability = root_capability.open_directory(
                leaf,
                expected_identity=inspection.batch_identity,
            )
        except FileNotFoundError:
            return True
        with batch_capability:
            if batch_capability.refresh_identity() != inspection.batch_identity:
                raise secure_fs.CapabilityError()
        # A missing manifest authorizes removal only when the batch is empty.
        try:
            root_capability.delete_directory(
                leaf,
                expected_identity=inspection.batch_identity,
            )
        except (OSError, secure_fs.CapabilityError):
            return False
    return True


def _create_recovery_journal(
    root_capability: secure_fs.DirectoryCapability,
    leaf: str,
    inspection: _SweepInspection,
    deletions: list[tuple[str, secure_fs.FileIdentity]],
) -> tuple[str, secure_fs.FileIdentity]:
    recovery_leaf = _recovery_leaf(leaf)
    payload = _recovery_journal_bytes(leaf, inspection, deletions)
    try:
        journal = root_capability.create_file(recovery_leaf)
    except FileExistsError:
        with root_capability.open_file(recovery_leaf) as existing:
            existing_identity = existing.identity
            existing_bytes = existing.read_bytes(
                max_bytes=STAGING_RECOVERY_MAX_BYTES
            )
            if existing.refresh_identity() != existing_identity:
                raise secure_fs.CapabilityError()
        if existing_bytes == payload:
            return recovery_leaf, existing_identity
        # A crash can leave the deterministic destination as any strict prefix
        # of the expected bytes. The caller has already revalidated the exact
        # root, batch, manifest and children, so only that narrowly proved torn
        # file may be replaced before the first batch mutation.
        if not payload.startswith(existing_bytes):
            raise secure_fs.CapabilityError()
        root_capability.delete_file(
            recovery_leaf,
            expected_identity=existing_identity,
        )
        root_capability.flush()
        journal = root_capability.create_file(recovery_leaf)
    with journal:
        journal.write_bytes(payload, max_bytes=STAGING_RECOVERY_MAX_BYTES)
        journal.flush()
        if journal.refresh_identity() != journal.identity:
            raise secure_fs.CapabilityError()
        return recovery_leaf, journal.identity


def _delete_inspected_manifest_batch(
    root: Path,
    leaf: str,
    timestamp: float,
    inspection: _SweepInspection,
) -> bool:
    if (
        inspection.manifest is None
        or inspection.manifest_identity is None
        or inspection.manifest_bytes is None
    ):
        raise ValueError('Expected a manifested batch')
    recovery: tuple[str, secure_fs.FileIdentity] | None = None
    with secure_fs.open_root(root) as root_capability:
        if (
            root_capability.identity != inspection.root_identity
            or root_capability.refresh_identity() != inspection.root_identity
        ):
            raise secure_fs.CapabilityError()
        with root_capability.open_directory(
            leaf,
            expected_identity=inspection.batch_identity,
        ) as batch_capability:
            if batch_capability.refresh_identity() != inspection.batch_identity:
                raise secure_fs.CapabilityError()
            with batch_capability.open_file(
                STAGING_MANIFEST_LEAF,
                expected_identity=inspection.manifest_identity,
            ) as manifest_capability:
                manifest_bytes = manifest_capability.read_bytes(
                    max_bytes=STAGING_MANIFEST_MAX_BYTES
                )
                if (
                    manifest_bytes != inspection.manifest_bytes
                    or manifest_capability.refresh_identity()
                    != inspection.manifest_identity
                ):
                    raise secure_fs.CapabilityError()
                manifest = _parse_staging_manifest(manifest_bytes)
            if manifest != inspection.manifest:
                raise secure_fs.CapabilityError()
            if timestamp < manifest.expires_at:
                return False

            # Verify every known child before the first destructive mutation.
            deletions: list[tuple[str, secure_fs.FileIdentity]] = []
            for child_leaf, record in manifest.entries.items():
                try:
                    if record.identity is None:
                        child = batch_capability.open_file(child_leaf)
                    else:
                        child = batch_capability.open_file(
                            child_leaf,
                            expected_identity=record.identity,
                        )
                except FileNotFoundError:
                    continue
                with child:
                    identity = child.identity
                    if record.identity is not None:
                        content = child.read_bytes(
                            max_bytes=MAX_UPLOAD_FILE_BYTES
                        )
                        if (
                            len(content) != record.size
                            or hashlib.sha256(content).hexdigest()
                            != record.digest
                            or child.refresh_identity() != record.identity
                        ):
                            raise MediaValidationError(
                                'Staged attachment changed before sweep'
                            )
                deletions.append((child_leaf, identity))

            # Preserve the authoritative identities outside the directory that
            # is about to be mutated. If an unknown extra prevents final removal,
            # operators still have a durable recovery journal.
            recovery = _create_recovery_journal(
                root_capability,
                leaf,
                inspection,
                deletions,
            )
            # The recovery file contents and its staging-root directory entry
            # must both be durable before the first destructive mutation.
            root_capability.flush()
            try:
                for child_leaf, identity in deletions:
                    batch_capability.delete_file(
                        child_leaf,
                        expected_identity=identity,
                    )
                batch_capability.delete_file(
                    STAGING_MANIFEST_LEAF,
                    expected_identity=inspection.manifest_identity,
                )
            except Exception as exc:
                raise _SweepRecoveryRequired() from exc
        try:
            root_capability.delete_directory(
                leaf,
                expected_identity=inspection.batch_identity,
            )
            root_capability.flush()
        except Exception as exc:
            # Once a child or manifest delete has been attempted, the external
            # journal is the authoritative state. Never make the batch queued
            # or claimable again.
            raise _SweepRecoveryRequired() from exc
        if recovery is not None:
            try:
                try:
                    root_capability.delete_file(
                        recovery[0],
                        expected_identity=recovery[1],
                    )
                except FileNotFoundError:
                    pass
                root_capability.flush()
            except (OSError, secure_fs.CapabilityError):
                # The stale batch is gone; retain a redundant journal rather
                # than restoring a live lease for a nonexistent directory.
                logger.exception('Failed to remove completed recovery journal')
    return True


def _resume_recovery_batch(
    root: Path,
    leaf: str,
    inspection: _SweepInspection,
) -> bool:
    if (
        inspection.recovery_identity is None
        or inspection.recovery_bytes is None
        or inspection.manifest is None
        or inspection.manifest_identity is None
        or inspection.manifest_bytes is None
    ):
        raise ValueError('Expected a recoverable staged batch')
    recovery_leaf = _recovery_leaf(leaf)
    with secure_fs.open_root(root) as root_capability:
        if (
            root_capability.identity != inspection.root_identity
            or root_capability.refresh_identity() != inspection.root_identity
        ):
            raise secure_fs.CapabilityError()
        with root_capability.open_file(
            recovery_leaf,
            expected_identity=inspection.recovery_identity,
        ) as journal_capability:
            journal_bytes = journal_capability.read_bytes(
                max_bytes=STAGING_RECOVERY_MAX_BYTES
            )
            if (
                journal_bytes != inspection.recovery_bytes
                or journal_capability.refresh_identity()
                != inspection.recovery_identity
            ):
                raise secure_fs.CapabilityError()
            recovery = _parse_recovery_journal(
                journal_bytes,
                expected_leaf=leaf,
            )
        if (
            recovery.root_identity != inspection.root_identity
            or recovery.batch_identity != inspection.batch_identity
            or recovery.manifest_identity != inspection.manifest_identity
            or recovery.manifest_bytes != inspection.manifest_bytes
            or recovery.manifest != inspection.manifest
            or recovery.children != inspection.recovery_children
        ):
            raise secure_fs.CapabilityError()

        try:
            batch_capability = root_capability.open_directory(
                leaf,
                expected_identity=inspection.batch_identity,
            )
        except FileNotFoundError:
            # The batch deletion completed before the previous process stopped;
            # only the redundant external journal remains.
            root_capability.delete_file(
                recovery_leaf,
                expected_identity=inspection.recovery_identity,
            )
            root_capability.flush()
            return True

        with batch_capability:
            if batch_capability.refresh_identity() != inspection.batch_identity:
                raise secure_fs.CapabilityError()
            try:
                for child_leaf, identity in recovery.children.items():
                    try:
                        child = batch_capability.open_file(
                            child_leaf,
                            expected_identity=identity,
                        )
                    except FileNotFoundError:
                        continue
                    with child:
                        record = recovery.manifest.entries[child_leaf]
                        if record.identity is not None:
                            content = child.read_bytes(
                                max_bytes=MAX_UPLOAD_FILE_BYTES
                            )
                            if (
                                len(content) != record.size
                                or hashlib.sha256(content).hexdigest()
                                != record.digest
                                or child.refresh_identity() != identity
                            ):
                                raise MediaValidationError(
                                    'Staged attachment changed during recovery'
                                )
                    batch_capability.delete_file(
                        child_leaf,
                        expected_identity=identity,
                    )
                try:
                    manifest_capability = batch_capability.open_file(
                        STAGING_MANIFEST_LEAF,
                        expected_identity=recovery.manifest_identity,
                    )
                except FileNotFoundError:
                    manifest_capability = None
                if manifest_capability is not None:
                    with manifest_capability:
                        manifest_bytes = manifest_capability.read_bytes(
                            max_bytes=STAGING_MANIFEST_MAX_BYTES
                        )
                        if (
                            manifest_bytes != recovery.manifest_bytes
                            or manifest_capability.refresh_identity()
                            != recovery.manifest_identity
                        ):
                            raise secure_fs.CapabilityError()
                    batch_capability.delete_file(
                        STAGING_MANIFEST_LEAF,
                        expected_identity=recovery.manifest_identity,
                    )
            except Exception as exc:
                raise _SweepRecoveryRequired() from exc
        try:
            root_capability.delete_directory(
                leaf,
                expected_identity=recovery.batch_identity,
            )
            root_capability.flush()
        except Exception as exc:
            raise _SweepRecoveryRequired() from exc
        try:
            root_capability.delete_file(
                recovery_leaf,
                expected_identity=inspection.recovery_identity,
            )
            root_capability.flush()
        except (OSError, secure_fs.CapabilityError):
            logger.exception('Failed to remove completed recovery journal')
    return True


def _remove_redundant_recovery_journal(
    root: Path,
    recovery_leaf: str,
) -> bool:
    """Delete an exact valid journal only when its pinned batch is absent."""
    if (
        not _OPAQUE_LEAF.fullmatch(recovery_leaf)
        or not recovery_leaf.startswith(STAGING_RECOVERY_PREFIX)
    ):
        return False
    with secure_fs.open_root(root) as root_capability:
        root_identity = root_capability.identity
        if root_capability.refresh_identity() != root_identity:
            raise secure_fs.CapabilityError()
        try:
            journal_capability = root_capability.open_file(recovery_leaf)
        except FileNotFoundError:
            return False
        with journal_capability:
            journal_identity = journal_capability.identity
            journal_bytes = journal_capability.read_bytes(
                max_bytes=STAGING_RECOVERY_MAX_BYTES
            )
            if journal_capability.refresh_identity() != journal_identity:
                raise secure_fs.CapabilityError()
            recovery = _parse_recovery_journal(journal_bytes)
        if (
            _recovery_leaf(recovery.batch_leaf) != recovery_leaf
            or recovery.root_identity != root_identity
        ):
            raise secure_fs.CapabilityError()
        try:
            batch_capability = root_capability.open_directory(
                recovery.batch_leaf,
                expected_identity=recovery.batch_identity,
            )
        except FileNotFoundError:
            batch_capability = None
        if batch_capability is not None:
            with batch_capability:
                if (
                    batch_capability.refresh_identity()
                    != recovery.batch_identity
                ):
                    raise secure_fs.CapabilityError()
            return False
        root_capability.delete_file(
            recovery_leaf,
            expected_identity=journal_identity,
        )
        root_capability.flush()
    return True


async def sweep_stale_staging(now=None, max_age_seconds: float = 3600) -> int:
    """Remove expired batches through their durable capability manifest."""
    root = _staging_root()
    # Kept for API compatibility. The durable manifest expiry is authoritative
    # across restarts; mutable directory mtimes cannot authorize deletion.
    del max_age_seconds
    if now is None:
        timestamp = time.time()
    elif hasattr(now, 'timestamp'):
        timestamp = float(now.timestamp())
    else:
        timestamp = float(now)
    try:
        candidates = await _run_thread_joined(lambda: list(root.iterdir()))
    except FileNotFoundError:
        return 0

    removed = 0
    # Recovery journals are root-level siblings, not batch directories. Probe
    # them independently so a crash after batch deletion cannot strand a valid
    # journal forever. Journals for extant pinned batches remain owned by the
    # normal recovery path below.
    for candidate in candidates:
        recovery_leaf = candidate.name
        if not recovery_leaf.startswith(STAGING_RECOVERY_PREFIX):
            continue
        try:
            await _run_thread_joined(
                _remove_redundant_recovery_journal,
                root,
                recovery_leaf,
            )
        except (OSError, MediaValidationError, secure_fs.CapabilityError):
            logger.exception('Failed closed while inspecting recovery journal')

    for candidate in candidates:
        leaf = candidate.name
        if (
            not _OPAQUE_LEAF.fullmatch(leaf)
            or leaf == STAGING_MANIFEST_LEAF
            or leaf.startswith(STAGING_RECOVERY_PREFIX)
        ):
            continue
        lexical = _lexical_absolute(root / leaf)
        observed_lease: tuple[str, float | None] | None
        with _ACTIVE_LOCK:
            observed_lease = _BATCH_LEASES.get(lexical)
            if (
                observed_lease is not None
                and observed_lease[0] not in {'queued', 'recovering'}
            ):
                continue
        try:
            inspection = await _run_thread_joined(
                _inspect_manifest_batch,
                root,
                leaf,
                observed_lease is not None
                and observed_lease[0] == 'recovering',
            )
        except (OSError, MediaValidationError, secure_fs.CapabilityError):
            logger.exception('Failed closed while inspecting staged attachments')
            continue
        if inspection is None:
            continue
        recovery_mode = inspection.recovery_identity is not None
        if inspection.manifest is None:
            # A registered batch must retain its durable ownership manifest.
            if observed_lease is not None:
                continue
            try:
                deleted = await _run_thread_joined(
                    _delete_empty_unmanifested_batch,
                    root,
                    leaf,
                    inspection,
                )
            except (OSError, MediaValidationError, secure_fs.CapabilityError):
                logger.exception(
                    'Failed closed while sweeping unmanifested attachments'
                )
                deleted = False
            if deleted:
                removed += 1
            continue
        if not recovery_mode and timestamp < inspection.manifest.expires_at:
            # Inspection is read-only; an unexpired queued action remains
            # claimable throughout this filesystem probe.
            continue

        reserved_lease: tuple[str, float | None] | None = None
        with _ACTIVE_LOCK:
            current_lease = _BATCH_LEASES.get(lexical)
            if recovery_mode:
                if observed_lease is None:
                    if current_lease is not None:
                        continue
                elif (
                    current_lease != observed_lease
                    or observed_lease[0] != 'recovering'
                ):
                    continue
                reserved_lease = observed_lease
                _BATCH_LEASES[lexical] = ('sweeping-recovery', None)
            else:
                if observed_lease is None:
                    if current_lease is not None:
                        continue
                else:
                    if (
                        current_lease != observed_lease
                        or observed_lease[0] != 'queued'
                        or observed_lease[1]
                        != inspection.manifest.expires_at
                    ):
                        continue
                    reserved_lease = observed_lease
                    _BATCH_LEASES[lexical] = (
                        'sweeping',
                        observed_lease[1],
                    )
        requires_recovery = recovery_mode
        try:
            if recovery_mode:
                deleted = await _run_thread_joined(
                    _resume_recovery_batch,
                    root,
                    leaf,
                    inspection,
                )
            else:
                deleted = await _run_thread_joined(
                    _delete_inspected_manifest_batch,
                    root,
                    leaf,
                    timestamp,
                    inspection,
                )
        except _SweepRecoveryRequired:
            logger.exception('Staged attachment sweep requires recovery')
            requires_recovery = True
            deleted = False
        except (OSError, MediaValidationError, secure_fs.CapabilityError):
            logger.exception('Failed closed while sweeping staged attachments')
            deleted = False
        if not deleted:
            with _ACTIVE_LOCK:
                current = _BATCH_LEASES.get(lexical)
                if requires_recovery:
                    if current is None or current[0] in {
                        'sweeping',
                        'sweeping-recovery',
                    }:
                        _BATCH_LEASES[lexical] = ('recovering', None)
                elif (
                    reserved_lease is not None
                    and current is not None
                    and current[0] == 'sweeping'
                ):
                    _BATCH_LEASES[lexical] = reserved_lease
            continue
        with _ACTIVE_LOCK:
            _ACTIVE_BATCHES.discard(lexical)
            _BATCH_LEASES.pop(lexical, None)
            staged = _BATCH_OBJECTS.pop(lexical, None)
            if staged is not None:
                staged._cleaned = True
                staged._claimed = False
        if staged is not None:
            staged._release_removed_reservations()
        removed += 1
    return removed


__all__ = [
    'AttachmentCleanupError',
    'CHUNK_BYTES',
    'MAX_UPLOAD_COUNT',
    'MAX_UPLOAD_FILE_BYTES',
    'MAX_UPLOAD_TOTAL_BYTES',
    'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
    'PromotedAttachments',
    'STAGING_LEASE_SECONDS',
    'StagedAttachmentSet',
    '_decode_data_url',
    'cleanup_staged_attachments',
    'pending_attachment_cleanup_count',
    'promote_staged_attachments',
    'release_promoted_attachment_reservation',
    'rollback_promoted_attachments',
    'retry_pending_attachment_cleanups',
    'stage_legacy_data_urls',
    'stage_upload_files',
    'start_attachment_maintenance',
    'stop_attachment_maintenance',
    'sweep_stale_staging',
]
