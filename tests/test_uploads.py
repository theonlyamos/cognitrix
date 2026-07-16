"""Bounded chat attachment staging and legacy image-content resolution."""

from __future__ import annotations

import base64
import io
import os
import threading
import time
from dataclasses import fields
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

import cognitrix.media.staging as staging
from cognitrix.media import StagedAttachment
from cognitrix.providers.base import LLMManager


class FakeUpload:
    def __init__(
        self,
        filename: str,
        data: bytes | None = None,
        *,
        size: int | None = None,
        content_type: str = 'application/octet-stream',
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._offset = 0
        self._remaining = size if size is not None else len(data or b'')
        self.read_sizes: list[int] = []
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self._remaining <= 0:
            return b''
        take = self._remaining if size < 0 else min(size, self._remaining)
        self._remaining -= take
        if self._data is None:
            return b'x' * take
        start = self._offset
        self._offset += take
        return self._data[start:self._offset]

    async def close(self) -> None:
        self.closed = True


class SlowCloseUpload(FakeUpload):
    def __init__(self, filename: str, data: bytes):
        super().__init__(filename, data)
        self.close_started = __import__('asyncio').Event()
        self.close_release = __import__('asyncio').Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.close_release.wait()
        self.closed = True


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (2, 2), (255, 0, 0)).save(buf, format='PNG')
    return buf.getvalue()


def _png_data_url() -> str:
    return 'data:image/png;base64,' + base64.b64encode(_png_bytes()).decode()


def _text_data_url(text: str = 'hello') -> str:
    return 'data:text/plain;base64,' + base64.b64encode(text.encode()).decode()


@pytest.fixture
def staging_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    return tmp_path / 'staging' / 'chat-media'


def _forget_live_batch(staged) -> None:
    """Model a process restart while leaving the durable batch on disk."""
    batch = staging._lexical_absolute(staged.batch_dir)
    with staging._ACTIVE_LOCK:
        staging._ACTIVE_BATCHES.discard(batch)
        staging._BATCH_LEASES.pop(batch, None)
        staging._BATCH_OBJECTS.pop(batch, None)


def test_staging_limits_are_the_transport_contract():
    assert staging.CHUNK_BYTES == 1024 * 1024
    assert staging.MAX_UPLOAD_FILE_BYTES == 10 * 1024 * 1024
    assert staging.MAX_UPLOAD_TOTAL_BYTES == 25 * 1024 * 1024
    assert staging.MAX_UPLOAD_COUNT == 20


@pytest.mark.asyncio
async def test_uploads_stream_in_one_mib_chunks_into_hashed_confined_batch(staging_workdir):
    upload = FakeUpload('../photo.png', size=staging.CHUNK_BYTES + 17, content_type='image/png')

    staged = await staging.stage_upload_files(
        [upload], user_key='alice@example.com', stream_id='browser/raw/id'
    )

    assert upload.read_sizes == [staging.CHUNK_BYTES, staging.CHUNK_BYTES, staging.CHUNK_BYTES]
    assert upload.closed is True
    assert len(staged.entries) == 1
    entry = staged.entries[0]
    assert entry.path.read_bytes() == b'x' * (staging.CHUNK_BYTES + 17)
    assert entry.filename == 'photo.png'
    assert entry.declared_mime == 'image/png'
    assert entry.size_bytes == staging.CHUNK_BYTES + 17
    assert entry.path.resolve().is_relative_to(staging_workdir.resolve())
    assert staged.batch_dir.parent.resolve() == staging_workdir.resolve()
    assert 'alice@example.com' not in staged.batch_dir.name
    assert 'browser' not in staged.batch_dir.name

    await staged.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('uploads', 'file_cap', 'total_cap', 'count_cap'),
    [
        ([FakeUpload('large.bin', size=5)], 4, 20, 20),
        ([FakeUpload('a.bin', size=4), FakeUpload('b.bin', size=4)], 10, 6, 20),
        ([FakeUpload('a.bin', b'a'), FakeUpload('b.bin', b'b'), FakeUpload('c.bin', b'c')], 10, 10, 2),
    ],
)
async def test_upload_limit_failure_removes_all_partial_paths(
    staging_workdir, monkeypatch, uploads, file_cap, total_cap, count_cap
):
    monkeypatch.setattr(staging, 'MAX_UPLOAD_FILE_BYTES', file_cap)
    monkeypatch.setattr(staging, 'MAX_UPLOAD_TOTAL_BYTES', total_cap)
    monkeypatch.setattr(staging, 'MAX_UPLOAD_COUNT', count_cap)
    monkeypatch.setattr(staging, 'CHUNK_BYTES', 2)

    with pytest.raises(HTTPException) as exc:
        await staging.stage_upload_files(uploads, user_key='user', stream_id='stream')

    assert exc.value.status_code == 413
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []
    assert all(upload.closed for upload in uploads)


@pytest.mark.asyncio
async def test_staged_metadata_has_no_upload_bytes_base64_or_data_url(staging_workdir):
    staged = await staging.stage_legacy_data_urls(
        [{'name': '..\\..\\note.txt', 'dataUrl': _text_data_url('hello')}],
        user_key='user',
        stream_id='stream',
    )

    entry = staged.entries[0]
    assert entry.filename == 'note.txt'
    assert entry.path.read_bytes() == b'hello'
    assert {field.name for field in fields(StagedAttachment)} == {
        'path', 'filename', 'declared_mime', 'size_bytes'
    }
    serialized = repr(staged)
    assert 'data:' not in serialized
    assert 'aGVsbG8=' not in serialized
    assert "b'hello'" not in serialized
    assert 'UploadFile' not in serialized
    assert entry.path.resolve().is_relative_to(staging_workdir.resolve())

    await staged.cleanup()


@pytest.mark.asyncio
async def test_legacy_encoded_length_is_rejected_before_decode(staging_workdir, monkeypatch):
    monkeypatch.setattr(staging, 'MAX_UPLOAD_FILE_BYTES', 3)
    calls = 0
    original = staging.base64.b64decode

    def tracked_decode(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(staging.base64, 'b64decode', tracked_decode)

    with pytest.raises(HTTPException) as exc:
        await staging.stage_legacy_data_urls(
            [{'name': 'too-big.bin', 'dataUrl': 'data:application/octet-stream;base64,' + ('A' * 8)}],
            user_key='user',
            stream_id='stream',
        )

    assert exc.value.status_code == 413
    assert calls == 0
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('attachments', 'file_cap', 'total_cap', 'count_cap'),
    [
        (
            [
                {'name': 'a.bin', 'dataUrl': 'data:application/octet-stream;base64,YWFh'},
                {'name': 'b.bin', 'dataUrl': 'data:application/octet-stream;base64,YmJi'},
            ],
            4,
            5,
            20,
        ),
        (
            [
                {'name': 'a.bin', 'dataUrl': 'data:application/octet-stream;base64,YQ=='},
                {'name': 'b.bin', 'dataUrl': 'data:application/octet-stream;base64,Yg=='},
            ],
            4,
            8,
            1,
        ),
    ],
)
async def test_legacy_count_and_total_limits_fail_before_creating_a_batch(
    staging_workdir, monkeypatch, attachments, file_cap, total_cap, count_cap
):
    monkeypatch.setattr(staging, 'MAX_UPLOAD_FILE_BYTES', file_cap)
    monkeypatch.setattr(staging, 'MAX_UPLOAD_TOTAL_BYTES', total_cap)
    monkeypatch.setattr(staging, 'MAX_UPLOAD_COUNT', count_cap)

    with pytest.raises(HTTPException) as exc:
        await staging.stage_legacy_data_urls(
            attachments, user_key='user', stream_id='stream'
        )

    assert exc.value.status_code == 413
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_and_stale_sweep_excludes_active_batches(
    staging_workdir, monkeypatch
):
    active = await staging.stage_upload_files(
        [FakeUpload('active.txt', b'active')], user_key='user', stream_id='stream'
    )
    await active.claim()
    inactive = await staging.stage_upload_files(
        [FakeUpload('old.txt', b'old')], user_key='user', stream_id='restart'
    )
    _forget_live_batch(inactive)

    removed = await staging.sweep_stale_staging(
        now=time.time() + staging.STAGING_LEASE_SECONDS + 1
    )

    assert removed == 1
    assert not inactive.batch_dir.exists()
    assert active.batch_dir.exists()
    await active.cleanup()
    await active.cleanup()
    assert not active.batch_dir.exists()


@pytest.mark.asyncio
async def test_queued_lease_survives_default_sweep_then_expires_for_ttl_cleanup(
    staging_workdir,
):
    queued = await staging.stage_upload_files(
        [FakeUpload('queued.txt', b'queued')], user_key='user', stream_id='stream'
    )
    created = time.time()
    old = created - 7200
    os.utime(queued.batch_dir, (old, old))

    assert await staging.sweep_stale_staging(now=created) == 0
    assert queued.batch_dir.exists()
    assert await staging.sweep_stale_staging(
        now=created + staging.STAGING_LEASE_SECONDS + 1
    ) == 1
    assert not queued.batch_dir.exists()


@pytest.mark.asyncio
async def test_restart_sweep_uses_manifest_expiry_not_directory_mtime(
    staging_workdir,
):
    orphan = await staging.stage_upload_files(
        [FakeUpload('orphan.txt', b'orphan')],
        user_key='user',
        stream_id='restart-orphan',
    )
    manifest_path = orphan.batch_dir / staging.STAGING_MANIFEST_LEAF
    manifest = staging._parse_staging_manifest(manifest_path.read_bytes())
    _forget_live_batch(orphan)
    old = manifest.created_at - 7200
    os.utime(orphan.batch_dir, (old, old))

    assert await staging.sweep_stale_staging(
        now=manifest.expires_at - 1,
        max_age_seconds=0,
    ) == 0
    assert orphan.batch_dir.exists()
    assert await staging.sweep_stale_staging(
        now=manifest.expires_at + 1,
        max_age_seconds=10**9,
    ) == 1
    assert not orphan.batch_dir.exists()


@pytest.mark.asyncio
async def test_restart_sweep_invalid_manifest_fails_closed(staging_workdir):
    orphan = await staging.stage_upload_files(
        [FakeUpload('orphan.txt', b'orphan')],
        user_key='user',
        stream_id='invalid-manifest',
    )
    manifest_path = orphan.batch_dir / staging.STAGING_MANIFEST_LEAF
    manifest_path.write_bytes(b'{invalid json\n')
    _forget_live_batch(orphan)

    assert await staging.sweep_stale_staging(now=time.time() + 10_000) == 0
    assert orphan.batch_dir.exists()
    assert orphan.entries[0].path.read_bytes() == b'orphan'
    __import__('shutil').rmtree(orphan.batch_dir)


@pytest.mark.asyncio
async def test_restart_sweep_missing_manifest_retains_nonempty_batch(
    staging_workdir,
):
    orphan = await staging.stage_upload_files(
        [FakeUpload('orphan.txt', b'orphan')],
        user_key='user',
        stream_id='missing-manifest',
    )
    (orphan.batch_dir / staging.STAGING_MANIFEST_LEAF).unlink()
    _forget_live_batch(orphan)

    assert await staging.sweep_stale_staging(now=time.time() + 10_000) == 0
    assert orphan.entries[0].path.read_bytes() == b'orphan'
    __import__('shutil').rmtree(orphan.batch_dir)


@pytest.mark.asyncio
async def test_restart_sweep_may_remove_empty_unmanifested_batch(
    staging_workdir,
):
    empty = staging_workdir / 'empty_orphan'
    empty.mkdir(parents=True)

    assert await staging.sweep_stale_staging(now=time.time()) == 1
    assert not empty.exists()


@pytest.mark.asyncio
async def test_unexpired_sweep_probe_never_blocks_concurrent_claim(
    staging_workdir,
    monkeypatch,
):
    queued = await staging.stage_upload_files(
        [FakeUpload('queued.txt', b'queued')],
        user_key='user',
        stream_id='claim-during-probe',
    )
    started = threading.Event()
    release = threading.Event()
    original_open_root = staging.secure_fs.open_root
    block_once = True

    def blocking_open_root(path):
        nonlocal block_once
        if block_once and Path(path) == staging_workdir:
            block_once = False
            started.set()
            release.wait(timeout=5)
        return original_open_root(path)

    monkeypatch.setattr(staging.secure_fs, 'open_root', blocking_open_root)
    sweep = __import__('asyncio').create_task(
        staging.sweep_stale_staging(now=time.time())
    )
    assert await __import__('asyncio').to_thread(started.wait, 2)

    queued.claim_now()
    release.set()

    assert await sweep == 0
    assert queued.batch_dir.exists()
    await queued.cleanup()


@pytest.mark.asyncio
async def test_unknown_extra_retains_external_recovery_journal(
    staging_workdir,
):
    orphan = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='unknown-extra',
    )
    manifest_path = orphan.batch_dir / staging.STAGING_MANIFEST_LEAF
    manifest_bytes = manifest_path.read_bytes()
    expires_at = staging._parse_staging_manifest(manifest_bytes).expires_at
    extra = orphan.batch_dir / 'unknown_extra'
    extra.write_bytes(b'unknown')
    _forget_live_batch(orphan)

    assert await staging.sweep_stale_staging(now=expires_at + 1) == 0
    recovery = staging_workdir / staging._recovery_leaf(orphan.batch_dir.name)
    assert recovery.exists()
    journal = recovery.read_bytes()
    assert orphan.batch_dir.name.encode() in journal
    assert manifest_bytes in journal
    assert extra.read_bytes() == b'unknown'

    __import__('shutil').rmtree(orphan.batch_dir)
    recovery.unlink()


@pytest.mark.asyncio
async def test_recovery_journal_directory_flush_precedes_destructive_delete(
    staging_workdir,
    monkeypatch,
):
    orphan = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='journal-ordering',
    )
    manifest = staging._parse_staging_manifest(
        (orphan.batch_dir / staging.STAGING_MANIFEST_LEAF).read_bytes()
    )
    _forget_live_batch(orphan)
    events = []
    original_delete_file = staging.secure_fs.DirectoryCapability.delete_file

    def tracked_flush(capability):
        events.append(('flush-directory', capability.identity))

    def tracked_delete(capability, leaf, *, expected_identity):
        events.append(('delete-file', leaf))
        return original_delete_file(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'flush',
        tracked_flush,
        raising=False,
    )
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        tracked_delete,
    )

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 1) == 1
    first_delete = next(
        index for index, event in enumerate(events)
        if event[0] == 'delete-file'
    )
    assert any(event[0] == 'flush-directory' for event in events[:first_delete])
    assert events[-1][0] == 'flush-directory'


@pytest.mark.asyncio
async def test_claim_can_win_after_expiry_before_sweep_reserves_batch(staging_workdir):
    queued = await staging.stage_upload_files(
        [FakeUpload('queued.txt', b'queued')], user_key='user', stream_id='stream'
    )
    batch = queued.batch_dir.resolve()
    with staging._ACTIVE_LOCK:
        staging._BATCH_LEASES[batch] = ('queued', time.time() - 1)

    await queued.claim()
    old = time.time() - 7200
    os.utime(queued.batch_dir, (old, old))
    assert await staging.sweep_stale_staging(now=time.time()) == 0
    assert queued.batch_dir.exists()
    await queued.cleanup()


@pytest.mark.asyncio
async def test_cleanup_settles_filesystem_removal_before_propagating_cancellation(
    staging_workdir, monkeypatch
):
    staged = await staging.stage_upload_files(
        [FakeUpload('active.txt', b'active')], user_key='user', stream_id='stream'
    )
    started = threading.Event()
    release = threading.Event()
    original = staging._remove_staged_batch_capability

    def blocking_remove(value):
        started.set()
        release.wait(timeout=5)
        original(value)

    monkeypatch.setattr(
        staging, '_remove_staged_batch_capability', blocking_remove
    )
    task = __import__('asyncio').create_task(staged.cleanup())
    assert await __import__('asyncio').to_thread(started.wait, 2)
    task.cancel()
    release.set()

    with pytest.raises(__import__('asyncio').CancelledError):
        await task
    assert not staged.batch_dir.exists()
    assert staged.batch_dir.resolve() not in staging._ACTIVE_BATCHES
    await staged.cleanup()


@pytest.mark.asyncio
async def test_batch_creation_cancellation_removes_the_just_created_directory(
    staging_workdir, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    original = staging._create_pinned_batch

    def blocking_create(*args, **kwargs):
        result = original(*args, **kwargs)
        started.set()
        release.wait(timeout=5)
        return result

    monkeypatch.setattr(staging, '_create_pinned_batch', blocking_create)
    task = __import__('asyncio').create_task(
        staging.stage_upload_files([], user_key='user', stream_id='stream')
    )
    assert await __import__('asyncio').to_thread(started.wait, 2)
    task.cancel()
    release.set()

    with pytest.raises(__import__('asyncio').CancelledError):
        await task
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []
    assert not any(path.parent == staging_workdir.resolve() for path in staging._ACTIVE_BATCHES)


@pytest.mark.asyncio
async def test_destination_open_cancellation_closes_handle_before_batch_cleanup(
    staging_workdir, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    original = staging._create_staged_file_capability

    def blocking_open(*args, **kwargs):
        capability = original(*args, **kwargs)
        started.set()
        release.wait(timeout=5)
        return capability

    monkeypatch.setattr(
        staging, '_create_staged_file_capability', blocking_open
    )
    task = __import__('asyncio').create_task(
        staging.stage_upload_files(
            [FakeUpload('file.txt', b'content')], user_key='user', stream_id='stream'
        )
    )
    assert await __import__('asyncio').to_thread(started.wait, 2)
    task.cancel()
    release.set()

    with pytest.raises(__import__('asyncio').CancelledError):
        await task
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []
    assert not any(path.parent == staging_workdir.resolve() for path in staging._ACTIVE_BATCHES)


@pytest.mark.asyncio
async def test_upload_close_settles_then_propagates_cancellation(staging_workdir):
    upload = SlowCloseUpload('file.txt', b'content')
    task = __import__('asyncio').create_task(
        staging.stage_upload_files([upload], user_key='user', stream_id='stream')
    )
    await __import__('asyncio').wait_for(upload.close_started.wait(), timeout=2)
    task.cancel()
    upload.close_release.set()

    with pytest.raises(__import__('asyncio').CancelledError):
        await task
    assert upload.closed is True
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []
    assert not any(path.parent == staging_workdir.resolve() for path in staging._ACTIVE_BATCHES)


@pytest.mark.asyncio
async def test_cleanup_failure_releases_active_ownership_for_ttl_retry(
    staging_workdir, monkeypatch
):
    staged = await staging.stage_upload_files(
        [FakeUpload('active.txt', b'active')], user_key='user', stream_id='stream'
    )
    original = staging._remove_staged_batch_capability

    def fail_delete(_staged):
        raise PermissionError('busy')

    monkeypatch.setattr(staging, '_remove_staged_batch_capability', fail_delete)
    with pytest.raises(PermissionError, match='busy'):
        await staged.cleanup()
    assert staged.batch_dir.exists()
    assert staged.batch_dir.resolve() not in staging._ACTIVE_BATCHES

    monkeypatch.setattr(staging, '_remove_staged_batch_capability', original)
    await staged.cleanup()
    assert not staged.batch_dir.exists()


@pytest.mark.asyncio
async def test_cleanup_identity_mismatch_retains_replacement_and_other_batch(
    staging_workdir, monkeypatch
):
    batch_a = await staging.stage_upload_files(
        [FakeUpload('a.txt', b'a')], user_key='user', stream_id='stream-a'
    )
    batch_b = await staging.stage_upload_files(
        [FakeUpload('b.txt', b'b')], user_key='user', stream_id='stream-b'
    )
    marker_b = batch_b.entries[0].path

    # Replace A after staging. Capability cleanup must reject the new identity
    # and must never redirect deletion into another live batch.
    __import__('shutil').rmtree(batch_a.batch_dir)
    batch_a.batch_dir.mkdir()
    replacement = batch_a.batch_dir / 'replacement.marker'
    replacement.write_text('retain')

    with pytest.raises(staging.MediaValidationError, match='cleanup failed'):
        await batch_a.cleanup()

    assert replacement.read_text() == 'retain'
    assert marker_b.read_bytes() == b'b'
    assert batch_b.batch_dir in staging._ACTIVE_BATCHES
    assert batch_a.batch_dir not in staging._ACTIVE_BATCHES
    __import__('shutil').rmtree(batch_a.batch_dir)
    await batch_b.cleanup()


def test_decode_data_url_compatibility_helper():
    assert staging._decode_data_url(_text_data_url('hi')) == b'hi'
    assert staging._decode_data_url('not-a-data-url') is None
    assert staging._decode_data_url('') is None


def test_image_url_from_content(tmp_path):
    data_uri = _png_data_url()
    assert LLMManager._image_url_from_content(data_uri) == data_uri

    path = tmp_path / 'x.png'
    Image.new('RGB', (2, 2)).save(path, format='PNG')
    out = LLMManager._image_url_from_content(str(path))
    assert out and out.startswith('data:image/png;base64,')

    assert LLMManager._image_url_from_content(str(tmp_path / 'missing.png')) is None
