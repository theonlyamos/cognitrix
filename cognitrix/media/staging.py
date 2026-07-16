"""Bounded, short-lived staging for browser chat attachments."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import shutil
import stat
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

from .types import StagedAttachment

CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 20
STAGING_LEASE_SECONDS = 3600.0

_ACTIVE_BATCHES: set[Path] = set()
_BATCH_LEASES: dict[Path, tuple[str, float | None]] = {}
_ACTIVE_LOCK = threading.RLock()
_T = TypeVar('_T')
logger = logging.getLogger('cognitrix.log')


def _staging_root() -> Path:
    return (settings.workdir / 'staging' / 'chat-media').resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_absolute(path: Path) -> Path:
    """Normalize dot segments without resolving links or reparse points."""
    return Path(os.path.abspath(os.fspath(path)))


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_top_level_link(path: Path) -> bool:
    """Identify symlinks and Windows junction/reparse points via lstat."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, 'is_junction', None)
        if is_junction is not None and is_junction():
            return True
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, 'st_file_attributes', 0)
    reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    return bool(attributes & reparse_flag)


def _unlink_top_level_link(path: Path) -> None:
    """Remove the link/junction itself, never anything below its target."""
    try:
        path.unlink()
    except (IsADirectoryError, PermissionError):
        os.rmdir(path)


def _remove_batch_path(path: Path) -> None:
    if _is_top_level_link(path):
        _unlink_top_level_link(path)
        return
    metadata = os.lstat(path)
    if stat.S_ISDIR(metadata.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


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


async def _open_path_joined(path: Path, mode: str):
    """Close a file opened by a worker if cancellation wins the handoff."""
    worker = asyncio.create_task(asyncio.to_thread(path.open, mode))
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
    _cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _cleaned: bool = field(default=False, init=False, repr=False)

    async def claim(self) -> None:
        """Atomically transfer a queued batch to the SSE consumer."""
        batch = _lexical_absolute(self.batch_dir)
        with _ACTIVE_LOCK:
            state = _BATCH_LEASES.get(batch)
            if state is None:
                raise MediaValidationError('Staged attachments are unavailable')
            phase, expires_at = state
            if phase != 'queued':
                raise MediaValidationError('Staged attachments are unavailable')
            # Even an elapsed queued lease may be claimed if the sweep has not
            # atomically reserved it yet. This avoids deleting a dequeued batch.
            _BATCH_LEASES[batch] = ('claimed', None)

    async def cleanup(self) -> None:
        """Delete this batch exactly once; repeated calls are harmless."""
        async with self._cleanup_lock:
            if self._cleaned:
                return
            root = _staging_root()
            batch = _lexical_absolute(self.batch_dir)
            if batch.parent != root or not _is_within(batch, root):
                raise ValueError('Refusing to clean a path outside the staging root')
            cancelled: asyncio.CancelledError | None = None
            try:
                try:
                    await _run_thread_joined(_remove_batch_path, batch)
                except FileNotFoundError:
                    pass
                except asyncio.CancelledError as exc:
                    # The joined worker has already settled; finish the
                    # ownership transition before cancellation escapes.
                    cancelled = exc
                if not _path_lexists(batch):
                    self._cleaned = True
            finally:
                # cleanup() is the ownership release point. If deletion failed,
                # leave the batch inactive so a later cleanup/TTL sweep can retry.
                with _ACTIVE_LOCK:
                    _ACTIVE_BATCHES.discard(batch)
                    _BATCH_LEASES.pop(batch, None)
            if cancelled is not None:
                raise cancelled


async def _create_batch(*, user_key: str, stream_id: str) -> StagedAttachmentSet:
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
    try:
        await _run_thread_joined(batch.mkdir, 0o700, False, False)
    except BaseException:
        try:
            if _path_lexists(batch):
                await _run_thread_joined(_remove_batch_path, batch)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_BATCHES.discard(batch)
                _BATCH_LEASES.pop(batch, None)
        raise
    return StagedAttachmentSet(batch_dir=batch, entries=[])


def _mark_queued(batch: StagedAttachmentSet) -> None:
    path = _lexical_absolute(batch.batch_dir)
    with _ACTIVE_LOCK:
        state = _BATCH_LEASES.get(path)
        if state is None or state[0] != 'writing':
            raise RuntimeError('Staging ownership was lost before enqueue')
        _BATCH_LEASES[path] = ('queued', time.time() + STAGING_LEASE_SECONDS)


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


async def _open_destination(batch: StagedAttachmentSet, index: int):
    destination = _lexical_absolute(
        batch.batch_dir / f'{index:02d}-{uuid4().hex}'
    )
    if destination.parent != batch.batch_dir:
        raise RuntimeError('Unsafe staging destination path')
    handle = await _open_path_joined(destination, 'xb')
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
            await _run_thread_joined(handle.write, chunk)
    finally:
        await _close_handle(handle)
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
    await sweep_stale_staging()
    if len(uploads) > MAX_UPLOAD_COUNT:
        await _close_uploads(uploads)
        raise _limit_error()

    batch = await _create_batch(user_key=user_key, stream_id=stream_id)
    try:
        total = 0
        for index, upload in enumerate(uploads):
            if not callable(getattr(upload, 'read', None)):
                raise _invalid_attachment()
            total += await _copy_upload(
                upload, batch=batch, index=index, total_so_far=total
            )
    except BaseException:
        try:
            await _close_uploads(uploads)
        finally:
            await batch.cleanup()
        raise
    try:
        await _close_uploads(uploads)
    except BaseException:
        await batch.cleanup()
        raise
    try:
        _mark_queued(batch)
    except BaseException:
        await batch.cleanup()
        raise
    return batch


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
            if written > expected_size:
                raise _limit_error()
            await _run_thread_joined(handle.write, decoded)
    finally:
        await _close_handle(handle)
    if written != expected_size:
        raise _invalid_attachment()
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
        prepared.append((_safe_filename(attachment.get('name')), mime, payload, size))

    batch = await _create_batch(user_key=user_key, stream_id=stream_id)
    try:
        for index, (filename, mime, payload, size) in enumerate(prepared):
            await _write_legacy_payload(
                payload,
                batch=batch,
                index=index,
                filename=filename,
                mime=mime,
                expected_size=size,
            )
        _mark_queued(batch)
        return batch
    except BaseException:
        await batch.cleanup()
        raise


@dataclass(frozen=True)
class PromotedAttachments:
    image_refs: list[ArtifactRef]
    document_paths: list[dict[str, str]]


def _preflight_staged_entries(staged: StagedAttachmentSet) -> list[StagedAttachment]:
    root = _staging_root()
    raw_batch = Path(staged.batch_dir)
    batch = _lexical_absolute(raw_batch)
    if not raw_batch.is_absolute() or raw_batch != batch:
        raise MediaValidationError('Staged attachments are unavailable')
    if batch.parent != root or not _is_within(batch, root):
        raise MediaValidationError('Staged attachments are unavailable')
    if _is_top_level_link(batch):
        raise MediaValidationError('Staged attachments are unavailable')
    try:
        batch_metadata = os.lstat(batch)
    except OSError as exc:
        raise MediaValidationError('Staged attachments are unavailable') from exc
    if not stat.S_ISDIR(batch_metadata.st_mode):
        raise MediaValidationError('Staged attachments are unavailable')

    entries = list(staged.entries)
    if len(entries) > MAX_UPLOAD_COUNT:
        raise MediaValidationError('Staged attachments exceed the count limit')
    total = 0
    for entry in entries:
        raw_path = Path(entry.path)
        path = _lexical_absolute(raw_path)
        if not raw_path.is_absolute() or raw_path != path:
            raise MediaValidationError('Staged attachments are unavailable')
        if path.parent != batch or not _is_within(path, batch):
            raise MediaValidationError('Staged attachments are unavailable')
        if _is_top_level_link(path):
            raise MediaValidationError('Staged attachments are unavailable')
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise MediaValidationError('Staged attachments are unavailable') from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise MediaValidationError('Staged attachment is not a regular file')
        if metadata.st_size != entry.size_bytes or entry.size_bytes < 0:
            raise MediaValidationError('Staged attachment size changed')
        if entry.size_bytes > MAX_UPLOAD_FILE_BYTES:
            raise MediaValidationError('Staged attachment exceeds the size limit')
        total += entry.size_bytes
        if total > MAX_UPLOAD_TOTAL_BYTES:
            raise MediaValidationError('Staged attachments exceed the total size limit')
    return entries


def _tools_upload_root() -> Path:
    tools_root = _lexical_absolute(settings.tools_root)
    uploads = _lexical_absolute(tools_root / 'uploads')
    if uploads.parent != tools_root:
        raise MediaValidationError('Upload destination is unavailable')
    uploads.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_top_level_link(uploads):
        raise MediaValidationError('Upload destination is unavailable')
    return uploads


def _copy_document_transaction(
    source: Path,
    temporary: Path,
    final: Path,
    expected_size: int,
) -> None:
    if _is_top_level_link(source):
        raise MediaValidationError('Staged attachments are unavailable')
    copied = 0
    temporary.parent.mkdir(mode=0o700, parents=False, exist_ok=False)
    if _is_top_level_link(temporary.parent):
        raise MediaValidationError('Upload destination is unavailable')
    try:
        with source.open('rb') as input_stream, temporary.open('xb') as output_stream:
            while True:
                chunk = input_stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > expected_size or copied > MAX_UPLOAD_FILE_BYTES:
                    raise MediaValidationError('Staged attachment size changed')
                output_stream.write(chunk)
        if copied != expected_size:
            raise MediaValidationError('Staged attachment size changed')
        os.replace(temporary, final)
        source.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _document_final_path(relative_value: str) -> Path:
    relative = Path(relative_value)
    if (
        relative.is_absolute()
        or relative.drive
        or '..' in relative.parts
        or len(relative.parts) < 3
        or relative.parts[0] != 'uploads'
    ):
        raise MediaValidationError('Invalid promoted document path')
    tools_root = _lexical_absolute(settings.tools_root)
    path = _lexical_absolute(tools_root / relative)
    uploads = _lexical_absolute(tools_root / 'uploads')
    if not _is_within(path, uploads):
        raise MediaValidationError('Invalid promoted document path')
    return path


async def _rollback_promoted_attachments(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> None:
    """Remove a promoted batch after a pre-scheduling stop or failure."""
    errors: list[BaseException] = []
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
            path = _document_final_path(document['path'])
            await _run_thread_joined(lambda: path.unlink(missing_ok=True))
            try:
                await _run_thread_joined(path.parent.rmdir)
            except OSError:
                pass
        except BaseException as exc:
            errors.append(exc)
            logger.exception('Failed to roll back promoted document')
    if errors:
        raise errors[0]


async def rollback_promoted_attachments(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> None:
    """Cancellation-settled public rollback for an already promoted batch."""
    await _run_awaitable_joined(
        _rollback_promoted_attachments(promoted, ownership)
    )


async def _rollback_joined(
    promoted: PromotedAttachments,
    ownership: MediaOwnership,
) -> None:
    try:
        await rollback_promoted_attachments(promoted, ownership)
    except BaseException:
        logger.exception('Attachment promotion rollback did not fully complete')


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
        entries = await _run_thread_joined(_preflight_staged_entries, staged)
        documents: list[StagedAttachment] = []
        for entry in entries:
            image_ref = await media_assets.ingest_staged_image_if_recognized(
                entry, ownership
            )
            if image_ref is None:
                documents.append(entry)
            else:
                promoted.image_refs.append(image_ref)

        if documents:
            uploads = await _run_thread_joined(_tools_upload_root)
            for document in documents:
                opaque = uuid4().hex
                directory = _lexical_absolute(uploads / opaque)
                filename = _safe_filename(document.filename)
                final = _lexical_absolute(directory / filename)
                temporary = _lexical_absolute(
                    directory / f'.{uuid4().hex}.tmp'
                )
                if (
                    directory.parent != uploads
                    or not _is_within(directory, uploads)
                    or final.parent != directory
                    or temporary.parent != directory
                ):
                    raise MediaValidationError('Upload destination is unavailable')
                relative = final.relative_to(_lexical_absolute(settings.tools_root))
                promoted.document_paths.append({
                    'name': filename,
                    'path': relative.as_posix(),
                })
                await _run_thread_joined(
                    _copy_document_transaction,
                    _lexical_absolute(document.path),
                    temporary,
                    final,
                    document.size_bytes,
                )
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__

    try:
        await staged.cleanup()
    except BaseException as exc:
        if primary_error is None:
            primary_error = exc
            primary_traceback = exc.__traceback__
        else:
            logger.exception('Failed to clean staged attachment batch')

    if primary_error is not None:
        if promoted.image_refs or promoted.document_paths:
            await _rollback_joined(promoted, ownership)
        raise primary_error.with_traceback(primary_traceback)
    return promoted


async def sweep_stale_staging(now=None, max_age_seconds: float = 3600) -> int:
    """Remove inactive top-level batches older than the configured TTL."""
    root = _staging_root()
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
    for candidate in candidates:
        lexical = _lexical_absolute(candidate)
        if lexical.parent != root:
            continue
        with _ACTIVE_LOCK:
            lease = _BATCH_LEASES.get(lexical)
            if lease is not None:
                phase, expires_at = lease
                if phase in {'writing', 'claimed'}:
                    continue
                if expires_at is None or timestamp < expires_at:
                    continue
        try:
            metadata = await _run_thread_joined(os.lstat, lexical)
        except FileNotFoundError:
            continue
        if timestamp - metadata.st_mtime <= max_age_seconds:
            continue
        # Claim deletion under the same lock used by StagedAttachmentSet.claim.
        # A claimant either wins first or observes that the lease is gone.
        with _ACTIVE_LOCK:
            lease = _BATCH_LEASES.get(lexical)
            if lease is not None:
                phase, expires_at = lease
                if phase != 'queued' or expires_at is None or timestamp < expires_at:
                    continue
                _BATCH_LEASES.pop(lexical, None)
                _ACTIVE_BATCHES.discard(lexical)
        try:
            await _run_thread_joined(_remove_batch_path, lexical)
        except FileNotFoundError:
            continue
        removed += 1
    return removed


__all__ = [
    'CHUNK_BYTES',
    'MAX_UPLOAD_COUNT',
    'MAX_UPLOAD_FILE_BYTES',
    'MAX_UPLOAD_TOTAL_BYTES',
    'PromotedAttachments',
    'STAGING_LEASE_SECONDS',
    'StagedAttachmentSet',
    '_decode_data_url',
    'promote_staged_attachments',
    'rollback_promoted_attachments',
    'stage_legacy_data_urls',
    'stage_upload_files',
    'sweep_stale_staging',
]
