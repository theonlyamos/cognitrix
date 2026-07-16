"""Bounded, short-lived staging for browser chat attachments."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
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

from .types import StagedAttachment

CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 20

_ACTIVE_BATCHES: set[Path] = set()
_ACTIVE_LOCK = threading.RLock()
_T = TypeVar('_T')


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
    try:
        await _run_thread_joined(batch.mkdir, 0o700, False, False)
    except BaseException:
        try:
            if _path_lexists(batch):
                await _run_thread_joined(_remove_batch_path, batch)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_BATCHES.discard(batch)
        raise
    return StagedAttachmentSet(batch_dir=batch, entries=[])


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
        return batch
    except BaseException:
        await batch.cleanup()
        raise


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
            if lexical in _ACTIVE_BATCHES:
                continue
        try:
            metadata = await _run_thread_joined(os.lstat, lexical)
        except FileNotFoundError:
            continue
        if timestamp - metadata.st_mtime <= max_age_seconds:
            continue
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
    'StagedAttachmentSet',
    '_decode_data_url',
    'stage_legacy_data_urls',
    'stage_upload_files',
    'sweep_stale_staging',
]
