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
    # A real process restart drops its process-local admission counter while
    # the manifest remains durable for the next sweep.
    staged._release_removed_reservations()


def _load_api_main(tmp_path, monkeypatch):
    frontend = tmp_path / 'frontend-dist'
    for leaf in ('css', 'assets', 'webfonts', 'fonts'):
        (frontend / leaf).mkdir(parents=True, exist_ok=True)
    config = __import__('cognitrix.config', fromlist=['config'])
    monkeypatch.setattr(config, 'FRONTEND_BUILD_DIR', frontend)
    return __import__('importlib').import_module('cognitrix.api.main')


def test_staging_limits_are_the_transport_contract():
    assert staging.CHUNK_BYTES == 1024 * 1024
    assert staging.MAX_UPLOAD_FILE_BYTES == 10 * 1024 * 1024
    assert staging.MAX_UPLOAD_TOTAL_BYTES == 25 * 1024 * 1024
    assert staging.MAX_UPLOAD_COUNT == 20


@pytest.mark.skipif(os.name != 'nt', reason='Windows path spelling contract')
def test_lexical_absolute_normalizes_extended_windows_drive_prefix():
    extended = Path('//?/C:/staging/chat-media/batch_123')
    ordinary = Path('C:/staging/chat-media/batch_123')

    assert staging._lexical_absolute(extended) == staging._lexical_absolute(
        ordinary
    )


@pytest.mark.asyncio
async def test_saturated_multipart_rejects_before_sweep_read_or_root_mutation(
    staging_workdir,
    monkeypatch,
):
    baseline = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        baseline,
        raising=False,
    )
    upload = FakeUpload('never-read.txt', b'must-not-read')

    async def unexpected_sweep(*_args, **_kwargs):
        raise AssertionError('saturated admission must precede the sweep')

    monkeypatch.setattr(staging, 'sweep_stale_staging', unexpected_sweep)

    with pytest.raises(HTTPException) as exc:
        await staging.stage_upload_files(
            [upload],
            user_key='user',
            stream_id='saturated-multipart',
        )

    assert exc.value.status_code == 503
    assert upload.read_sizes == []
    assert upload.closed is True
    assert not staging_workdir.exists()


@pytest.mark.asyncio
async def test_saturated_legacy_rejects_before_sweep_or_root_mutation(
    staging_workdir,
    monkeypatch,
):
    baseline = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        baseline,
        raising=False,
    )

    async def unexpected_sweep(*_args, **_kwargs):
        raise AssertionError('saturated admission must precede the sweep')

    monkeypatch.setattr(staging, 'sweep_stale_staging', unexpected_sweep)

    with pytest.raises(HTTPException) as exc:
        await staging.stage_legacy_data_urls(
            [{'name': 'never.txt', 'dataUrl': _text_data_url('never')}],
            user_key='user',
            stream_id='saturated-legacy',
        )

    assert exc.value.status_code == 503
    assert not staging_workdir.exists()


@pytest.mark.asyncio
async def test_concurrent_attachment_admission_never_exceeds_global_unit_cap(
    staging_workdir,
    monkeypatch,
):
    baseline = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        baseline + 4,
        raising=False,
    )
    tasks = [
        staging.stage_legacy_data_urls(
            [{'name': f'{index}.txt', 'dataUrl': _text_data_url(str(index))}],
            user_key='user',
            stream_id=f'concurrent-{index}',
        )
        for index in range(6)
    ]
    results = await __import__('asyncio').gather(*tasks, return_exceptions=True)
    admitted = [
        result for result in results
        if isinstance(result, staging.StagedAttachmentSet)
    ]
    rejected = [result for result in results if isinstance(result, HTTPException)]
    try:
        assert len(admitted) == 2
        assert len(rejected) == 4
        assert all(error.status_code == 503 for error in rejected)
        assert staging._attachment_cleanup_obligation_count() == baseline + 4
    finally:
        cleanup_results = await __import__('asyncio').gather(
            *(item.cleanup() for item in admitted),
            return_exceptions=True,
        )
        for cleanup_result in cleanup_results:
            if isinstance(cleanup_result, BaseException):
                raise cleanup_result
    assert staging._attachment_cleanup_obligation_count() == baseline
    assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []


@pytest.mark.asyncio
async def test_failed_staging_cleanup_retains_one_unit_and_backpressures_new_batches(
    staging_workdir,
    monkeypatch,
):
    baseline = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        baseline + 2,
        raising=False,
    )
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    staged = await staging.stage_upload_files(
        [FakeUpload('retained.txt', b'retained')],
        user_key='user',
        stream_id='retained-cleanup',
    )
    original_remove = staging._remove_staged_batch_capability

    def unavailable_remove(_staged):
        raise OSError('simulated staging storage outage')

    monkeypatch.setattr(
        staging,
        '_remove_staged_batch_capability',
        unavailable_remove,
    )
    try:
        assert staging._attachment_cleanup_obligation_count() == baseline + 2
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.cleanup_staged_attachments(staged)
        assert staging.pending_attachment_cleanup_count() == 1
        assert staging._attachment_cleanup_obligation_count() == baseline + 1

        blocked = FakeUpload('blocked.txt', b'blocked')
        existing = set(staging_workdir.iterdir())
        with pytest.raises(HTTPException) as exc:
            await staging.stage_upload_files(
                [blocked],
                user_key='user',
                stream_id='blocked-by-retained-cleanup',
            )
        assert exc.value.status_code == 503
        assert blocked.read_sizes == []
        assert blocked.closed is True
        assert set(staging_workdir.iterdir()) == existing
    finally:
        monkeypatch.setattr(
            staging,
            '_remove_staged_batch_capability',
            original_remove,
        )
        await staging.retry_pending_attachment_cleanups()
    assert staging.pending_attachment_cleanup_count() == 0
    assert staging._attachment_cleanup_obligation_count() == baseline


@pytest.mark.asyncio
async def test_maintenance_sweeps_periodically_and_retries_pending_until_resolved(
    monkeypatch,
):
    now = 0.0
    delays = []
    sweeps = 0
    document_sweeps = 0
    retries = 0

    def clock():
        return now

    async def wait(delay):
        nonlocal now
        delays.append(delay)
        now += delay

    async def sweep(*_args, **_kwargs):
        nonlocal sweeps
        sweeps += 1
        return 0

    async def retry():
        nonlocal retries
        retries += 1
        return 1

    async def reconcile_documents():
        nonlocal document_sweeps
        document_sweeps += 1
        return 0

    monkeypatch.setattr(staging, 'ATTACHMENT_MAINTENANCE_SWEEP_SECONDS', 3, raising=False)
    monkeypatch.setattr(staging, 'ATTACHMENT_MAINTENANCE_RETRY_INITIAL_SECONDS', 1, raising=False)
    monkeypatch.setattr(staging, 'ATTACHMENT_MAINTENANCE_RETRY_MAX_SECONDS', 4, raising=False)
    monkeypatch.setattr(staging, 'sweep_stale_staging', sweep)
    monkeypatch.setattr(
        staging.document_assets,
        'reconcile_expired',
        reconcile_documents,
    )
    monkeypatch.setattr(staging, 'retry_pending_attachment_cleanups', retry)
    monkeypatch.setattr(staging, 'pending_attachment_cleanup_count', lambda: 1)

    await staging._run_attachment_maintenance(
        wait=wait,
        clock=clock,
        cycles=7,
    )

    assert retries == 7
    assert sweeps >= 2
    assert document_sweeps == sweeps
    assert delays
    assert max(delays) <= 4


@pytest.mark.asyncio
async def test_document_reconciliation_runs_when_staging_sweep_fails(monkeypatch):
    calls = []

    async def fail_staging():
        calls.append('staging')
        raise OSError('staging root unavailable')

    async def reconcile_documents():
        calls.append('documents')
        return 0

    monkeypatch.setattr(staging, 'sweep_stale_staging', fail_staging)
    monkeypatch.setattr(
        staging.document_assets,
        'reconcile_expired',
        reconcile_documents,
    )
    monkeypatch.setattr(staging, 'pending_attachment_cleanup_count', lambda: 0)

    await staging._run_attachment_maintenance(
        wait=lambda _delay: __import__('asyncio').sleep(0),
        clock=lambda: 0.0,
        cycles=1,
    )

    assert calls == ['staging', 'documents']


@pytest.mark.asyncio
async def test_maintenance_uses_one_task_and_joins_shutdown(monkeypatch):
    await staging.stop_attachment_maintenance()
    started = __import__('asyncio').Event()
    cancelled = __import__('asyncio').Event()

    async def worker():
        started.set()
        try:
            await __import__('asyncio').Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(staging, '_run_attachment_maintenance', worker)

    first = staging.start_attachment_maintenance()
    second = staging.start_attachment_maintenance()
    assert first is second
    await __import__('asyncio').wait_for(started.wait(), timeout=1)

    await staging.stop_attachment_maintenance()

    await __import__('asyncio').wait_for(cancelled.wait(), timeout=1)
    assert first.done()
    assert staging._ATTACHMENT_MAINTENANCE_TASK is None


@pytest.mark.asyncio
async def test_existing_pending_retry_does_not_self_wake_maintenance(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    wake_calls = 0

    def wake():
        nonlocal wake_calls
        wake_calls += 1

    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', wake)

    class Cleanup:
        batch_dir = tmp_path / 'self-wake'

        def __init__(self):
            self.fail = True

        async def cleanup(self):
            if self.fail:
                raise OSError('still unavailable')

    retained = Cleanup()
    with pytest.raises(staging.AttachmentCleanupError):
        await staging.cleanup_staged_attachments(retained)
    with pytest.raises(staging.AttachmentCleanupError):
        await staging.cleanup_staged_attachments(retained)

    assert wake_calls == 1
    retained.fail = False
    await staging.cleanup_staged_attachments(retained)


@pytest.mark.asyncio
async def test_maintenance_shutdown_survives_cancel_consumed_by_failed_cleanup(
    tmp_path,
    monkeypatch,
):
    await staging.stop_attachment_maintenance()
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)

    async def no_sweep(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(staging, 'sweep_stale_staging', no_sweep)
    started = __import__('asyncio').Event()
    release = __import__('asyncio').Event()

    class Cleanup:
        batch_dir = tmp_path / 'cancelled-maintenance-cleanup'

        def __init__(self):
            self.block = False
            self.fail = True

        async def cleanup(self):
            if self.block:
                started.set()
                await release.wait()
            if self.fail:
                raise OSError('still unavailable')

    retained = Cleanup()
    with pytest.raises(staging.AttachmentCleanupError):
        await staging.cleanup_staged_attachments(retained)
    retained.block = True
    staging.start_attachment_maintenance()
    await __import__('asyncio').wait_for(started.wait(), timeout=1)
    stopping = __import__('asyncio').create_task(
        staging.stop_attachment_maintenance()
    )
    await __import__('asyncio').sleep(0)
    release.set()
    try:
        await __import__('asyncio').wait_for(stopping, timeout=1)
    finally:
        retained.block = False
        retained.fail = False
        release.set()
        await staging.stop_attachment_maintenance()
        await staging.cleanup_staged_attachments(retained)


@pytest.mark.asyncio
async def test_start_maintenance_replaces_state_from_a_closed_foreign_loop(
    monkeypatch,
):
    await staging.stop_attachment_maintenance()
    started = __import__('asyncio').Event()

    class StaleTask:
        def done(self):
            return False

    class ClosedLoop:
        def is_closed(self):
            return True

        def is_running(self):
            return False

    class ForeignWake:
        def set(self):
            raise AssertionError('must not signal a foreign-loop event')

    async def worker():
        started.set()
        await __import__('asyncio').Event().wait()

    monkeypatch.setattr(staging, '_ATTACHMENT_MAINTENANCE_TASK', StaleTask())
    monkeypatch.setattr(staging, '_ATTACHMENT_MAINTENANCE_WAKE', ForeignWake())
    monkeypatch.setattr(
        staging,
        '_ATTACHMENT_MAINTENANCE_LOOP',
        ClosedLoop(),
        raising=False,
    )
    monkeypatch.setattr(staging, '_run_attachment_maintenance', worker)

    task = staging.start_attachment_maintenance()
    try:
        assert task is not None and not isinstance(task, StaleTask)
        await __import__('asyncio').wait_for(started.wait(), timeout=1)
    finally:
        await staging.stop_attachment_maintenance()


@pytest.mark.asyncio
async def test_lifespan_stops_maintenance_when_scheduler_shutdown_fails(
    tmp_path,
    monkeypatch,
):
    api_main = _load_api_main(tmp_path, monkeypatch)

    scheduler_started = __import__('asyncio').Event()
    scheduler_release = __import__('asyncio').Event()
    maintenance_stopped = False

    async def initialize():
        return None

    async def recover_once():
        return None

    async def recovery_loop(*, interval_seconds):
        await __import__('asyncio').Event().wait()

    async def failing_scheduler():
        scheduler_started.set()
        try:
            await scheduler_release.wait()
        except __import__('asyncio').CancelledError as exc:
            raise RuntimeError('scheduler shutdown failed') from exc

    def start_maintenance():
        return None

    async def stop_maintenance():
        nonlocal maintenance_stopped
        maintenance_stopped = True

    monkeypatch.setattr(api_main, 'initialize_database', initialize)
    monkeypatch.setattr(api_main, 'run_recovery_pass', recover_once)
    monkeypatch.setattr(api_main, 'recovery_loop', recovery_loop)
    monkeypatch.setattr(api_main, 'scheduler_loop', failing_scheduler)
    monkeypatch.setattr(
        api_main,
        'start_attachment_maintenance',
        start_maintenance,
    )
    monkeypatch.setattr(
        api_main,
        'stop_attachment_maintenance',
        stop_maintenance,
    )

    with pytest.raises(RuntimeError, match='scheduler shutdown failed'):
        async with api_main.lifespan(api_main.app):
            await __import__('asyncio').wait_for(
                scheduler_started.wait(),
                timeout=1,
            )

    assert maintenance_stopped is True


@pytest.mark.asyncio
async def test_lifespan_joins_scheduler_when_maintenance_start_fails(
    tmp_path,
    monkeypatch,
):
    api_main = _load_api_main(tmp_path, monkeypatch)
    real_asyncio = __import__('asyncio')
    created = []

    async def scheduler():
        await real_asyncio.Event().wait()

    async def initialize():
        return None

    async def recover_once():
        return None

    async def recovery_loop(*, interval_seconds):
        await real_asyncio.Event().wait()

    def create_task(coroutine):
        task = real_asyncio.create_task(coroutine)
        created.append(task)
        return task

    def fail_maintenance_start():
        raise RuntimeError('maintenance startup failed')

    monkeypatch.setattr(api_main, 'initialize_database', initialize)
    monkeypatch.setattr(api_main, 'run_recovery_pass', recover_once)
    monkeypatch.setattr(api_main, 'recovery_loop', recovery_loop)
    monkeypatch.setattr(api_main, 'scheduler_loop', scheduler)
    monkeypatch.setattr(
        api_main,
        'start_attachment_maintenance',
        fail_maintenance_start,
    )
    monkeypatch.setattr(
        api_main,
        'asyncio',
        __import__('types').SimpleNamespace(
            CancelledError=real_asyncio.CancelledError,
            create_task=create_task,
        ),
    )

    with pytest.raises(RuntimeError, match='maintenance startup failed'):
        async with api_main.lifespan(api_main.app):
            raise AssertionError('lifespan must not yield after startup failure')

    assert len(created) == 2
    assert all(task.cancelled() for task in created)
    assert all(task.done() for task in created)


@pytest.mark.asyncio
async def test_partial_batch_create_cleanup_failure_retains_reserved_obligation(
    staging_workdir,
    monkeypatch,
):
    baseline = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        baseline + 2,
    )
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    original_create = staging._create_pinned_batch
    original_remove = staging._remove_staged_batch_capability

    def fail_after_create(*args, **kwargs):
        original_create(*args, **kwargs)
        raise OSError('simulated create handoff failure')

    def unavailable_remove(_staged):
        raise OSError('simulated cleanup outage')

    monkeypatch.setattr(staging, '_create_pinned_batch', fail_after_create)
    monkeypatch.setattr(staging, '_remove_staged_batch_capability', unavailable_remove)
    upload = FakeUpload('never-read.txt', b'never-read')
    try:
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.stage_upload_files(
                [upload],
                user_key='user',
                stream_id='partial-create-retained',
            )
        assert upload.read_sizes == []
        assert upload.closed is True
        assert staging.pending_attachment_cleanup_count() == 1
        assert staging._attachment_cleanup_obligation_count() == baseline + 1
        assert staging_workdir.exists() and list(staging_workdir.iterdir())
    finally:
        monkeypatch.setattr(staging, '_create_pinned_batch', original_create)
        monkeypatch.setattr(staging, '_remove_staged_batch_capability', original_remove)
        await staging.retry_pending_attachment_cleanups()
    assert staging._attachment_cleanup_obligation_count() == baseline


@pytest.mark.asyncio
async def test_unknown_batch_identity_retains_cleanup_until_retry_capture(
    staging_workdir,
    monkeypatch,
):
    baseline_pending = staging.pending_attachment_cleanup_count()
    baseline_units = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    original_create = staging.secure_fs.DirectoryCapability.create_directory
    original_open = staging.secure_fs.DirectoryCapability.open_directory
    created_path = None
    identity_probe_blocked = True

    def create_then_lose_identity(_capability, leaf):
        nonlocal created_path
        created_path = staging_workdir / leaf
        created_path.mkdir(mode=0o700)
        raise staging.secure_fs.CreatedChildUnknownIdentityError(
            leaf=leaf,
            is_directory=True,
        )

    def block_first_cleanup_identity(capability, leaf, **kwargs):
        if (
            identity_probe_blocked
            and created_path is not None
            and leaf == created_path.name
        ):
            raise staging.secure_fs.CapabilityError()
        return original_open(capability, leaf, **kwargs)

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'create_directory',
        create_then_lose_identity,
    )
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'open_directory',
        block_first_cleanup_identity,
    )
    upload = FakeUpload('never-read.txt', b'never-read')
    try:
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.stage_upload_files(
                [upload],
                user_key='user',
                stream_id='unknown-batch-identity',
            )

        assert upload.read_sizes == []
        assert upload.closed is True
        assert created_path is not None and created_path.is_dir()
        assert staging.pending_attachment_cleanup_count() == baseline_pending + 1
        assert staging._attachment_cleanup_obligation_count() == baseline_units + 1

        identity_probe_blocked = False
        assert await staging.retry_pending_attachment_cleanups() == baseline_pending
        assert not created_path.exists()
        assert staging._attachment_cleanup_obligation_count() == baseline_units
    finally:
        identity_probe_blocked = False
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'create_directory',
            original_create,
        )
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'open_directory',
            original_open,
        )
        await staging.retry_pending_attachment_cleanups()
        if created_path is not None and created_path.exists():
            created_path.rmdir()


@pytest.mark.parametrize(
    ('phase', 'flush_number'),
    [
        ('batch', 1),
        ('manifest', 2),
        ('entry', 3),
    ],
)
@pytest.mark.asyncio
async def test_created_child_parent_flush_failure_leaves_no_staging_orphan(
    staging_workdir,
    monkeypatch,
    phase,
    flush_number,
):
    baseline_pending = staging.pending_attachment_cleanup_count()
    baseline_units = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    original_flush = staging.secure_fs.DirectoryCapability.flush
    flushes = 0

    def fail_selected_parent_flush(capability):
        nonlocal flushes
        flushes += 1
        if flushes == flush_number:
            raise OSError(f'simulated {phase} parent flush failure')
        return original_flush(capability)

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'flush',
        fail_selected_parent_flush,
    )
    try:
        with pytest.raises(Exception):
            await staging.stage_upload_files(
                [FakeUpload('known.txt', b'known')],
                user_key='user',
                stream_id=f'{phase}-flush-rollback',
            )

        assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []
        assert staging.pending_attachment_cleanup_count() == baseline_pending
        assert staging._attachment_cleanup_obligation_count() == baseline_units
    finally:
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'flush',
            original_flush,
        )
        if staging_workdir.exists():
            __import__('shutil').rmtree(staging_workdir)
        staging_workdir.mkdir(parents=True, exist_ok=True)
        await staging.retry_pending_attachment_cleanups()


@pytest.mark.parametrize(
    ('phase', 'flush_number', 'delete_method'),
    [
        ('batch', 1, 'delete_directory'),
        ('manifest', 2, 'delete_file'),
        ('entry', 3, 'delete_file'),
    ],
)
@pytest.mark.asyncio
async def test_unsettled_created_child_rollback_retains_cleanup_obligation(
    staging_workdir,
    monkeypatch,
    phase,
    flush_number,
    delete_method,
):
    baseline_pending = staging.pending_attachment_cleanup_count()
    baseline_units = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    original_flush = staging.secure_fs.DirectoryCapability.flush
    original_delete = getattr(
        staging.secure_fs.DirectoryCapability,
        delete_method,
    )
    flushes = 0
    deletion_blocked = True

    def fail_selected_parent_flush(capability):
        nonlocal flushes
        flushes += 1
        if flushes == flush_number:
            raise OSError(f'simulated {phase} parent flush failure')
        return original_flush(capability)

    def block_exact_delete(capability, leaf, *, expected_identity):
        if deletion_blocked:
            raise OSError(f'simulated persistent {phase} rollback failure')
        return original_delete(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'flush',
        fail_selected_parent_flush,
    )
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        delete_method,
        block_exact_delete,
    )
    try:
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.stage_upload_files(
                [FakeUpload('known.txt', b'known')],
                user_key='user',
                stream_id=f'{phase}-flush-retained',
            )

        assert staging_workdir.exists() and list(staging_workdir.iterdir())
        assert staging.pending_attachment_cleanup_count() == baseline_pending + 1
        assert staging._attachment_cleanup_obligation_count() == baseline_units + 1

        deletion_blocked = False
        await staging.retry_pending_attachment_cleanups()

        assert not staging_workdir.exists() or list(staging_workdir.iterdir()) == []
        assert staging.pending_attachment_cleanup_count() == baseline_pending
        assert staging._attachment_cleanup_obligation_count() == baseline_units
    finally:
        deletion_blocked = False
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'flush',
            original_flush,
        )
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            delete_method,
            original_delete,
        )
        if staging_workdir.exists():
            __import__('shutil').rmtree(staging_workdir)
        staging_workdir.mkdir(parents=True, exist_ok=True)
        await staging.retry_pending_attachment_cleanups()


@pytest.mark.asyncio
async def test_distinct_failed_cleanup_objects_never_collide_or_evict(
    tmp_path,
    monkeypatch,
):
    baseline = staging._attachment_cleanup_obligation_count()
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        baseline + 2,
    )
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)

    class FailingCleanup:
        def __init__(self):
            self.batch_dir = tmp_path / 'same-legacy-key'
            self.fail = True

        async def cleanup(self):
            if self.fail:
                raise OSError('blocked')

    first = FailingCleanup()
    second = FailingCleanup()
    try:
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.cleanup_staged_attachments(first)
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.cleanup_staged_attachments(second)
        assert staging.pending_attachment_cleanup_count() == 2
        assert staging._attachment_cleanup_obligation_count() == baseline + 2
    finally:
        first.fail = second.fail = False
        await staging.retry_pending_attachment_cleanups()
        for item in (first, second):
            reservation = getattr(item, '_cleanup_reservation', None)
            if reservation is not None:
                reservation.release_all()
    assert staging._attachment_cleanup_obligation_count() == baseline


@pytest.mark.asyncio
async def test_cleanup_success_only_removes_the_same_retained_obligation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)

    class Cleanup:
        batch_dir = tmp_path / 'identity-conditional-pop'

        def __init__(self):
            self.fail = True

        async def cleanup(self):
            if self.fail:
                raise OSError('blocked')

    retained = Cleanup()
    replacement = Cleanup()
    with pytest.raises(staging.AttachmentCleanupError):
        await staging.cleanup_staged_attachments(retained)
    key = staging._staging_cleanup_key(retained)
    retained.fail = False
    with staging._PENDING_CLEANUP_LOCK:
        staging._PENDING_STAGING_CLEANUPS[key] = replacement

    await staging.cleanup_staged_attachments(retained)

    with staging._PENDING_CLEANUP_LOCK:
        assert staging._PENDING_STAGING_CLEANUPS.get(key) is replacement
        staging._PENDING_STAGING_CLEANUPS.pop(key, None)


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
    baseline = staging._attachment_cleanup_obligation_count()
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
    assert staging._attachment_cleanup_obligation_count() == baseline


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


def test_unwritten_recovery_creation_uses_cleanup_identity() -> None:
    file_identity = staging.secure_fs.FileIdentity(1, b'j' * 16, False)
    root_identity = staging.secure_fs.FileIdentity(1, b'r' * 16, True)
    batch_identity = staging.secure_fs.FileIdentity(1, b'b' * 16, True)
    manifest_identity = staging.secure_fs.FileIdentity(1, b'm' * 16, False)
    batch_leaf = 'batch_123'
    recovery_leaf = staging._recovery_leaf(batch_leaf)

    class RootCapability:
        def __init__(self) -> None:
            self.deleted: list[tuple[str, object]] = []

        def create_file(self, leaf):
            assert leaf == recovery_leaf
            raise staging.secure_fs.CreatedChildCleanupError(
                leaf=leaf,
                identity=file_identity,
                is_directory=False,
            )

        def delete_file(self, leaf, *, expected_identity):
            self.deleted.append((leaf, expected_identity))

        def flush(self):
            pass

    root = RootCapability()
    inspection = staging._SweepInspection(
        root_identity=root_identity,
        batch_identity=batch_identity,
        manifest_identity=manifest_identity,
        manifest_bytes=b'manifest\n',
        manifest=staging._ParsedManifest(1.0, 2.0, {}),
    )

    with pytest.raises(staging.secure_fs.CreatedChildCleanupError):
        staging._create_recovery_journal(root, batch_leaf, inspection, [])

    assert root.deleted == [(recovery_leaf, file_identity)]


def test_unwritten_recovery_cleanup_failure_retains_exact_identity() -> None:
    file_identity = staging.secure_fs.FileIdentity(1, b'j' * 16, False)
    batch_leaf = 'batch_123'
    recovery_leaf = staging._recovery_leaf(batch_leaf)

    class RootCapability:
        def create_file(self, leaf):
            raise staging.secure_fs.CreatedChildCleanupError(
                leaf=leaf,
                identity=file_identity,
                is_directory=False,
            )

        def delete_file(self, _leaf, *, expected_identity):
            assert expected_identity == file_identity
            raise FileNotFoundError(recovery_leaf)

        def flush(self):
            raise OSError('simulated recovery-directory flush failure')

    inspection = staging._SweepInspection(
        root_identity=staging.secure_fs.FileIdentity(1, b'r' * 16, True),
        batch_identity=staging.secure_fs.FileIdentity(1, b'b' * 16, True),
        manifest_identity=staging.secure_fs.FileIdentity(1, b'm' * 16, False),
        manifest_bytes=b'manifest\n',
        manifest=staging._ParsedManifest(1.0, 2.0, {}),
    )

    with pytest.raises(staging.secure_fs.CreatedChildCleanupError) as exc:
        staging._create_recovery_journal(
            RootCapability(),
            batch_leaf,
            inspection,
            [],
        )

    assert exc.value.leaf == recovery_leaf
    assert exc.value.identity == file_identity
    assert exc.value.is_directory is False


def test_restart_removes_empty_quarantine_from_unwritten_recovery_journal(
    tmp_path,
    monkeypatch,
) -> None:
    batch_leaf = 'batch_123'
    (tmp_path / batch_leaf).mkdir()
    root_identity = staging.secure_fs.FileIdentity(1, b'r' * 16, True)
    batch_identity = staging.secure_fs.FileIdentity(1, b'b' * 16, True)
    manifest_identity = staging.secure_fs.FileIdentity(1, b'm' * 16, False)
    journal_identity = staging.secure_fs.FileIdentity(1, b'j' * 16, False)
    manifest_bytes = staging._manifest_line({
        'created_at': 1.0,
        'expires_at': 2.0,
        'version': 1,
    })
    inspection = staging._SweepInspection(
        root_identity=root_identity,
        batch_identity=batch_identity,
        manifest_identity=manifest_identity,
        manifest_bytes=manifest_bytes,
        manifest=staging._ParsedManifest(1.0, 2.0, {}),
    )
    recovery_leaf = staging._recovery_leaf(batch_leaf)
    quarantine_leaf = staging.secure_fs._posix_quarantine_leaf(
        recovery_leaf,
        journal_identity,
        directory=False,
    )

    class Capability:
        def __init__(self, identity, payload=None) -> None:
            self.identity = identity
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read_bytes(self, *, max_bytes):
            assert max_bytes == staging.STAGING_RECOVERY_MAX_BYTES
            return self.payload

        def refresh_identity(self):
            return self.identity

    class RootCapability(Capability):
        def __init__(self) -> None:
            super().__init__(root_identity)
            self.deleted: list[tuple[str, object]] = []
            self.flushes = 0

        def open_file(self, leaf):
            assert leaf == quarantine_leaf
            return Capability(journal_identity, b'')

        def open_directory(self, leaf, *, expected_identity=None):
            assert leaf == batch_leaf
            assert expected_identity == batch_identity
            return Capability(batch_identity)

        def delete_file(self, leaf, *, expected_identity):
            self.deleted.append((leaf, expected_identity))

        def flush(self):
            self.flushes += 1

    root = RootCapability()
    monkeypatch.setattr(staging.secure_fs, 'open_root', lambda _path: root)
    monkeypatch.setattr(
        staging,
        '_inspect_manifest_batch',
        lambda inspected_root, leaf: (
            inspection
            if inspected_root == tmp_path and leaf == batch_leaf
            else None
        ),
    )
    monkeypatch.setattr(
        staging,
        '_recovery_deletions_for_inspection',
        lambda root_capability, leaf, inspected: (
            []
            if (
                root_capability is root
                and leaf == batch_leaf
                and inspected is inspection
            )
            else None
        ),
    )

    assert staging._remove_quarantined_recovery_journal(
        tmp_path,
        quarantine_leaf,
    ) is True
    assert root.deleted == [(recovery_leaf, journal_identity)]
    assert root.flushes == 1


def test_restart_removes_nonempty_strict_prefix_quarantined_recovery_journal(
    tmp_path,
    monkeypatch,
) -> None:
    batch_leaf = 'batch_123'
    (tmp_path / batch_leaf).mkdir()
    root_identity = staging.secure_fs.FileIdentity(1, b'r' * 16, True)
    batch_identity = staging.secure_fs.FileIdentity(1, b'b' * 16, True)
    manifest_identity = staging.secure_fs.FileIdentity(1, b'm' * 16, False)
    child_identity = staging.secure_fs.FileIdentity(1, b'c' * 16, False)
    journal_identity = staging.secure_fs.FileIdentity(1, b'j' * 16, False)
    manifest_bytes = staging._manifest_line({
        'created_at': 1.0,
        'expires_at': 2.0,
        'version': 1,
    })
    inspection = staging._SweepInspection(
        root_identity=root_identity,
        batch_identity=batch_identity,
        manifest_identity=manifest_identity,
        manifest_bytes=manifest_bytes,
        manifest=staging._ParsedManifest(1.0, 2.0, {}),
    )
    deletions = [('child_123', child_identity)]
    expected = staging._recovery_journal_bytes(
        batch_leaf,
        inspection,
        deletions,
    )
    journal_prefix = expected[: max(1, len(expected) // 2)]
    assert journal_prefix and expected.startswith(journal_prefix)
    assert journal_prefix != expected
    recovery_leaf = staging._recovery_leaf(batch_leaf)
    quarantine_leaf = staging.secure_fs._posix_quarantine_leaf(
        recovery_leaf,
        journal_identity,
        directory=False,
    )

    class Capability:
        def __init__(self, identity, payload=None) -> None:
            self.identity = identity
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read_bytes(self, *, max_bytes):
            assert max_bytes == staging.STAGING_RECOVERY_MAX_BYTES
            return self.payload

        def refresh_identity(self):
            return self.identity

    class RootCapability(Capability):
        def __init__(self) -> None:
            super().__init__(root_identity)
            self.payload = journal_prefix
            self.deleted: list[tuple[str, object]] = []
            self.flushes = 0

        def open_file(self, leaf):
            assert leaf == quarantine_leaf
            return Capability(journal_identity, self.payload)

        def open_directory(self, leaf, *, expected_identity=None):
            assert leaf == batch_leaf
            assert expected_identity == batch_identity
            return Capability(batch_identity)

        def delete_file(self, leaf, *, expected_identity):
            self.deleted.append((leaf, expected_identity))

        def flush(self):
            self.flushes += 1

    root = RootCapability()
    monkeypatch.setattr(staging.secure_fs, 'open_root', lambda _path: root)
    monkeypatch.setattr(
        staging,
        '_inspect_manifest_batch',
        lambda inspected_root, leaf: (
            inspection
            if inspected_root == tmp_path and leaf == batch_leaf
            else None
        ),
    )
    monkeypatch.setattr(
        staging,
        '_recovery_deletions_for_inspection',
        lambda root_capability, leaf, inspected: (
            deletions
            if (
                root_capability is root
                and leaf == batch_leaf
                and inspected is inspection
            )
            else []
        ),
        raising=False,
    )

    assert staging._remove_quarantined_recovery_journal(
        tmp_path,
        quarantine_leaf,
    ) is True
    assert root.deleted == [(recovery_leaf, journal_identity)]
    assert root.flushes == 1

    root.payload = b'not-an-exact-recovery-prefix'
    root.deleted.clear()
    root.flushes = 0
    with pytest.raises(staging.secure_fs.CapabilityError):
        staging._remove_quarantined_recovery_journal(
            tmp_path,
            quarantine_leaf,
        )
    assert root.deleted == []
    assert root.flushes == 0


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
async def test_torn_recovery_journal_is_repaired_before_batch_mutation(
    staging_workdir,
):
    orphan = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='torn-recovery-journal',
    )
    manifest = staging._parse_staging_manifest(
        (orphan.batch_dir / staging.STAGING_MANIFEST_LEAF).read_bytes()
    )
    _forget_live_batch(orphan)
    recovery = staging_workdir / staging._recovery_leaf(orphan.batch_dir.name)
    recovery.write_bytes(b'')

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 1) == 1
    assert not orphan.batch_dir.exists()
    assert not recovery.exists()


@pytest.mark.asyncio
async def test_post_mutation_sweep_failure_stays_recovering_and_resumes_after_restart(
    staging_workdir,
    monkeypatch,
):
    queued = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='recover-after-directory-delete-failure',
    )
    manifest_path = queued.batch_dir / staging.STAGING_MANIFEST_LEAF
    manifest = staging._parse_staging_manifest(manifest_path.read_bytes())
    recovery = staging_workdir / staging._recovery_leaf(queued.batch_dir.name)
    original_delete_directory = (
        staging.secure_fs.DirectoryCapability.delete_directory
    )
    failed_once = False

    def fail_final_batch_delete(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal failed_once
        if leaf == queued.batch_dir.name and not failed_once:
            failed_once = True
            raise OSError('simulated final batch-directory delete failure')
        return original_delete_directory(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_directory',
        fail_final_batch_delete,
    )

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 1) == 0
    lexical = staging._lexical_absolute(queued.batch_dir)
    with staging._ACTIVE_LOCK:
        assert staging._BATCH_LEASES[lexical][0] == 'recovering'
    with pytest.raises(staging.MediaValidationError):
        queued.claim_now()
    assert queued.batch_dir.exists()
    assert not queued.entries[0].path.exists()
    assert not manifest_path.exists()
    assert recovery.exists()

    _forget_live_batch(queued)

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 2) == 1
    assert not queued.batch_dir.exists()
    assert not recovery.exists()


@pytest.mark.asyncio
async def test_restart_proves_quarantined_batch_deleted_before_recovery_journal(
    staging_workdir,
    monkeypatch,
):
    queued = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='quarantined-recovery-batch',
    )
    manifest = staging._parse_staging_manifest(
        (queued.batch_dir / staging.STAGING_MANIFEST_LEAF).read_bytes()
    )
    recovery_leaf = staging._recovery_leaf(queued.batch_dir.name)
    recovery_path = staging_workdir / recovery_leaf
    quarantine_leaf = 'q_recovery_batch'
    quarantine_path = staging_workdir / quarantine_leaf
    original_delete_directory = (
        staging.secure_fs.DirectoryCapability.delete_directory
    )
    original_delete_file = staging.secure_fs.DirectoryCapability.delete_file
    attempts = 0

    def quarantine_then_resume(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal attempts
        if leaf != queued.batch_dir.name:
            return original_delete_directory(
                capability,
                leaf,
                expected_identity=expected_identity,
            )
        attempts += 1
        if attempts == 1:
            queued.batch_dir.rename(quarantine_path)
            raise OSError('simulated post-rename recovery batch delete failure')
        assert not queued.batch_dir.exists()
        assert quarantine_path.is_dir()
        return original_delete_directory(
            capability,
            quarantine_leaf,
            expected_identity=expected_identity,
        )

    def retain_journal_until_batch_is_proven_absent(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        if leaf == recovery_leaf:
            assert not quarantine_path.exists()
        return original_delete_file(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_directory',
        quarantine_then_resume,
    )
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        retain_journal_until_batch_is_proven_absent,
    )
    _forget_live_batch(queued)
    try:
        assert await staging.sweep_stale_staging(
            now=manifest.expires_at + 1
        ) == 0
        assert attempts == 1
        assert not queued.batch_dir.exists()
        assert quarantine_path.is_dir()
        assert recovery_path.is_file()

        _forget_live_batch(queued)
        assert await staging.sweep_stale_staging(
            now=manifest.expires_at + 2
        ) == 0
        assert attempts == 2
        assert not quarantine_path.exists()
        assert not recovery_path.exists()
    finally:
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'delete_directory',
            original_delete_directory,
        )
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'delete_file',
            original_delete_file,
        )
        _forget_live_batch(queued)
        if quarantine_path.exists():
            quarantine_path.rmdir()
        if recovery_path.exists():
            recovery_path.unlink()


@pytest.mark.asyncio
async def test_restart_resumes_existing_journal_before_surviving_manifest(
    staging_workdir,
    monkeypatch,
):
    queued = await staging.stage_upload_files(
        [
            FakeUpload('first.txt', b'first'),
            FakeUpload('second.txt', b'second'),
        ],
        user_key='user',
        stream_id='recover-before-surviving-manifest',
    )
    manifest_path = queued.batch_dir / staging.STAGING_MANIFEST_LEAF
    manifest = staging._parse_staging_manifest(manifest_path.read_bytes())
    first_leaf, second_leaf = [entry.path.name for entry in queued.entries]
    recovery = staging_workdir / staging._recovery_leaf(queued.batch_dir.name)
    original_delete_file = staging.secure_fs.DirectoryCapability.delete_file
    failed_once = False

    def fail_second_child_once(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal failed_once
        if leaf == second_leaf and not failed_once:
            failed_once = True
            raise OSError('simulated restart after first child delete')
        return original_delete_file(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        fail_second_child_once,
    )

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 1) == 0
    assert recovery.exists()
    assert not (queued.batch_dir / first_leaf).exists()
    assert (queued.batch_dir / second_leaf).exists()
    assert manifest_path.exists()

    _forget_live_batch(queued)
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        original_delete_file,
    )

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 2) == 1
    assert not queued.batch_dir.exists()
    assert not recovery.exists()


@pytest.mark.asyncio
async def test_restart_removes_valid_journal_when_pinned_batch_is_absent(
    staging_workdir,
    monkeypatch,
):
    queued = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='journal-only-restart',
    )
    manifest = staging._parse_staging_manifest(
        (queued.batch_dir / staging.STAGING_MANIFEST_LEAF).read_bytes()
    )
    recovery_leaf = staging._recovery_leaf(queued.batch_dir.name)
    recovery = staging_workdir / recovery_leaf
    original_delete_file = staging.secure_fs.DirectoryCapability.delete_file
    failed_once = False

    def fail_recovery_delete_once(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal failed_once
        if leaf == recovery_leaf and not failed_once:
            failed_once = True
            raise OSError('simulated crash before recovery journal deletion')
        return original_delete_file(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        fail_recovery_delete_once,
    )

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 1) == 1
    assert not queued.batch_dir.exists()
    assert recovery.exists()

    _forget_live_batch(queued)
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        original_delete_file,
    )

    assert await staging.sweep_stale_staging(now=manifest.expires_at + 2) == 0
    assert not recovery.exists()


@pytest.mark.parametrize('valid_quarantine_name', [True, False])
@pytest.mark.asyncio
async def test_restart_sweep_resumes_quarantined_recovery_journal(
    staging_workdir,
    monkeypatch,
    valid_quarantine_name,
):
    queued = await staging.stage_upload_files(
        [FakeUpload('known.txt', b'known')],
        user_key='user',
        stream_id='quarantined-recovery-journal',
    )
    manifest = staging._parse_staging_manifest(
        (queued.batch_dir / staging.STAGING_MANIFEST_LEAF).read_bytes()
    )
    recovery_leaf = staging._recovery_leaf(queued.batch_dir.name)
    recovery_path = staging_workdir / recovery_leaf
    original_delete_file = staging.secure_fs.DirectoryCapability.delete_file
    quarantine_path = None
    failed_once = False

    def quarantine_journal_then_resume(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal failed_once, quarantine_path
        if leaf == recovery_leaf and not failed_once:
            quarantine_identity = expected_identity
            if not valid_quarantine_name:
                quarantine_identity = staging.secure_fs.FileIdentity(
                    volume=expected_identity.volume + 1,
                    file_id=expected_identity.file_id,
                    is_directory=False,
                )
            quarantine_leaf = staging.secure_fs._posix_quarantine_leaf(
                recovery_leaf,
                quarantine_identity,
                directory=False,
            )
            quarantine_path = staging_workdir / quarantine_leaf
            recovery_path.rename(quarantine_path)
            failed_once = True
            raise OSError('simulated unrestorable recovery journal delete')
        if (
            leaf == recovery_leaf
            and quarantine_path is not None
            and quarantine_path.exists()
        ):
            return original_delete_file(
                capability,
                quarantine_path.name,
                expected_identity=expected_identity,
            )
        return original_delete_file(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_file',
        quarantine_journal_then_resume,
    )
    _forget_live_batch(queued)
    try:
        assert await staging.sweep_stale_staging(
            now=manifest.expires_at + 1
        ) == 1
        assert not queued.batch_dir.exists()
        assert not recovery_path.exists()
        assert quarantine_path is not None and quarantine_path.is_file()

        _forget_live_batch(queued)
        assert await staging.sweep_stale_staging(
            now=manifest.expires_at + 2
        ) == 0
        assert quarantine_path.exists() is not valid_quarantine_name
    finally:
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'delete_file',
            original_delete_file,
        )
        _forget_live_batch(queued)
        if quarantine_path is not None and quarantine_path.exists():
            quarantine_path.unlink()
        if recovery_path.exists():
            recovery_path.unlink()


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
async def test_quarantined_staged_batch_retry_removes_orphan_before_release(
    staging_workdir,
    monkeypatch,
):
    baseline_pending = staging.pending_attachment_cleanup_count()
    baseline_units = staging._attachment_cleanup_obligation_count()
    staged = await staging.stage_upload_files(
        [FakeUpload('active.txt', b'active')],
        user_key='user',
        stream_id='quarantined-staged-batch',
    )
    quarantine_leaf = 'q_staged_retry'
    quarantine_path = staging_workdir / quarantine_leaf
    original_delete = staging.secure_fs.DirectoryCapability.delete_directory
    attempts = 0

    def quarantine_then_resume(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal attempts
        if leaf != staged.batch_dir.name:
            return original_delete(
                capability,
                leaf,
                expected_identity=expected_identity,
            )
        attempts += 1
        if attempts == 1:
            staged.batch_dir.rename(quarantine_path)
            raise OSError('simulated post-rename staged batch delete failure')
        assert not staged.batch_dir.exists()
        assert quarantine_path.is_dir()
        return original_delete(
            capability,
            quarantine_leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    monkeypatch.setattr(
        staging.secure_fs.DirectoryCapability,
        'delete_directory',
        quarantine_then_resume,
    )
    try:
        with pytest.raises(staging.AttachmentCleanupError):
            await staging.cleanup_staged_attachments(staged)
        assert attempts == 1
        assert quarantine_path.is_dir()
        assert staging.pending_attachment_cleanup_count() == baseline_pending + 1
        assert staging._attachment_cleanup_obligation_count() == baseline_units + 1

        assert await staging.retry_pending_attachment_cleanups() == baseline_pending
        assert attempts == 2
        assert not quarantine_path.exists()
        assert staging._attachment_cleanup_obligation_count() == baseline_units
    finally:
        monkeypatch.setattr(
            staging.secure_fs.DirectoryCapability,
            'delete_directory',
            original_delete,
        )
        if quarantine_path.exists():
            quarantine_path.rmdir()
        await staging.retry_pending_attachment_cleanups()


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
    await batch_a.cleanup()
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
