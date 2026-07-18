"""Pinned-directory filesystem capabilities for untrusted attachment storage.

Only the initial root is addressed by path. Every descendant operation is a
single opaque component resolved relative to an already-open directory handle.

Capabilities are lifetime guards, not just lookup helpers. Keep a file or
directory capability open while Session code consumes it. If a capability must
be closed between phases, reopen it with ``expected_identity`` so a namespace
replacement is rejected before use.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CapabilityError(RuntimeError):
    """A secure filesystem operation could not be proved safe."""

    def __init__(self) -> None:
        super().__init__('Secure filesystem operation failed')


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Filesystem identity captured from an open handle."""

    volume: int
    file_id: bytes
    is_directory: bool


class CreatedChildCleanupError(CapabilityError):
    """A created child remains possible after its parent flush failed."""

    def __init__(
        self,
        *,
        leaf: str,
        identity: FileIdentity,
        is_directory: bool,
    ) -> None:
        super().__init__()
        self.leaf = leaf
        self.identity = identity
        self.is_directory = is_directory


class CreatedChildUnknownIdentityError(CapabilityError):
    """A created child may remain, but no safe identity could be captured."""

    def __init__(self, *, leaf: str, is_directory: bool) -> None:
        super().__init__()
        self.leaf = leaf
        self.is_directory = is_directory


_OPAQUE_COMPONENT = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_DOCUMENT_FILE_COMPONENT = re.compile(
    r'f_[0-9a-f]{32}(?:\.[a-z0-9]{1,16})?\Z'
)
_POSIX_QUARANTINE_PREFIX = 'qv1_'
_POSIX_QUARANTINE_COMPONENT = re.compile(r'qv1_[fd]_[0-9a-f]{64}\Z')
_WINDOWS_RESERVED = {
    'CON',
    'PRN',
    'AUX',
    'NUL',
    *(f'COM{number}' for number in range(1, 10)),
    *(f'LPT{number}' for number in range(1, 10)),
}


class _DocumentFileLeaf(str):
    """Proof that a file leaf passed the stricter document-name grammar."""


def _document_file_leaf(leaf: str) -> _DocumentFileLeaf:
    if not isinstance(leaf, str) or not _DOCUMENT_FILE_COMPONENT.fullmatch(leaf):
        raise ValueError('Document storage leaf is invalid')
    return _DocumentFileLeaf(leaf)


def _validate_leaf(leaf: str) -> str:
    valid_component = bool(_OPAQUE_COMPONENT.fullmatch(leaf)) if isinstance(leaf, str) else False
    if isinstance(leaf, _DocumentFileLeaf):
        valid_component = bool(_DOCUMENT_FILE_COMPONENT.fullmatch(leaf))
    if (
        not isinstance(leaf, str)
        or not valid_component
        or leaf in {'.', '..'}
        or leaf.upper() in _WINDOWS_RESERVED
        or leaf.rstrip(' .') != leaf
        or any(character in leaf for character in ('/', '\\', ':', '\x00'))
    ):
        raise ValueError('Storage leaf must be exactly one safe opaque component')
    return leaf


def _bounded_size(max_bytes: int) -> int:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise ValueError('max_bytes must be a non-negative integer')
    return max_bytes


def _posix_quarantine_leaf(
    leaf: str,
    expected_identity: FileIdentity,
    *,
    directory: bool,
) -> str:
    """Return the stable quarantine name for one logical POSIX child."""

    opaque_leaf = _validate_leaf(leaf)
    if expected_identity.is_directory is not directory:
        raise CapabilityError()
    digest = hashlib.sha256()
    digest.update(b'cognitrix-posix-quarantine-v1\0')
    digest.update(opaque_leaf.encode('ascii'))
    digest.update(b'\0')
    digest.update(str(expected_identity.volume).encode('ascii'))
    digest.update(b'\0')
    digest.update(bytes(expected_identity.file_id))
    digest.update(b'\0directory' if directory else b'\0file')
    kind = 'd' if directory else 'f'
    return _validate_leaf(
        f'{_POSIX_QUARANTINE_PREFIX}{kind}_{digest.hexdigest()}'
    )


def _is_posix_quarantine_leaf(
    leaf: object,
    *,
    directory: bool | None = None,
) -> bool:
    """Return whether ``leaf`` has the versioned production quarantine shape."""

    if not isinstance(leaf, str) or _POSIX_QUARANTINE_COMPONENT.fullmatch(leaf) is None:
        return False
    if directory is None:
        return True
    kind = 'd' if directory else 'f'
    return leaf.startswith(f'{_POSIX_QUARANTINE_PREFIX}{kind}_')


class _Capability:
    def __init__(self, backend: Any, handle: Any, identity: FileIdentity) -> None:
        self._backend = backend
        self._handle = handle
        self.identity = identity

    def _open_handle(self) -> Any:
        if self._handle is None:
            raise CapabilityError()
        return self._handle

    def refresh_identity(self) -> FileIdentity:
        return self._backend.identity(
            self._open_handle(),
            directory=self.identity.is_directory,
        )

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._backend.close(handle)

    def __enter__(self):
        self._open_handle()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class _FileCapability(_Capability):
    """Capability pinning one regular file until the context is closed."""

    def read_bytes(self, *, max_bytes: int) -> bytes:
        return self._backend.read_bounded(
            self._open_handle(),
            _bounded_size(max_bytes),
        )

    def write_bytes(self, data: bytes, *, max_bytes: int) -> None:
        limit = _bounded_size(max_bytes)
        try:
            payload = bytes(data)
        except (TypeError, ValueError) as exc:
            raise TypeError('data must be bytes-like') from exc
        if len(payload) > limit:
            raise CapabilityError()
        self._backend.write_bounded(self._open_handle(), payload, limit)
        self.flush()

    def flush(self) -> None:
        self._backend.flush(self._open_handle())
        if self.refresh_identity() != self.identity:
            raise CapabilityError()


class _DirectoryCapability(_Capability):
    """Capability pinning one directory until the context is closed."""

    def flush(self) -> None:
        self._backend.flush_directory(self._open_handle())
        if self.refresh_identity() != self.identity:
            raise CapabilityError()

    def _open_child(
        self,
        leaf: str,
        *,
        directory: bool,
        create: bool,
        writable: bool,
        expected_identity: FileIdentity | None = None,
    ):
        opaque_leaf = _validate_leaf(leaf)
        handle = self._backend.open_child(
            self._open_handle(),
            opaque_leaf,
            directory=directory,
            create=create,
            writable=writable,
        )
        factory = _directory_from_opened if directory else _file_from_opened
        capability = factory(self._backend, handle)
        if expected_identity is not None and capability.identity != expected_identity:
            capability.close()
            raise CapabilityError()
        return capability

    def open_directory(
        self,
        leaf: str,
        *,
        expected_identity: FileIdentity | None = None,
    ) -> _DirectoryCapability:
        return self._open_child(
            leaf,
            directory=True,
            create=False,
            writable=False,
            expected_identity=expected_identity,
        )

    def create_directory(self, leaf: str) -> _DirectoryCapability:
        return self._create_child(
            leaf,
            directory=True,
        )

    def open_file(
        self,
        leaf: str,
        *,
        expected_identity: FileIdentity | None = None,
    ) -> _FileCapability:
        return self._open_child(
            leaf,
            directory=False,
            create=False,
            writable=False,
            expected_identity=expected_identity,
        )

    def open_document_file(
        self,
        leaf: str,
        *,
        expected_identity: FileIdentity | None = None,
    ) -> _FileCapability:
        return self._open_child(
            _document_file_leaf(leaf),
            directory=False,
            create=False,
            writable=False,
            expected_identity=expected_identity,
        )

    def create_file(self, leaf: str) -> _FileCapability:
        return self._create_child(
            leaf,
            directory=False,
        )

    def create_document_file(self, leaf: str) -> _FileCapability:
        return self._create_child(
            _document_file_leaf(leaf),
            directory=False,
        )

    def _create_child(self, leaf: str, *, directory: bool):
        opaque_leaf = _validate_leaf(leaf)
        capability = self._open_child(
            opaque_leaf,
            directory=directory,
            create=True,
            writable=True,
        )
        try:
            self.flush()
        except BaseException:
            identity = capability.identity
            cleanup_errors: list[BaseException] = []
            try:
                capability.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                if directory:
                    self.delete_directory(
                        opaque_leaf,
                        expected_identity=identity,
                    )
                else:
                    self.delete_file(
                        opaque_leaf,
                        expected_identity=identity,
                    )
            except FileNotFoundError:
                pass
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                self.flush()
            except BaseException as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                raise CreatedChildCleanupError(
                    leaf=opaque_leaf,
                    identity=identity,
                    is_directory=directory,
                ) from cleanup_errors[-1]
            raise
        return capability

    def delete_file(self, leaf: str, *, expected_identity: FileIdentity) -> None:
        if expected_identity.is_directory:
            raise CapabilityError()
        self._backend.delete_child(
            self._open_handle(),
            _validate_leaf(leaf),
            expected_identity=expected_identity,
            directory=False,
        )

    def delete_document_file(
        self,
        leaf: str,
        *,
        expected_identity: FileIdentity,
    ) -> None:
        self.delete_file(
            _document_file_leaf(leaf),
            expected_identity=expected_identity,
        )

    def delete_directory(
        self,
        leaf: str,
        *,
        expected_identity: FileIdentity,
    ) -> None:
        if not expected_identity.is_directory:
            raise CapabilityError()
        self._backend.delete_child(
            self._open_handle(),
            _validate_leaf(leaf),
            expected_identity=expected_identity,
            directory=True,
        )


FileCapability = _FileCapability
DirectoryCapability = _DirectoryCapability


def _file_from_opened(backend: Any, handle: Any) -> _FileCapability:
    try:
        identity = backend.identity(handle, directory=False)
        if identity.is_directory:
            raise CapabilityError()
        return _FileCapability(backend, handle, identity)
    except BaseException:
        backend.close(handle)
        raise


def _directory_from_opened(backend: Any, handle: Any) -> _DirectoryCapability:
    try:
        identity = backend.identity(handle, directory=True)
        if not identity.is_directory:
            raise CapabilityError()
        return _DirectoryCapability(backend, handle, identity)
    except BaseException:
        backend.close(handle)
        raise


# Native NT constants are intentionally module-level so contract tests can
# prove the relative-open security flags without creating privileged links.
OBJ_CASE_INSENSITIVE = 0x00000040
OBJ_DONT_REPARSE = 0x00001000

FILE_READ_DATA = 0x00000001
FILE_LIST_DIRECTORY = FILE_READ_DATA
FILE_WRITE_DATA = 0x00000002
FILE_ADD_FILE = FILE_WRITE_DATA
FILE_ADD_SUBDIRECTORY = 0x00000004
FILE_TRAVERSE = 0x00000020
FILE_READ_ATTRIBUTES = 0x00000080
DELETE = 0x00010000
SYNCHRONIZE = 0x00100000

FILE_OPEN = 0x00000001
FILE_CREATE = 0x00000002
FILE_DIRECTORY_FILE = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_NON_DIRECTORY_FILE = 0x00000040

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_FILE_ID_INFO_CLASS = 18
_FILE_STANDARD_INFO_CLASS = 1
_FILE_DISPOSITION_INFO_CLASS = 4


class _WindowsBackend:
    """Handle-relative Windows backend with an injectable native API seam."""

    def __init__(self, api: Any) -> None:
        self._api = api

    def _accept_opened(self, handle: int) -> int:
        try:
            if self._api.is_reparse(handle):
                raise CapabilityError()
            return handle
        except BaseException:
            self._api.close(handle)
            raise

    def open_root(self, path: str) -> int:
        return self._accept_opened(
            self._api.open_root(
                path,
                share_access=FILE_SHARE_READ | FILE_SHARE_WRITE,
            )
        )

    def open_child(
        self,
        parent_handle: int,
        leaf: str,
        *,
        directory: bool,
        create: bool,
        writable: bool,
    ) -> int:
        _validate_leaf(leaf)
        desired_access = FILE_READ_ATTRIBUTES | DELETE | SYNCHRONIZE
        if directory:
            desired_access |= (
                FILE_LIST_DIRECTORY
                | FILE_ADD_FILE
                | FILE_ADD_SUBDIRECTORY
                | FILE_TRAVERSE
            )
            create_options = FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT
        else:
            desired_access |= FILE_READ_DATA
            if writable:
                desired_access |= FILE_WRITE_DATA
            create_options = FILE_NON_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT
        share_access = (
            FILE_SHARE_READ | FILE_SHARE_WRITE if directory else FILE_SHARE_READ
        )
        handle = self._api.nt_create_file(
            parent_handle=parent_handle,
            leaf=leaf,
            object_attributes=OBJ_DONT_REPARSE | OBJ_CASE_INSENSITIVE,
            create_disposition=FILE_CREATE if create else FILE_OPEN,
            create_options=create_options,
            share_access=share_access,
            desired_access=desired_access,
        )
        return self._accept_opened(handle)

    def identity(self, handle: int, *, directory: bool) -> FileIdentity:
        identity = self._api.query_identity(handle, directory=directory)
        if identity.is_directory is not directory:
            raise CapabilityError()
        return identity

    def read_bounded(self, handle: int, max_bytes: int) -> bytes:
        return self._api.read_bounded(handle, max_bytes)

    def write_bounded(self, handle: int, data: bytes, max_bytes: int) -> None:
        if len(data) > max_bytes:
            raise CapabilityError()
        self._api.write_all(handle, data)

    def flush(self, handle: int) -> None:
        self._api.flush(handle)

    def flush_directory(self, handle: int) -> None:
        self._api.flush_directory(handle)

    def close(self, handle: int) -> None:
        self._api.close(handle)

    def delete_child(
        self,
        parent_handle: int,
        leaf: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        handle = self.open_child(
            parent_handle,
            leaf,
            directory=directory,
            create=False,
            writable=False,
        )
        try:
            if self.identity(handle, directory=directory) != expected_identity:
                raise CapabilityError()
            self._api.set_delete_disposition(handle)
        finally:
            self.close(handle)


if os.name == 'nt':
    from ctypes import wintypes

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ('Length', wintypes.USHORT),
            ('MaximumLength', wintypes.USHORT),
            ('Buffer', wintypes.LPWSTR),
        ]


    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ('Length', wintypes.ULONG),
            ('RootDirectory', wintypes.HANDLE),
            ('ObjectName', ctypes.POINTER(_UNICODE_STRING)),
            ('Attributes', wintypes.ULONG),
            ('SecurityDescriptor', wintypes.LPVOID),
            ('SecurityQualityOfService', wintypes.LPVOID),
        ]


    class _IO_STATUS_UNION(ctypes.Union):
        _fields_ = [('Status', wintypes.LONG), ('Pointer', wintypes.LPVOID)]


    class _IO_STATUS_BLOCK(ctypes.Structure):
        _anonymous_ = ('result',)
        _fields_ = [
            ('result', _IO_STATUS_UNION),
            ('Information', ctypes.c_size_t),
        ]


    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [
            ('FileAttributes', wintypes.DWORD),
            ('ReparseTag', wintypes.DWORD),
        ]


    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [('Identifier', ctypes.c_ubyte * 16)]


    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ('VolumeSerialNumber', ctypes.c_ulonglong),
            ('FileId', _FILE_ID_128),
        ]


    class _FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [
            ('AllocationSize', ctypes.c_longlong),
            ('EndOfFile', ctypes.c_longlong),
            ('NumberOfLinks', wintypes.DWORD),
            ('DeletePending', ctypes.c_ubyte),
            ('Directory', ctypes.c_ubyte),
        ]


    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [('DeleteFile', wintypes.BOOL)]


class _WindowsApi:
    """Small ctypes wrapper; every descendant name remains handle-relative."""

    def __init__(self) -> None:
        if os.name != 'nt':
            raise CapabilityError()
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._ntdll = ctypes.WinDLL('ntdll')
        self._nt_flush_buffers_file = getattr(
            self._ntdll,
            'NtFlushBuffersFile',
            None,
        )
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self._kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._kernel32.ReadFile.restype = wintypes.BOOL
        self._kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._kernel32.WriteFile.restype = wintypes.BOOL
        self._kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        self._kernel32.FlushFileBuffers.restype = wintypes.BOOL
        self._kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_OBJECT_ATTRIBUTES),
            ctypes.POINTER(_IO_STATUS_BLOCK),
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
        ]
        self._ntdll.NtCreateFile.restype = wintypes.LONG
        self._ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        self._ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
        if self._nt_flush_buffers_file is not None:
            self._nt_flush_buffers_file.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_IO_STATUS_BLOCK),
            ]
            self._nt_flush_buffers_file.restype = wintypes.LONG

    @staticmethod
    def _handle(value: int) -> wintypes.HANDLE:
        return wintypes.HANDLE(value)

    @staticmethod
    def _handle_value(handle: Any) -> int:
        value = ctypes.cast(handle, ctypes.c_void_p).value
        if value is None:
            raise CapabilityError()
        return int(value)

    def _last_error(self) -> CapabilityError:
        return CapabilityError()

    def _raise_ntstatus(self, status: int) -> None:
        error = int(self._ntdll.RtlNtStatusToDosError(status))
        if error in {80, 183}:
            raise FileExistsError(error, 'Storage component already exists')
        if error in {2, 3}:
            raise FileNotFoundError(error, 'Storage component does not exist')
        raise CapabilityError()

    def open_root(self, path: str, *, share_access: int) -> int:
        root = Path(path)
        if not root.is_absolute():
            raise ValueError('Capability root must be absolute')
        desired_access = (
            FILE_LIST_DIRECTORY
            | FILE_ADD_FILE
            | FILE_ADD_SUBDIRECTORY
            | FILE_TRAVERSE
            | FILE_READ_ATTRIBUTES
            | SYNCHRONIZE
        )
        handle = self._kernel32.CreateFileW(
            os.fspath(root),
            desired_access,
            share_access,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle is None or self._handle_value(handle) == invalid:
            raise self._last_error()
        return self._handle_value(handle)

    def nt_create_file(
        self,
        *,
        parent_handle: int,
        leaf: str,
        object_attributes: int,
        create_disposition: int,
        create_options: int,
        share_access: int,
        desired_access: int,
    ) -> int:
        _validate_leaf(leaf)
        name_buffer = ctypes.create_unicode_buffer(leaf)
        name = _UNICODE_STRING(
            len(leaf) * 2,
            (len(leaf) + 1) * 2,
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES),
            self._handle(parent_handle),
            ctypes.pointer(name),
            object_attributes,
            None,
            None,
        )
        result = wintypes.HANDLE()
        io_status = _IO_STATUS_BLOCK()
        status = int(
            self._ntdll.NtCreateFile(
                ctypes.byref(result),
                desired_access,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                _FILE_ATTRIBUTE_NORMAL,
                share_access,
                create_disposition,
                create_options,
                None,
                0,
            )
        )
        if status < 0:
            self._raise_ntstatus(status)
        return self._handle_value(result)

    def _query(self, handle: int, info_class: int, value: Any) -> None:
        if not self._kernel32.GetFileInformationByHandleEx(
            self._handle(handle),
            info_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            raise self._last_error()

    def is_reparse(self, handle: int) -> bool:
        attributes = _FILE_ATTRIBUTE_TAG_INFO()
        self._query(handle, _FILE_ATTRIBUTE_TAG_INFO_CLASS, attributes)
        return bool(attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT)

    def query_identity(self, handle: int, *, directory: bool) -> FileIdentity:
        attributes = _FILE_ATTRIBUTE_TAG_INFO()
        identity = _FILE_ID_INFO()
        standard = _FILE_STANDARD_INFO()
        self._query(handle, _FILE_ATTRIBUTE_TAG_INFO_CLASS, attributes)
        if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise CapabilityError()
        self._query(handle, _FILE_ID_INFO_CLASS, identity)
        self._query(handle, _FILE_STANDARD_INFO_CLASS, standard)
        is_directory = bool(standard.Directory)
        if is_directory is not directory:
            raise CapabilityError()
        if not is_directory and int(standard.NumberOfLinks) != 1:
            raise CapabilityError()
        return FileIdentity(
            int(identity.VolumeSerialNumber),
            bytes(identity.FileId.Identifier),
            is_directory,
        )

    def read_bounded(self, handle: int, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            requested = min(64 * 1024, remaining)
            buffer = ctypes.create_string_buffer(requested)
            received = wintypes.DWORD()
            if not self._kernel32.ReadFile(
                self._handle(handle),
                buffer,
                requested,
                ctypes.byref(received),
                None,
            ):
                raise self._last_error()
            count = int(received.value)
            if count == 0:
                break
            chunks.append(buffer.raw[:count])
            remaining -= count
        data = b''.join(chunks)
        if len(data) > max_bytes:
            raise CapabilityError()
        return data

    def write_all(self, handle: int, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + 64 * 1024]
            buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            if not self._kernel32.WriteFile(
                self._handle(handle),
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            ):
                raise self._last_error()
            count = int(written.value)
            if count <= 0:
                raise CapabilityError()
            offset += count

    def flush(self, handle: int) -> None:
        if not self._kernel32.FlushFileBuffers(self._handle(handle)):
            raise self._last_error()

    def flush_directory(self, handle: int) -> None:
        if self._kernel32.FlushFileBuffers(self._handle(handle)):
            return
        if self._nt_flush_buffers_file is None:
            raise CapabilityError()
        io_status = _IO_STATUS_BLOCK()
        status = int(
            self._nt_flush_buffers_file(
                self._handle(handle),
                ctypes.byref(io_status),
            )
        )
        if status < 0:
            raise CapabilityError()

    def set_delete_disposition(self, handle: int) -> None:
        disposition = _FILE_DISPOSITION_INFO(True)
        if not self._kernel32.SetFileInformationByHandle(
            self._handle(handle),
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise self._last_error()

    def close(self, handle: int) -> None:
        if not self._kernel32.CloseHandle(self._handle(handle)):
            raise self._last_error()


def _posix_identity(metadata: os.stat_result, *, directory: bool) -> FileIdentity:
    is_directory = stat.S_ISDIR(metadata.st_mode)
    is_file = stat.S_ISREG(metadata.st_mode)
    if is_directory is not directory or (not directory and not is_file):
        raise CapabilityError()
    if not directory and int(metadata.st_nlink) != 1:
        raise CapabilityError()
    return FileIdentity(
        int(metadata.st_dev),
        int(metadata.st_ino).to_bytes(16, 'little', signed=False),
        is_directory,
    )


class _PosixApi:
    """Thin dir_fd-only wrapper around the POSIX stdlib primitives."""

    @staticmethod
    def _flag(name: str) -> int:
        value = getattr(os, name, None)
        if value is None:
            raise CapabilityError()
        return int(value)

    @staticmethod
    def _translate(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (FileExistsError, FileNotFoundError):
            raise
        except (NotImplementedError, OSError) as exc:
            raise CapabilityError() from exc

    def open_root(self, path: str) -> int:
        root = Path(path)
        if not root.is_absolute():
            raise ValueError('Capability root must be absolute')
        flags = (
            os.O_RDONLY
            | self._flag('O_DIRECTORY')
            | self._flag('O_NOFOLLOW')
            | getattr(os, 'O_CLOEXEC', 0)
        )
        return self._translate(os.open, os.fspath(root), flags)

    def mkdir_child(self, parent_handle: int, leaf: str) -> None:
        self._translate(os.mkdir, leaf, 0o700, dir_fd=parent_handle)

    def open_child(
        self,
        parent_handle: int,
        leaf: str,
        *,
        directory: bool,
        create: bool,
        writable: bool,
    ) -> int:
        nofollow = self._flag('O_NOFOLLOW')
        if directory:
            flags = (
                os.O_RDONLY
                | self._flag('O_DIRECTORY')
                | nofollow
                | getattr(os, 'O_CLOEXEC', 0)
            )
        else:
            flags = (
                (os.O_RDWR if writable else os.O_RDONLY)
                | nofollow
                | getattr(os, 'O_CLOEXEC', 0)
            )
            if create:
                flags |= os.O_CREAT | self._flag('O_EXCL')
        return self._translate(
            os.open,
            leaf,
            flags,
            0o600,
            dir_fd=parent_handle,
        )

    def query_identity(self, handle: int, *, directory: bool) -> FileIdentity:
        try:
            metadata = os.fstat(handle)
        except OSError as exc:
            raise CapabilityError() from exc
        return _posix_identity(metadata, directory=directory)

    def name_identity(
        self,
        parent_handle: int,
        leaf: str,
        *,
        directory: bool,
    ) -> FileIdentity:
        metadata = self._translate(
            os.stat,
            leaf,
            dir_fd=parent_handle,
            follow_symlinks=False,
        )
        return _posix_identity(metadata, directory=directory)

    def rename_child(
        self,
        parent_handle: int,
        source: str,
        destination: str,
    ) -> None:
        self._translate(
            os.rename,
            source,
            destination,
            src_dir_fd=parent_handle,
            dst_dir_fd=parent_handle,
        )

    def delete_name(
        self,
        parent_handle: int,
        leaf: str,
        *,
        directory: bool,
    ) -> None:
        operation = os.rmdir if directory else os.unlink
        self._translate(operation, leaf, dir_fd=parent_handle)

    def read_bounded(self, handle: int, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        try:
            while remaining:
                chunk = os.read(handle, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError as exc:
            raise CapabilityError() from exc
        data = b''.join(chunks)
        if len(data) > max_bytes:
            raise CapabilityError()
        return data

    def write_bounded(self, handle: int, data: bytes, max_bytes: int) -> None:
        if len(data) > max_bytes:
            raise CapabilityError()
        offset = 0
        try:
            while offset < len(data):
                count = os.write(handle, data[offset : offset + 64 * 1024])
                if count <= 0:
                    raise CapabilityError()
                offset += count
        except OSError as exc:
            raise CapabilityError() from exc

    def flush(self, handle: int) -> None:
        self._translate(os.fsync, handle)

    def flush_directory(self, handle: int) -> None:
        self._translate(os.fsync, handle)

    def close(self, handle: int) -> None:
        self._translate(os.close, handle)


class _PosixBackend:
    """Private-root, dir_fd backend for non-Windows hosts.

    The Python stdlib cannot atomically unlink an already-open descriptor. A
    same-UID process that can mutate the private root can therefore race the
    final verified-name unlink. We confine every mutation with ``dir_fd``, move
    candidates to a unique quarantine name, and refuse every detectable
    identity mismatch. Callers must keep the pinned root private from other
    namespace writers for the capability lifetime. Quarantine names are
    deterministic so an interrupted delete can be resumed from a new backend.
    """

    def __init__(self, api: Any | None = None, *, token_factory=None) -> None:
        self._api = api or _PosixApi()
        self._token_factory = token_factory

    def open_root(self, path: str) -> int:
        return self._api.open_root(path)

    def _quarantine_leaf(
        self,
        leaf: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> str:
        if self._token_factory is not None:
            return _validate_leaf(self._token_factory())
        return _posix_quarantine_leaf(
            leaf,
            expected_identity,
            directory=directory,
        )

    def _name_identity_or_none(
        self,
        parent_handle: int,
        leaf: str,
        *,
        directory: bool,
    ) -> FileIdentity | None:
        try:
            return self._api.name_identity(
                parent_handle,
                leaf,
                directory=directory,
            )
        except FileNotFoundError:
            return None

    def _verify_named_child(
        self,
        parent_handle: int,
        leaf: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        handle = self._api.open_child(
            parent_handle,
            leaf,
            directory=directory,
            create=False,
            writable=False,
        )
        try:
            if self.identity(handle, directory=directory) != expected_identity:
                raise CapabilityError()
            if (
                self._name_identity_or_none(
                    parent_handle,
                    leaf,
                    directory=directory,
                )
                != expected_identity
            ):
                raise CapabilityError()
        finally:
            self.close(handle)

    def _delete_verified_quarantine(
        self,
        parent_handle: int,
        quarantine: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        self._verify_named_child(
            parent_handle,
            quarantine,
            expected_identity=expected_identity,
            directory=directory,
        )
        self._api.delete_name(
            parent_handle,
            quarantine,
            directory=directory,
        )
        self.flush_directory(parent_handle)

    def _return_restored_child_to_quarantine(
        self,
        parent_handle: int,
        leaf: str,
        quarantine: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        if (
            self._name_identity_or_none(
                parent_handle,
                leaf,
                directory=directory,
            )
            != expected_identity
            or self._name_identity_or_none(
                parent_handle,
                quarantine,
                directory=directory,
            )
            is not None
        ):
            return
        self._api.rename_child(parent_handle, leaf, quarantine)
        try:
            self.flush_directory(parent_handle)
        except BaseException:
            pass

    def _restore_quarantine(
        self,
        parent_handle: int,
        leaf: str,
        quarantine: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        quarantine_identity = self._name_identity_or_none(
            parent_handle,
            quarantine,
            directory=directory,
        )
        if quarantine_identity is None:
            return
        if quarantine_identity != expected_identity:
            raise CapabilityError()
        if (
            self._name_identity_or_none(
                parent_handle,
                leaf,
                directory=directory,
            )
            is not None
        ):
            raise CapabilityError()
        self._verify_named_child(
            parent_handle,
            quarantine,
            expected_identity=expected_identity,
            directory=directory,
        )
        if (
            self._name_identity_or_none(
                parent_handle,
                leaf,
                directory=directory,
            )
            is not None
            or self._name_identity_or_none(
                parent_handle,
                quarantine,
                directory=directory,
            )
            != expected_identity
        ):
            raise CapabilityError()
        self._api.rename_child(parent_handle, quarantine, leaf)
        try:
            self.flush_directory(parent_handle)
        except BaseException:
            try:
                self._return_restored_child_to_quarantine(
                    parent_handle,
                    leaf,
                    quarantine,
                    expected_identity=expected_identity,
                    directory=directory,
                )
            except BaseException:
                pass
            raise

    def _quarantine_delete(
        self,
        parent_handle: int,
        leaf: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        _validate_leaf(leaf)
        if expected_identity.is_directory is not directory:
            raise CapabilityError()
        quarantine = self._quarantine_leaf(
            leaf,
            expected_identity=expected_identity,
            directory=directory,
        )
        logical_identity = self._name_identity_or_none(
            parent_handle,
            leaf,
            directory=directory,
        )
        quarantine_identity = self._name_identity_or_none(
            parent_handle,
            quarantine,
            directory=directory,
        )
        if logical_identity not in {None, expected_identity}:
            raise CapabilityError()
        if quarantine_identity not in {None, expected_identity}:
            raise CapabilityError()
        if logical_identity is None:
            if quarantine_identity is None:
                self.flush_directory(parent_handle)
                return
            self._delete_verified_quarantine(
                parent_handle,
                quarantine,
                expected_identity=expected_identity,
                directory=directory,
            )
            return
        if quarantine_identity is not None:
            raise CapabilityError()

        self._verify_named_child(
            parent_handle,
            leaf,
            expected_identity=expected_identity,
            directory=directory,
        )
        if (
            self._name_identity_or_none(
                parent_handle,
                leaf,
                directory=directory,
            )
            != expected_identity
            or self._name_identity_or_none(
                parent_handle,
                quarantine,
                directory=directory,
            )
            is not None
        ):
            raise CapabilityError()
        self._api.rename_child(parent_handle, leaf, quarantine)
        try:
            self.flush_directory(parent_handle)
            self._delete_verified_quarantine(
                parent_handle,
                quarantine,
                expected_identity=expected_identity,
                directory=directory,
            )
        except BaseException:
            try:
                self._restore_quarantine(
                    parent_handle,
                    leaf,
                    quarantine,
                    expected_identity=expected_identity,
                    directory=directory,
                )
            except BaseException:
                pass
            raise

    def _settle_directory_created_before_identity(
        self,
        parent_handle: int,
        leaf: str,
        primary_error: BaseException,
    ) -> None:
        """Remove a post-mkdir child or hand off cleanup ownership."""

        handle = None
        created_identity = None
        try:
            handle = self._api.open_child(
                parent_handle,
                leaf,
                directory=True,
                create=False,
                writable=False,
            )
            created_identity = self.identity(handle, directory=True)
        except BaseException:
            # Identityless namespace mutation is not safe: even a relative
            # rmdir can remove a replacement directory after the mkdir result
            # was lost. Let the caller retain capacity and retry an exact open.
            pass
        finally:
            if handle is not None:
                try:
                    self.close(handle)
                except BaseException:
                    pass

        if created_identity is not None:
            try:
                self._quarantine_delete(
                    parent_handle,
                    leaf,
                    expected_identity=created_identity,
                    directory=True,
                )
            except BaseException as cleanup_error:
                raise CreatedChildCleanupError(
                    leaf=leaf,
                    identity=created_identity,
                    is_directory=True,
                ) from cleanup_error
            raise primary_error

        raise CreatedChildUnknownIdentityError(
            leaf=leaf,
            is_directory=True,
        ) from primary_error

    def open_child(
        self,
        parent_handle: int,
        leaf: str,
        *,
        directory: bool,
        create: bool,
        writable: bool,
    ) -> int:
        _validate_leaf(leaf)
        created_identity = None
        if directory and create:
            self._api.mkdir_child(parent_handle, leaf)
            try:
                created_identity = self._api.name_identity(
                    parent_handle,
                    leaf,
                    directory=True,
                )
            except BaseException as exc:
                self._settle_directory_created_before_identity(
                    parent_handle,
                    leaf,
                    exc,
                )
        handle = None
        try:
            handle = self._api.open_child(
                parent_handle,
                leaf,
                directory=directory,
                create=create and not directory,
                writable=writable,
            )
            if created_identity is not None and (
                self.identity(handle, directory=True) != created_identity
            ):
                raise CapabilityError()
            return handle
        except BaseException:
            cleanup_error = None
            if handle is not None:
                try:
                    self.close(handle)
                except BaseException as exc:
                    cleanup_error = exc
            if created_identity is not None:
                try:
                    self._quarantine_delete(
                        parent_handle,
                        leaf,
                        expected_identity=created_identity,
                        directory=True,
                    )
                except BaseException as exc:
                    cleanup_error = exc
                if cleanup_error is not None:
                    raise CreatedChildCleanupError(
                        leaf=leaf,
                        identity=created_identity,
                        is_directory=True,
                    ) from cleanup_error
            raise

    def identity(self, handle: int, *, directory: bool) -> FileIdentity:
        identity = self._api.query_identity(handle, directory=directory)
        if identity.is_directory is not directory:
            raise CapabilityError()
        return identity

    def read_bounded(self, handle: int, max_bytes: int) -> bytes:
        return self._api.read_bounded(handle, max_bytes)

    def write_bounded(self, handle: int, data: bytes, max_bytes: int) -> None:
        if len(data) > max_bytes:
            raise CapabilityError()
        self._api.write_bounded(handle, data, max_bytes)

    def flush(self, handle: int) -> None:
        self._api.flush(handle)

    def flush_directory(self, handle: int) -> None:
        self._api.flush_directory(handle)

    def close(self, handle: int) -> None:
        self._api.close(handle)

    def delete_child(
        self,
        parent_handle: int,
        leaf: str,
        *,
        expected_identity: FileIdentity,
        directory: bool,
    ) -> None:
        self._quarantine_delete(
            parent_handle,
            leaf,
            expected_identity=expected_identity,
            directory=directory,
        )


_DEFAULT_BACKEND: Any | None = None


def _platform_backend():
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = (
            _WindowsBackend(_WindowsApi()) if os.name == 'nt' else _PosixBackend()
        )
    return _DEFAULT_BACKEND


def open_root(path: str | os.PathLike[str]) -> DirectoryCapability:
    """Pin an existing absolute directory as the root capability."""

    backend = _platform_backend()
    handle = backend.open_root(os.fspath(path))
    return _directory_from_opened(backend, handle)


__all__ = [
    'CapabilityError',
    'CreatedChildCleanupError',
    'CreatedChildUnknownIdentityError',
    'DirectoryCapability',
    'FileCapability',
    'FileIdentity',
    'open_root',
]
