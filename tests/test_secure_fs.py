from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest


def _secure_fs():
    from cognitrix.media import secure_fs

    return secure_fs


def test_normal_create_write_read_and_verified_delete(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        root_identity = root.identity
        assert root.refresh_identity() == root_identity

        with root.create_directory('batch_123') as batch:
            batch_identity = batch.identity
            assert batch.refresh_identity() == batch_identity

            with batch.create_file('asset_123') as output:
                file_identity = output.identity
                output.write_bytes(b'normal document bytes', max_bytes=64)
                output.flush()
                assert output.refresh_identity() == file_identity

            with batch.open_file('asset_123') as source:
                assert source.identity == file_identity
                assert source.read_bytes(max_bytes=64) == b'normal document bytes'

            batch.delete_file('asset_123', expected_identity=file_identity)
            assert not (tmp_path / 'batch_123' / 'asset_123').exists()

        root.delete_directory('batch_123', expected_identity=batch_identity)
        assert not (tmp_path / 'batch_123').exists()


def test_create_is_exclusive_and_does_not_replace_existing_file(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        with root.create_file('asset_123') as first:
            first.write_bytes(b'first', max_bytes=5)
        with pytest.raises(FileExistsError):
            root.create_file('asset_123')
        with root.open_file('asset_123') as existing:
            assert existing.read_bytes(max_bytes=5) == b'first'


@pytest.mark.parametrize(
    'leaf',
    [
        '',
        '.',
        '..',
        'two/parts',
        r'two\parts',
        'drive:name',
        'nul\x00name',
        'trailing.',
        'trailing ',
        'CON',
        'com1',
        'report.pdf',
        'display name',
    ],
)
def test_unsafe_or_nonopaque_leaf_is_rejected(tmp_path: Path, leaf: str) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        with pytest.raises(ValueError, match='opaque component'):
            root.create_file(leaf)


def test_wrong_expected_identity_refuses_deletion(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        with root.create_file('asset_123') as output:
            expected = output.identity
        wrong = replace(expected, file_id=b'\xff' * len(expected.file_id))

        with pytest.raises(secure_fs.CapabilityError):
            root.delete_file('asset_123', expected_identity=wrong)

        assert (tmp_path / 'asset_123').is_file()


def test_bounded_io_rejects_overflow_without_partial_write(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        with root.create_file('asset_123') as output:
            with pytest.raises(secure_fs.CapabilityError):
                output.write_bytes(b'too large', max_bytes=3)
        with root.open_file('asset_123') as source:
            assert source.read_bytes(max_bytes=0) == b''

        with root.create_file('asset_456') as output:
            output.write_bytes(b'four', max_bytes=4)
        with root.open_file('asset_456') as source:
            with pytest.raises(secure_fs.CapabilityError):
                source.read_bytes(max_bytes=3)


def test_close_is_idempotent_and_open_failure_closes_handle_once() -> None:
    secure_fs = _secure_fs()

    class Backend:
        def __init__(self) -> None:
            self.closed: list[int] = []

        def identity(self, handle: int, *, directory: bool):
            raise secure_fs.CapabilityError()

        def close(self, handle: int) -> None:
            self.closed.append(handle)

    backend = Backend()
    with pytest.raises(secure_fs.CapabilityError):
        secure_fs._file_from_opened(backend, 41)
    assert backend.closed == [41]

    identity = secure_fs.FileIdentity(1, b'x' * 16, False)
    capability = secure_fs._FileCapability(backend, 42, identity)
    capability.close()
    capability.close()
    assert backend.closed == [41, 42]


def test_windows_relative_create_contract_uses_pinned_parent_and_no_reparse() -> None:
    secure_fs = _secure_fs()

    class Api:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed: list[int] = []

        def nt_create_file(self, **kwargs):
            self.calls.append(kwargs)
            return 73

        def query_identity(self, handle: int, *, directory: bool):
            return secure_fs.FileIdentity(9, b'i' * 16, directory)

        def is_reparse(self, handle: int) -> bool:
            return False

        def close(self, handle: int) -> None:
            self.closed.append(handle)

    api = Api()
    backend = secure_fs._WindowsBackend(api)
    handle = backend.open_child(
        55,
        'asset_123',
        directory=False,
        create=True,
        writable=True,
    )

    assert handle == 73
    assert api.calls == [
        {
            'parent_handle': 55,
            'leaf': 'asset_123',
            'object_attributes': secure_fs.OBJ_DONT_REPARSE
            | secure_fs.OBJ_CASE_INSENSITIVE,
            'create_disposition': secure_fs.FILE_CREATE,
            'create_options': secure_fs.FILE_NON_DIRECTORY_FILE
            | secure_fs.FILE_SYNCHRONOUS_IO_NONALERT,
            'share_access': secure_fs.FILE_SHARE_READ,
            'desired_access': secure_fs.FILE_READ_DATA
            | secure_fs.FILE_WRITE_DATA
            | secure_fs.FILE_READ_ATTRIBUTES
            | secure_fs.DELETE
            | secure_fs.SYNCHRONIZE,
        }
    ]


def test_windows_directory_open_shares_read_write_but_never_delete() -> None:
    secure_fs = _secure_fs()

    class Api:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def nt_create_file(self, **kwargs):
            self.calls.append(kwargs)
            return 74

        def is_reparse(self, _handle: int) -> bool:
            return False

        def close(self, _handle: int) -> None:
            pass

    api = Api()
    backend = secure_fs._WindowsBackend(api)
    backend.open_child(
        56,
        'batch_123',
        directory=True,
        create=False,
        writable=False,
    )

    assert api.calls[0]['share_access'] == (
        secure_fs.FILE_SHARE_READ | secure_fs.FILE_SHARE_WRITE
    )
    assert not api.calls[0]['share_access'] & secure_fs.FILE_SHARE_DELETE


def test_windows_root_open_never_shares_delete() -> None:
    secure_fs = _secure_fs()

    class Api:
        def __init__(self) -> None:
            self.share_access: int | None = None

        def open_root(self, _path: str, *, share_access: int) -> int:
            self.share_access = share_access
            return 75

        def is_reparse(self, _handle: int) -> bool:
            return False

        def close(self, _handle: int) -> None:
            pass

    api = Api()
    backend = secure_fs._WindowsBackend(api)
    backend.open_root(r'C:\pinned-root')

    assert api.share_access == secure_fs.FILE_SHARE_READ | secure_fs.FILE_SHARE_WRITE
    assert not api.share_access & secure_fs.FILE_SHARE_DELETE


def test_windows_backend_rejects_reparse_and_closes_opened_handle() -> None:
    secure_fs = _secure_fs()

    class Api:
        def __init__(self) -> None:
            self.closed: list[int] = []

        def nt_create_file(self, **_kwargs):
            return 81

        def is_reparse(self, _handle: int) -> bool:
            return True

        def close(self, handle: int) -> None:
            self.closed.append(handle)

    api = Api()
    backend = secure_fs._WindowsBackend(api)

    with pytest.raises(secure_fs.CapabilityError):
        backend.open_child(12, 'asset_123', directory=False, create=False, writable=False)
    assert api.closed == [81]


def test_windows_identity_mismatch_never_requests_disposition() -> None:
    secure_fs = _secure_fs()

    class Api:
        def __init__(self) -> None:
            self.deleted: list[int] = []
            self.closed: list[int] = []

        def nt_create_file(self, **_kwargs):
            return 91

        def is_reparse(self, _handle: int) -> bool:
            return False

        def query_identity(self, _handle: int, *, directory: bool):
            return secure_fs.FileIdentity(2, b'actual'.ljust(16, b'!'), directory)

        def set_delete_disposition(self, handle: int) -> None:
            self.deleted.append(handle)

        def close(self, handle: int) -> None:
            self.closed.append(handle)

    api = Api()
    backend = secure_fs._WindowsBackend(api)
    wrong = secure_fs.FileIdentity(2, b'wrong'.ljust(16, b'!'), False)

    with pytest.raises(secure_fs.CapabilityError):
        backend.delete_child(19, 'asset_123', expected_identity=wrong, directory=False)
    assert api.deleted == []
    assert api.closed == [91]


def test_display_filename_is_never_accepted_as_storage_leaf(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        with pytest.raises(ValueError):
            root.create_file('Customer Invoice.pdf')
        with root.create_file('f_4a86cdda') as output:
            output.write_bytes(b'invoice', max_bytes=7)

    assert not (tmp_path / 'Customer Invoice.pdf').exists()
    assert (tmp_path / 'f_4a86cdda').is_file()


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing contract')
def test_windows_file_capability_blocks_rename_until_closed(tmp_path: Path) -> None:
    secure_fs = _secure_fs()
    source = tmp_path / 'asset_123'
    destination = tmp_path / 'asset_456'

    with secure_fs.open_root(tmp_path) as root:
        with root.create_file('asset_123') as output:
            output.write_bytes(b'pinned', max_bytes=6)
            with pytest.raises(OSError):
                source.rename(destination)
        source.rename(destination)

    assert destination.read_bytes() == b'pinned'


def test_hardlinked_file_is_rejected_when_link_count_is_available(tmp_path: Path) -> None:
    secure_fs = _secure_fs()
    source = tmp_path / 'asset_123'
    link = tmp_path / 'asset_456'
    source.write_bytes(b'linked')
    try:
        os.link(source, link)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f'hard links unavailable: {exc}')

    with secure_fs.open_root(tmp_path) as root:
        with pytest.raises(secure_fs.CapabilityError):
            root.open_file('asset_123')


def test_write_revalidates_identity_after_flush_before_publication() -> None:
    secure_fs = _secure_fs()
    original = secure_fs.FileIdentity(1, b'a' * 16, False)
    replacement = secure_fs.FileIdentity(1, b'b' * 16, False)

    class Backend:
        def __init__(self) -> None:
            self.flushed = False

        def write_bounded(self, _handle, _data, _limit) -> None:
            pass

        def flush(self, _handle) -> None:
            self.flushed = True

        def identity(self, _handle, *, directory: bool):
            assert directory is False
            return replacement

        def close(self, _handle) -> None:
            pass

    backend = Backend()
    capability = secure_fs._FileCapability(backend, 101, original)

    with pytest.raises(secure_fs.CapabilityError):
        capability.write_bytes(b'bytes', max_bytes=5)
    assert backend.flushed is True


def test_identity_checked_reopen_refuses_replacement(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        with root.create_file('asset_123') as output:
            expected = output.identity
            output.write_bytes(b'original', max_bytes=8)
        with root.open_file('asset_123', expected_identity=expected) as reopened:
            assert reopened.read_bytes(max_bytes=8) == b'original'

        wrong = replace(expected, file_id=b'z' * len(expected.file_id))
        with pytest.raises(secure_fs.CapabilityError):
            root.open_file('asset_123', expected_identity=wrong)


def test_posix_quarantine_never_unlinks_swapped_file_replacement() -> None:
    secure_fs = _secure_fs()
    expected = secure_fs.FileIdentity(1, b'e' * 16, False)
    replacement = secure_fs.FileIdentity(1, b'r' * 16, False)

    class Api:
        def __init__(self) -> None:
            self.names = {'asset_123': expected}
            self.handles: dict[int, object] = {}
            self.deleted: list[str] = []
            self.next_handle = 200

        def open_child(self, _parent, leaf, **_kwargs):
            handle = self.next_handle
            self.next_handle += 1
            self.handles[handle] = self.names[leaf]
            return handle

        def query_identity(self, handle, *, directory):
            identity = self.handles[handle]
            assert identity.is_directory is directory
            return identity

        def name_identity(self, _parent, leaf, *, directory):
            identity = self.names[leaf]
            assert identity.is_directory is directory
            return identity

        def rename_child(self, _parent, source, destination):
            self.names[source] = replacement
            self.names[destination] = self.names.pop(source)

        def delete_name(self, _parent, leaf, *, directory):
            self.deleted.append(leaf)
            del self.names[leaf]

        def close(self, handle):
            self.handles.pop(handle, None)

    api = Api()
    backend = secure_fs._PosixBackend(api, token_factory=lambda: 'q_token')

    with pytest.raises(secure_fs.CapabilityError):
        backend.delete_child(
            1,
            'asset_123',
            expected_identity=expected,
            directory=False,
        )
    assert api.deleted == []
    assert replacement in api.names.values()


def test_posix_failed_post_mkdir_open_preserves_swapped_directory() -> None:
    secure_fs = _secure_fs()
    created = secure_fs.FileIdentity(1, b'c' * 16, True)
    replacement = secure_fs.FileIdentity(1, b's' * 16, True)

    class Api:
        def __init__(self) -> None:
            self.names: dict[str, object] = {}
            self.handles: dict[int, object] = {}
            self.deleted: list[str] = []

        def mkdir_child(self, _parent, leaf):
            self.names[leaf] = created

        def name_identity(self, _parent, leaf, *, directory):
            identity = self.names[leaf]
            assert identity.is_directory is directory
            return identity

        def open_child(self, _parent, leaf, **_kwargs):
            self.names[leaf] = replacement
            self.handles[301] = replacement
            return 301

        def query_identity(self, handle, *, directory):
            identity = self.handles[handle]
            assert identity.is_directory is directory
            return identity

        def rename_child(self, _parent, source, destination):
            self.names[destination] = self.names.pop(source)

        def delete_name(self, _parent, leaf, *, directory):
            self.deleted.append(leaf)
            del self.names[leaf]

        def close(self, handle):
            self.handles.pop(handle, None)

    api = Api()
    backend = secure_fs._PosixBackend(api, token_factory=lambda: 'q_token')

    with pytest.raises(secure_fs.CapabilityError):
        backend.open_child(
            1,
            'batch_123',
            directory=True,
            create=True,
            writable=True,
        )
    assert api.deleted == []
    assert api.names['batch_123'] == replacement


def test_directory_flush_delegates_and_revalidates_identity() -> None:
    secure_fs = _secure_fs()
    identity = secure_fs.FileIdentity(1, b'd' * 16, True)

    class Backend:
        def __init__(self) -> None:
            self.events: list[tuple[object, ...]] = []

        def flush_directory(self, handle) -> None:
            self.events.append(('flush_directory', handle))

        def identity(self, handle, *, directory):
            self.events.append(('identity', handle, directory))
            return identity

        def close(self, _handle) -> None:
            pass

    backend = Backend()
    capability = secure_fs._DirectoryCapability(backend, 401, identity)

    capability.flush()

    assert backend.events == [
        ('flush_directory', 401),
        ('identity', 401, True),
    ]


@pytest.mark.parametrize('method_name', ['create_file', 'create_directory'])
def test_child_creation_flushes_parent_before_return(method_name: str) -> None:
    secure_fs = _secure_fs()
    parent_identity = secure_fs.FileIdentity(1, b'p' * 16, True)
    is_directory = method_name == 'create_directory'
    child_identity = secure_fs.FileIdentity(1, b'c' * 16, is_directory)

    class Backend:
        def __init__(self) -> None:
            self.events: list[tuple[object, ...]] = []

        def open_child(self, parent, leaf, **kwargs):
            self.events.append(('open_child', parent, leaf, kwargs))
            return 403

        def identity(self, handle, *, directory):
            self.events.append(('identity', handle, directory))
            return parent_identity if handle == 402 else child_identity

        def flush_directory(self, handle) -> None:
            self.events.append(('flush_directory', handle))

        def close(self, handle) -> None:
            self.events.append(('close', handle))

    backend = Backend()
    parent = secure_fs._DirectoryCapability(backend, 402, parent_identity)

    child = getattr(parent, method_name)('child_123')

    assert backend.events[-2:] == [
        ('flush_directory', 402),
        ('identity', 402, True),
    ]
    child.close()


@pytest.mark.parametrize(
    ('method_name', 'is_directory'),
    [
        ('create_file', False),
        ('create_directory', True),
    ],
)
def test_parent_flush_failure_rolls_back_exact_created_child(
    method_name: str,
    is_directory: bool,
) -> None:
    secure_fs = _secure_fs()
    parent_identity = secure_fs.FileIdentity(1, b'p' * 16, True)
    child_identity = secure_fs.FileIdentity(1, b'c' * 16, is_directory)

    class Backend:
        def __init__(self) -> None:
            self.names = {'child_123': child_identity}
            self.flushes = 0
            self.events: list[tuple[object, ...]] = []

        def open_child(self, parent, leaf, **kwargs):
            self.events.append(('open_child', parent, leaf, kwargs))
            return 502

        def identity(self, handle, *, directory):
            self.events.append(('identity', handle, directory))
            return parent_identity if handle == 501 else child_identity

        def flush_directory(self, handle) -> None:
            self.flushes += 1
            self.events.append(('flush_directory', handle, self.flushes))
            if self.flushes == 1:
                raise OSError('simulated parent flush failure')

        def delete_child(
            self,
            parent,
            leaf,
            *,
            expected_identity,
            directory,
        ) -> None:
            self.events.append((
                'delete_child',
                parent,
                leaf,
                expected_identity,
                directory,
            ))
            assert expected_identity == child_identity
            assert directory is is_directory
            assert self.names[leaf] == expected_identity
            del self.names[leaf]

        def close(self, handle) -> None:
            self.events.append(('close', handle))

    backend = Backend()
    parent = secure_fs._DirectoryCapability(backend, 501, parent_identity)

    with pytest.raises(OSError, match='parent flush failure'):
        getattr(parent, method_name)('child_123')

    assert backend.names == {}
    close_index = backend.events.index(('close', 502))
    delete_index = next(
        index
        for index, event in enumerate(backend.events)
        if event[0] == 'delete_child'
    )
    assert close_index < delete_index
    assert backend.flushes == 2


@pytest.mark.parametrize(
    ('method_name', 'is_directory'),
    [
        ('create_file', False),
        ('create_directory', True),
    ],
)
def test_failed_created_child_rollback_exposes_pinned_identity(
    method_name: str,
    is_directory: bool,
) -> None:
    secure_fs = _secure_fs()
    parent_identity = secure_fs.FileIdentity(1, b'p' * 16, True)
    child_identity = secure_fs.FileIdentity(1, b'c' * 16, is_directory)

    class Backend:
        def open_child(self, _parent, _leaf, **_kwargs):
            return 512

        def identity(self, handle, *, directory):
            return parent_identity if handle == 511 else child_identity

        def flush_directory(self, _handle) -> None:
            raise OSError('simulated parent flush failure')

        def delete_child(self, *_args, **_kwargs) -> None:
            raise OSError('simulated exact rollback failure')

        def close(self, _handle) -> None:
            pass

    parent = secure_fs._DirectoryCapability(Backend(), 511, parent_identity)

    with pytest.raises(secure_fs.CreatedChildCleanupError) as exc:
        getattr(parent, method_name)('child_123')

    assert exc.value.leaf == 'child_123'
    assert exc.value.identity == child_identity
    assert exc.value.is_directory is is_directory


def test_windows_backend_directory_flush_delegates_to_native_api() -> None:
    secure_fs = _secure_fs()

    class Api:
        def __init__(self) -> None:
            self.flushed: list[int] = []

        def flush_directory(self, handle: int) -> None:
            self.flushed.append(handle)

    api = Api()
    backend = secure_fs._WindowsBackend(api)

    backend.flush_directory(404)

    assert api.flushed == [404]


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory flush contract')
def test_windows_normal_pinned_directory_flush_succeeds(tmp_path: Path) -> None:
    secure_fs = _secure_fs()

    with secure_fs.open_root(tmp_path) as root:
        root.flush()
        with root.create_directory('batch_123') as batch:
            batch.flush()
