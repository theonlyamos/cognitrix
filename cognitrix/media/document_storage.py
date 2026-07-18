"""Capability-pinned physical storage for durable document artifacts.

This module owns document namespace creation, exact deletion, recovery
inspection, and bounded reads. Promotion orchestration and database state live
elsewhere and depend only on this small API.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from cognitrix.config import settings
from cognitrix.media import secure_fs
from cognitrix.media.types import MediaValidationError

MAX_DOCUMENT_STORAGE_BYTES = 10 * 1024 * 1024


def _lexical_absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


async def _run_thread_joined(func, *args):
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


@dataclass(frozen=True)
class DocumentStorageDestination:
    path: Path
    tools_root_identity: secure_fs.FileIdentity
    uploads_identity: secure_fs.FileIdentity


@dataclass(frozen=True)
class DocumentStorageRecord:
    relative_path: str
    directory_leaf: str
    file_leaf: str
    expected_size: int
    expected_digest: str
    document_id: str | None = None
    tools_root_identity: secure_fs.FileIdentity | None = None
    uploads_identity: secure_fs.FileIdentity | None = None
    directory_identity: secure_fs.FileIdentity | None = None
    file_identity: secure_fs.FileIdentity | None = None


def prepare_document_destination() -> DocumentStorageDestination:
    tools_root = _lexical_absolute(settings.tools_root)
    uploads = _lexical_absolute(tools_root / 'uploads')
    if uploads.parent != tools_root:
        raise MediaValidationError('Upload destination is unavailable')
    tools_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with secure_fs.open_root(tools_root) as tools_capability:
            tools_root_identity = tools_capability.identity
            if tools_capability.refresh_identity() != tools_root_identity:
                raise secure_fs.CapabilityError()
            try:
                uploads_capability = tools_capability.open_directory('uploads')
            except FileNotFoundError:
                try:
                    uploads_capability = tools_capability.create_directory('uploads')
                except FileExistsError:
                    uploads_capability = tools_capability.open_directory('uploads')
            with uploads_capability:
                uploads_identity = uploads_capability.identity
                if uploads_capability.refresh_identity() != uploads_identity:
                    raise secure_fs.CapabilityError()
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError('Upload destination is unavailable') from exc
    return DocumentStorageDestination(
        path=uploads,
        tools_root_identity=tools_root_identity,
        uploads_identity=uploads_identity,
    )


def _created_identity(
    error: secure_fs.CreatedChildCleanupError,
    *,
    leaf: str,
    directory: bool,
) -> secure_fs.FileIdentity:
    if (
        error.leaf != leaf
        or error.is_directory is not directory
        or error.identity.is_directory is not directory
    ):
        raise secure_fs.CapabilityError() from error
    return error.identity


def _validate_unknown_creation(
    error: secure_fs.CreatedChildUnknownIdentityError,
    *,
    leaf: str,
    directory: bool,
) -> None:
    if error.leaf != leaf or error.is_directory is not directory:
        raise secure_fs.CapabilityError() from error


def _progress(
    callback: Callable[[DocumentStorageRecord], None] | None,
    record: DocumentStorageRecord,
) -> None:
    if callback is not None:
        callback(record)


def create_document_sync(
    snapshot: bytes,
    destination: DocumentStorageDestination,
    record: DocumentStorageRecord,
    on_progress: Callable[[DocumentStorageRecord], None] | None = None,
) -> DocumentStorageRecord:
    if (
        len(snapshot) != record.expected_size
        or len(snapshot) > MAX_DOCUMENT_STORAGE_BYTES
        or hashlib.sha256(snapshot).hexdigest() != record.expected_digest
    ):
        raise MediaValidationError('Staged attachment changed')
    expected_path = (
        Path('uploads') / record.directory_leaf / record.file_leaf
    ).as_posix()
    if record.relative_path != expected_path:
        raise MediaValidationError('Invalid promoted document path')
    current = replace(
        record,
        tools_root_identity=destination.tools_root_identity,
        uploads_identity=destination.uploads_identity,
    )
    _progress(on_progress, current)
    try:
        with secure_fs.open_root(destination.path) as root_capability:
            if (
                root_capability.identity != destination.uploads_identity
                or root_capability.refresh_identity()
                != destination.uploads_identity
            ):
                raise secure_fs.CapabilityError()
            try:
                directory_capability = root_capability.create_directory(
                    record.directory_leaf
                )
            except secure_fs.CreatedChildCleanupError as exc:
                current = replace(
                    current,
                    directory_identity=_created_identity(
                        exc, leaf=record.directory_leaf, directory=True
                    ),
                )
                _progress(on_progress, current)
                raise
            except secure_fs.CreatedChildUnknownIdentityError as exc:
                _validate_unknown_creation(
                    exc, leaf=record.directory_leaf, directory=True
                )
                raise
            with directory_capability:
                current = replace(
                    current, directory_identity=directory_capability.identity
                )
                _progress(on_progress, current)
                try:
                    output = directory_capability.create_document_file(
                        record.file_leaf
                    )
                except secure_fs.CreatedChildCleanupError as exc:
                    current = replace(
                        current,
                        file_identity=_created_identity(
                            exc, leaf=record.file_leaf, directory=False
                        ),
                    )
                    _progress(on_progress, current)
                    raise
                except secure_fs.CreatedChildUnknownIdentityError as exc:
                    _validate_unknown_creation(
                        exc, leaf=record.file_leaf, directory=False
                    )
                    raise
                with output:
                    current = replace(current, file_identity=output.identity)
                    _progress(on_progress, current)
                    output.write_bytes(
                        snapshot, max_bytes=MAX_DOCUMENT_STORAGE_BYTES
                    )
                    if output.refresh_identity() != output.identity:
                        raise secure_fs.CapabilityError()
                if (
                    directory_capability.refresh_identity()
                    != directory_capability.identity
                ):
                    raise secure_fs.CapabilityError()
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError('Upload destination is unavailable') from exc
    return current


async def create_document(
    snapshot: bytes,
    destination: DocumentStorageDestination,
    record: DocumentStorageRecord,
    on_progress: Callable[[DocumentStorageRecord], None] | None = None,
) -> DocumentStorageRecord:
    return await _run_thread_joined(
        create_document_sync, snapshot, destination, record, on_progress
    )


def _resolve_upload_root(record: DocumentStorageRecord) -> Path:
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
                'uploads', expected_identity=record.uploads_identity
            ) as uploads_capability:
                if uploads_capability.refresh_identity() != record.uploads_identity:
                    raise secure_fs.CapabilityError()
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError(
            'Promoted document cleanup root changed'
        ) from exc
    return uploads


def inspect_document_sync(
    record: DocumentStorageRecord,
) -> DocumentStorageRecord | None:
    """Capture exact child identities and verify bytes for one pending intent."""
    try:
        uploads = _resolve_upload_root(record)
        with secure_fs.open_root(uploads) as root_capability:
            if root_capability.identity != record.uploads_identity:
                raise secure_fs.CapabilityError()
            try:
                directory = root_capability.open_directory(record.directory_leaf)
            except FileNotFoundError:
                return None
            with directory:
                inspected = replace(record, directory_identity=directory.identity)
                try:
                    file_capability = directory.open_document_file(
                        record.file_leaf
                    )
                except FileNotFoundError:
                    return inspected
                with file_capability:
                    content = file_capability.read_bytes(
                        max_bytes=MAX_DOCUMENT_STORAGE_BYTES
                    )
                    if (
                        len(content) != record.expected_size
                        or hashlib.sha256(content).hexdigest()
                        != record.expected_digest
                        or file_capability.refresh_identity()
                        != file_capability.identity
                    ):
                        raise secure_fs.CapabilityError()
                    return replace(
                        inspected, file_identity=file_capability.identity
                    )
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError(
            'Promoted document recovery inspection failed'
        ) from exc


async def inspect_document(
    record: DocumentStorageRecord,
) -> DocumentStorageRecord | None:
    return await _run_thread_joined(inspect_document_sync, record)


def delete_document_sync(record: DocumentStorageRecord) -> None:
    expected_path = (
        Path('uploads') / record.directory_leaf / record.file_leaf
    ).as_posix()
    if record.relative_path != expected_path:
        raise MediaValidationError('Invalid promoted document path')
    try:
        uploads = _resolve_upload_root(record)
        with secure_fs.open_root(uploads) as root_capability:
            if (
                root_capability.identity != record.uploads_identity
                or root_capability.refresh_identity() != record.uploads_identity
            ):
                raise secure_fs.CapabilityError()
            if record.directory_identity is None:
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
                directory = root_capability.open_directory(
                    record.directory_leaf,
                    expected_identity=record.directory_identity,
                )
            except FileNotFoundError:
                try:
                    root_capability.delete_directory(
                        record.directory_leaf,
                        expected_identity=record.directory_identity,
                    )
                except FileNotFoundError:
                    pass
                return
            with directory:
                if directory.refresh_identity() != record.directory_identity:
                    raise secure_fs.CapabilityError()
                if record.file_identity is not None:
                    try:
                        directory.delete_document_file(
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


async def delete_document(record: DocumentStorageRecord) -> None:
    await _run_thread_joined(delete_document_sync, record)


def read_document_sync(record: DocumentStorageRecord) -> bytes:
    if record.directory_identity is None or record.file_identity is None:
        raise MediaValidationError('Document storage identity is incomplete')
    try:
        uploads = _resolve_upload_root(record)
        with secure_fs.open_root(uploads) as root_capability:
            with root_capability.open_directory(
                record.directory_leaf,
                expected_identity=record.directory_identity,
            ) as directory:
                with directory.open_document_file(
                    record.file_leaf,
                    expected_identity=record.file_identity,
                ) as file_capability:
                    content = file_capability.read_bytes(
                        max_bytes=MAX_DOCUMENT_STORAGE_BYTES
                    )
                    if (
                        len(content) != record.expected_size
                        or hashlib.sha256(content).hexdigest()
                        != record.expected_digest
                        or file_capability.refresh_identity()
                        != record.file_identity
                    ):
                        raise secure_fs.CapabilityError()
                    return content
    except (OSError, secure_fs.CapabilityError, ValueError) as exc:
        raise MediaValidationError('Document is unavailable') from exc


async def read_document(record: DocumentStorageRecord) -> bytes:
    return await _run_thread_joined(read_document_sync, record)


__all__ = [
    'DocumentStorageDestination',
    'DocumentStorageRecord',
    'MAX_DOCUMENT_STORAGE_BYTES',
    'create_document',
    'create_document_sync',
    'delete_document',
    'delete_document_sync',
    'inspect_document',
    'inspect_document_sync',
    'prepare_document_destination',
    'read_document',
    'read_document_sync',
]
