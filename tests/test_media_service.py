import asyncio
import io
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from cognitrix.artifacts import Artifact, variant_path
from cognitrix.media import (
    MediaAccessError,
    MediaAssetService,
    MediaOwnership,
    MediaQuotaError,
    MediaValidationError,
    StagedAttachment,
)


def _image_bytes(mode, size, color, image_format, **save_kwargs):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, format=image_format, **save_kwargs)
    return output.getvalue()


@pytest.fixture
def rgb_jpeg_bytes():
    return _image_bytes('RGB', (24, 16), (120, 30, 10), 'JPEG', quality=90)


@pytest.fixture
def rgba_png_bytes():
    return _image_bytes('RGBA', (18, 12), (10, 20, 30, 96), 'PNG')


@pytest.fixture
def exif_rotated_jpeg_bytes():
    image = Image.new('RGB', (12, 20), (40, 80, 120))
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, format='JPEG', exif=exif)
    return output.getvalue()


@pytest.fixture
def wide_jpeg_bytes():
    return _image_bytes('RGB', (2000, 800), (30, 60, 90), 'JPEG', quality=90)


@pytest.fixture
def gif_bytes():
    return _image_bytes('P', (10, 8), 1, 'GIF')


@pytest.fixture
def invalid_bytes():
    return b'not an image'


@pytest.fixture
def artifact_store(monkeypatch, tmp_path):
    from cognitrix import artifacts

    rows = {}
    root = tmp_path / 'artifact-root'

    async def save(row):
        if row.id is None:
            object.__setattr__(row, 'id', str(uuid.uuid4()))
            rows[str(row.id)] = row
        elif str(row.id) in rows:
            rows[str(row.id)] = row
        return row

    async def get(artifact_id):
        return rows.get(str(artifact_id))

    async def find(query):
        return [
            row
            for row in rows.values()
            if all(getattr(row, key) == value for key, value in query.items())
        ]

    async def delete_many(query):
        doomed = [
            artifact_id
            for artifact_id, row in rows.items()
            if all(getattr(row, key) == value for key, value in query.items())
        ]
        for artifact_id in doomed:
            rows.pop(artifact_id)

    monkeypatch.setattr(artifacts, '_root', lambda: root)
    monkeypatch.setattr(Artifact, 'save', save)
    monkeypatch.setattr(Artifact, 'get', get)
    monkeypatch.setattr(Artifact, 'find', find)
    monkeypatch.setattr(Artifact, 'delete_many', delete_many)
    yield rows, root
    assert not root.exists() or not list(root.rglob('*.tmp'))


def _stage(tmp_path, data, *, filename='upload.bin', declared_mime='application/octet-stream'):
    path = tmp_path / f'staged-{uuid.uuid4().hex}'
    path.write_bytes(data)
    return StagedAttachment(
        path=path,
        filename=filename,
        declared_mime=declared_mime,
        size_bytes=len(data),
    )


async def _ingest(service, tmp_path, data, *, filename, declared_mime):
    ownership = MediaOwnership(session_id='session', user_id='user', agent_id='agent')
    ref = await service.ingest_staged_image(
        _stage(tmp_path, data, filename=filename, declared_mime=declared_mime),
        ownership,
    )
    artifact = await Artifact.get(ref.id)
    return artifact


@pytest.mark.asyncio
async def test_ingest_sniffs_content_and_chooses_sanitized_master_format(
    artifact_store, tmp_path, rgb_jpeg_bytes, rgba_png_bytes, gif_bytes
):
    service = MediaAssetService()

    rgba = await _ingest(
        service,
        tmp_path,
        rgba_png_bytes,
        filename='actually-not-a-jpeg.jpg',
        declared_mime='image/jpeg',
    )
    jpeg = await _ingest(
        service,
        tmp_path,
        rgb_jpeg_bytes,
        filename='actually-not-a-png.png',
        declared_mime='image/png',
    )
    gif = await _ingest(
        service,
        tmp_path,
        gif_bytes,
        filename='animation.gif',
        declared_mime='image/gif',
    )

    assert (rgba.mime_type, Path(rgba.filename).suffix) == ('image/png', '.png')
    assert (jpeg.mime_type, Path(jpeg.filename).suffix) == ('image/jpeg', '.jpg')
    assert (gif.mime_type, Path(gif.filename).suffix) == ('image/png', '.png')
    for artifact, expected_format in ((rgba, 'PNG'), (jpeg, 'JPEG'), (gif, 'PNG')):
        with Image.open(variant_path(artifact, 'original')) as image:
            assert image.format == expected_format


@pytest.mark.asyncio
async def test_ingest_applies_exif_orientation_and_strips_metadata_from_every_variant(
    artifact_store, tmp_path, exif_rotated_jpeg_bytes
):
    artifact = await _ingest(
        MediaAssetService(),
        tmp_path,
        exif_rotated_jpeg_bytes,
        filename='camera.jpg',
        declared_mime='image/jpeg',
    )

    assert (artifact.width, artifact.height) == (20, 12)
    for variant in ('original', 'vision', 'thumbnail'):
        with Image.open(variant_path(artifact, variant)) as image:
            assert image.size == (20, 12)
            assert not image.getexif()
            assert 'exif' not in image.info


@pytest.mark.asyncio
async def test_ingest_bounds_vision_and_webp_thumbnail(
    artifact_store, tmp_path, wide_jpeg_bytes
):
    artifact = await _ingest(
        MediaAssetService(),
        tmp_path,
        wide_jpeg_bytes,
        filename='wide.jpg',
        declared_mime='image/jpeg',
    )

    with Image.open(variant_path(artifact, 'original')) as original:
        assert original.size == (2000, 800)
        assert not original.getexif()
    with Image.open(variant_path(artifact, 'vision')) as vision:
        assert max(vision.size) <= 1568
        assert not vision.getexif()
    with Image.open(variant_path(artifact, 'thumbnail')) as thumbnail:
        assert thumbnail.format == 'WEBP'
        assert max(thumbnail.size) <= 384
        assert not thumbnail.getexif()


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_image_without_files_or_row(
    artifact_store, tmp_path, invalid_bytes
):
    rows, root = artifact_store

    with pytest.raises(MediaValidationError, match='valid image'):
        await _ingest(
            MediaAssetService(),
            tmp_path,
            invalid_bytes,
            filename='fake.png',
            declared_mime='image/png',
        )

    assert rows == {}
    assert not root.exists() or not any(root.rglob('*'))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'data',
    [
        pytest.param(b'P6\n1 1\n255\n\x01\x02\x03', id='ppm'),
        pytest.param(b'P5\n1 1\n255\n\x01', id='pnm'),
    ],
)
async def test_classifier_rejects_pillow_recognized_unsupported_pnm_rasters(
    artifact_store, tmp_path, data
):
    rows, root = artifact_store

    with pytest.raises(MediaValidationError, match='Unsupported image format'):
        await MediaAssetService().ingest_staged_image_if_recognized(
            _stage(
                tmp_path,
                data,
                filename='declared-document.txt',
                declared_mime='text/plain',
            ),
            MediaOwnership('session', 'user', 'agent'),
        )

    assert rows == {}
    assert not root.exists() or not any(root.rglob('*'))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'data',
    [
        pytest.param(b'P6', id='truncated-header'),
        pytest.param(b'P6\n1 1\n255\n', id='missing-pixels'),
    ],
)
async def test_classifier_rejects_truncated_recognized_ppm(
    artifact_store, tmp_path, data
):
    rows, root = artifact_store

    with pytest.raises(MediaValidationError):
        await MediaAssetService().ingest_staged_image_if_recognized(
            _stage(tmp_path, data),
            MediaOwnership('session', 'user', 'agent'),
        )

    assert rows == {}
    assert not root.exists() or not any(root.rglob('*'))


@pytest.mark.asyncio
async def test_classifier_returns_none_only_for_non_image_bytes(tmp_path):
    result = await MediaAssetService().ingest_staged_image_if_recognized(
        _stage(
            tmp_path,
            b'arbitrary non-image bytes',
            filename='pretend.png',
            declared_mime='image/png',
        ),
        MediaOwnership('session', 'user', 'agent'),
    )

    assert result is None


@pytest.mark.asyncio
async def test_classifier_keeps_accepting_supported_raster_bytes(
    artifact_store, tmp_path, rgba_png_bytes
):
    rows, _ = artifact_store

    result = await MediaAssetService().ingest_staged_image_if_recognized(
        _stage(
            tmp_path,
            rgba_png_bytes,
            filename='declared-document.txt',
            declared_mime='text/plain',
        ),
        MediaOwnership('session', 'user', 'agent'),
    )

    assert result is not None
    assert rows[result.id].mime_type == 'image/png'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'ownership',
    [
        MediaOwnership(session_id='other', user_id='user', agent_id='agent'),
        MediaOwnership(session_id='session', user_id='other', agent_id='agent'),
        MediaOwnership(session_id='session', user_id='user', agent_id='other'),
    ],
)
async def test_ownership_mismatch_is_typed_and_precedes_file_read(
    monkeypatch, artifact_store, ownership
):
    rows, _ = artifact_store
    artifact = Artifact(
        id='owned',
        session_id='session',
        user_id='user',
        agent_id='agent',
        storage_key='missing/original.png',
        vision_storage_key='missing/vision.png',
        thumbnail_storage_key='missing/thumbnail.webp',
    )
    rows['owned'] = artifact
    reads = []

    def unexpected_read(path):
        reads.append(path)
        raise AssertionError('ownership must be checked before bytes are read')

    monkeypatch.setattr(Path, 'read_bytes', unexpected_read)

    with pytest.raises(MediaAccessError):
        await MediaAssetService().resolve_image('owned', ownership)
    assert reads == []


def _fake_processed(original_size=5, vision_size=5, thumbnail_size=5):
    from cognitrix.media.processing import _ProcessedImage

    return _ProcessedImage(
        original=b'o' * original_size,
        vision=b'v' * vision_size,
        thumbnail=b't' * thumbnail_size if thumbnail_size is not None else None,
        mime_type='image/png',
        extension='.png',
        width=10,
        height=10,
    )


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (None, 2),
        ('', 2),
        ('invalid', 2),
        ('0', 1),
        ('-10', 1),
        ('4', 4),
        ('9', 8),
    ],
)
def test_media_processing_concurrency_defaults_and_clamps(raw, expected):
    from cognitrix.media.processing import _parse_media_processing_concurrency

    assert _parse_media_processing_concurrency(raw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('image_format', ['WEBP', 'BMP', 'TIFF'])
async def test_other_accepted_rasters_become_png_edit_masters(
    artifact_store, tmp_path, image_format
):
    data = _image_bytes('RGB', (14, 9), (50, 100, 150), image_format)
    artifact = await _ingest(
        MediaAssetService(),
        tmp_path,
        data,
        filename=f'input.{image_format.lower()}',
        declared_mime=f'image/{image_format.lower()}',
    )

    assert artifact.mime_type == 'image/png'
    with Image.open(variant_path(artifact, 'original')) as image:
        assert image.format == 'PNG'


def _seed_retained(rows, root, *, artifact_id, ownership, sizes=(1, 1, 1)):
    from cognitrix import artifacts

    namespace = artifacts._storage_namespace(ownership.session_id)
    keys = (
        f'{namespace}/{artifact_id}-original.png',
        f'{namespace}/{artifact_id}-vision.png',
        f'{namespace}/{artifact_id}-thumbnail.webp',
    )
    for key, size in zip(keys, sizes, strict=True):
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb') as stream:
            stream.truncate(size)
    artifact = Artifact(
        _id=artifact_id,
        session_id=ownership.session_id,
        user_id=ownership.user_id,
        agent_id=ownership.agent_id,
        storage_key=keys[0],
        vision_storage_key=keys[1],
        thumbnail_storage_key=keys[2],
        size_bytes=sizes[0],
    )
    rows[artifact_id] = artifact
    return artifact, [root / key for key in keys]


@pytest.mark.asyncio
async def test_sanitized_master_over_ten_mib_leaves_no_files_or_row(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    monkeypatch.setattr(
        service_module,
        '_process_image',
        lambda data: _fake_processed(original_size=10 * 1024 * 1024 + 1),
    )

    with pytest.raises(MediaValidationError, match='10 MiB'):
        await MediaAssetService().store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )

    assert rows == {}
    assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))


@pytest.mark.asyncio
async def test_session_byte_quota_counts_master_and_both_derivatives(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    _seed_retained(
        rows,
        root,
        artifact_id='retained',
        ownership=ownership,
        sizes=(100 * 1024 * 1024 - 12, 0, 0),
    )
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())

    with pytest.raises(MediaQuotaError, match='storage limit'):
        await MediaAssetService().store_generated_image(b'new', {}, ownership)

    assert list(rows) == ['retained']
    assert not list(root.rglob('*.tmp'))


@pytest.mark.asyncio
async def test_twenty_row_limit_counts_images_not_derivative_files(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    for index in range(19):
        _seed_retained(
            rows,
            root,
            artifact_id=f'retained-{index}',
            ownership=ownership,
        )
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    service = MediaAssetService()

    await service.store_generated_image(b'twentieth', {}, ownership)
    assert len(rows) == 20
    with pytest.raises(MediaQuotaError, match='image limit'):
        await service.store_generated_image(b'twenty-first', {}, ownership)

    assert len(rows) == 20


@pytest.mark.asyncio
async def test_metadata_save_failure_removes_final_and_temporary_files(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())

    async def fail_save(row):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(Artifact, 'save', fail_save)

    with pytest.raises(RuntimeError, match='database unavailable'):
        await MediaAssetService().store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )

    assert rows == {}
    assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))


@pytest.mark.asyncio
async def test_delete_artifacts_removes_all_variants_and_only_authorized_rows(
    artifact_store,
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    owned, owned_paths = _seed_retained(
        rows,
        root,
        artifact_id='owned',
        ownership=ownership,
    )
    other, other_paths = _seed_retained(
        rows,
        root,
        artifact_id='other',
        ownership=MediaOwnership('session', 'other-user', 'agent'),
    )
    service = MediaAssetService()

    await service.delete_artifacts([str(owned.id)], ownership)

    assert 'owned' not in rows
    assert not any(path.exists() for path in owned_paths)
    assert rows['other'] is other
    assert all(path.exists() for path in other_paths)
    with pytest.raises(MediaAccessError):
        await service.delete_artifacts([str(other.id)], ownership)
    assert all(path.exists() for path in other_paths)


@pytest.mark.asyncio
async def test_concurrent_commits_serialize_the_final_session_quota_check(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    _seed_retained(
        rows,
        root,
        artifact_id='retained',
        ownership=ownership,
        sizes=(100 * 1024 * 1024 - 10, 0, 0),
    )
    monkeypatch.setattr(
        service_module,
        '_process_image',
        lambda data: _fake_processed(original_size=4, vision_size=3, thumbnail_size=2),
    )

    async def slow_save(row):
        await asyncio.sleep(0.03)
        rows[str(row.id)] = row
        return row

    monkeypatch.setattr(Artifact, 'save', slow_save)
    service = MediaAssetService()
    results = await asyncio.gather(
        service.store_generated_image(b'first', {}, ownership),
        service.store_generated_image(b'second', {}, ownership),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, MediaQuotaError) for result in results) == 1
    assert len(rows) == 2
    assert not list(root.rglob('*.tmp'))


@pytest.mark.asyncio
async def test_slow_same_session_save_blocks_later_processing_only_for_that_session(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, _ = artifact_store
    processed_inputs = []
    first_save_started = asyncio.Event()
    release_first_save = asyncio.Event()

    def process(data):
        processed_inputs.append(data)
        return _fake_processed()

    async def slow_save(row):
        if row.prompt == 'first':
            first_save_started.set()
            await release_first_save.wait()
        if row.id is None:
            object.__setattr__(row, 'id', str(uuid.uuid4()))
        rows[str(row.id)] = row
        return row

    monkeypatch.setattr(service_module, '_process_image', process)
    monkeypatch.setattr(Artifact, 'save', slow_save)
    service = MediaAssetService()
    first = asyncio.create_task(
        service.store_generated_image(
            b'first',
            {'prompt': 'first'},
            MediaOwnership('session-a', 'user', 'agent'),
        )
    )
    same_session = None
    other_session = None
    try:
        await asyncio.wait_for(first_save_started.wait(), 1)
        same_session = asyncio.create_task(
            service.store_generated_image(
                b'same',
                {'prompt': 'same'},
                MediaOwnership('session-a', 'user', 'agent'),
            )
        )
        other_session = asyncio.create_task(
            service.store_generated_image(
                b'other',
                {'prompt': 'other'},
                MediaOwnership('session-b', 'user', 'agent'),
            )
        )

        await asyncio.wait_for(other_session, 1)
        assert b'other' in processed_inputs
        assert b'same' not in processed_inputs

        release_first_save.set()
        await asyncio.gather(first, same_session)
        assert processed_inputs.index(b'same') > processed_inputs.index(b'first')
    finally:
        release_first_save.set()
        pending = [
            task
            for task in (first, same_session, other_session)
            if task is not None and not task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_media_cpu_is_bounded_and_uses_worker_threads(monkeypatch):
    from cognitrix.media import processing

    monkeypatch.setattr(processing, 'MEDIA_PROCESSING_CONCURRENCY', 2)
    event_loop_thread = threading.get_ident()
    worker_threads = set()
    active = 0
    maximum = 0
    guard = threading.Lock()

    def observe():
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            worker_threads.add(threading.get_ident())
        time.sleep(0.03)
        with guard:
            active -= 1

    await asyncio.gather(*(processing.run_media_cpu(observe) for _ in range(6)))

    assert maximum == 2
    assert worker_threads
    assert event_loop_thread not in worker_threads


@pytest.mark.asyncio
async def test_legacy_thumbnail_is_created_once_for_concurrent_resolvers(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='legacy',
        ownership=ownership,
    )
    original = _image_bytes('RGB', (640, 320), (10, 40, 80), 'PNG')
    paths[0].write_bytes(original)
    artifact.thumbnail_storage_key = None
    paths[2].unlink()
    thumbnail = _image_bytes('RGB', (384, 192), (10, 40, 80), 'WEBP')
    encodes = 0
    guard = threading.Lock()

    def make_thumbnail(data):
        nonlocal encodes
        with guard:
            encodes += 1
        time.sleep(0.02)
        return thumbnail

    monkeypatch.setattr(service_module, '_make_thumbnail', make_thumbnail, raising=False)
    service = MediaAssetService()
    first, second = await asyncio.gather(
        service.resolve_variant_file('legacy', ownership, 'thumbnail'),
        service.resolve_variant_file('legacy', ownership, 'thumbnail'),
    )

    assert encodes == 1
    assert first.path == second.path
    assert first.mime_type == 'image/webp'
    assert first.path.read_bytes() == thumbnail
    assert artifact.thumbnail_storage_key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'invalid_kind',
    ['corrupt', 'oversized', 'wrong-format', 'metadata'],
)
async def test_invalid_existing_thumbnail_is_atomically_regenerated(
    artifact_store, invalid_kind
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='legacy-raw-id',
        ownership=ownership,
    )
    paths[0].write_bytes(_image_bytes('RGB', (640, 320), (10, 40, 80), 'PNG'))
    if invalid_kind == 'corrupt':
        invalid_thumbnail = b'not a thumbnail'
    elif invalid_kind == 'oversized':
        invalid_thumbnail = _image_bytes(
            'RGB', (500, 250), (10, 40, 80), 'WEBP'
        )
    elif invalid_kind == 'wrong-format':
        invalid_thumbnail = _image_bytes(
            'RGB', (200, 100), (10, 40, 80), 'PNG'
        )
    else:
        exif = Image.Exif()
        exif[270] = 'private metadata'
        invalid_thumbnail = _image_bytes(
            'RGB',
            (200, 100),
            (10, 40, 80),
            'WEBP',
            exif=exif,
        )
    paths[2].write_bytes(invalid_thumbnail)
    previous_path = paths[2]

    resolved = await MediaAssetService().resolve_variant_file(
        str(artifact.id),
        ownership,
        'thumbnail',
    )

    assert resolved.path != previous_path
    assert not previous_path.exists()
    assert 'legacy-raw-id' not in resolved.path.name
    with Image.open(resolved.path) as thumbnail:
        thumbnail.load()
        assert thumbnail.format == 'WEBP'
        assert max(thumbnail.size) <= 384
        assert not thumbnail.getexif()
        assert not {'exif', 'icc_profile', 'xmp'}.intersection(thumbnail.info)


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_lazy_thumbnail_rollback(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='cancel-lazy-thumbnail',
        ownership=ownership,
    )
    paths[0].write_bytes(_image_bytes('RGB', (640, 320), (10, 40, 80), 'PNG'))
    artifact.thumbnail_storage_key = None
    paths[2].unlink()
    replace_started = threading.Event()
    replace_release = threading.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_interrupted = asyncio.Event()
    original_replace = service_module.os.replace
    original_remove_paths = service_module._remove_paths

    def replace(source, destination):
        result = original_replace(source, destination)
        if not replace_started.is_set():
            replace_started.set()
            replace_release.wait(timeout=5)
        return result

    async def gated_remove_paths(candidate_paths):
        cleanup_started.set()
        try:
            await cleanup_release.wait()
        except asyncio.CancelledError:
            cleanup_interrupted.set()
            raise
        await original_remove_paths(candidate_paths)

    monkeypatch.setattr(service_module.os, 'replace', replace)
    monkeypatch.setattr(service_module, '_remove_paths', gated_remove_paths)
    task = asyncio.create_task(
        MediaAssetService().resolve_variant_file(
            str(artifact.id), ownership, 'thumbnail'
        )
    )
    try:
        assert await asyncio.to_thread(replace_started.wait, 1)
        task.cancel()
        replace_release.set()
        await asyncio.wait_for(cleanup_started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)

        assert not task.done()
        assert not cleanup_interrupted.is_set()

        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert artifact.thumbnail_storage_key is None
        assert not list(root.rglob('*-thumbnail.webp'))
        assert not list(root.rglob('*.tmp'))
    finally:
        replace_release.set()
        cleanup_release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_lazy_thumbnail_replacement_credits_the_invalid_derivative_bytes(
    artifact_store,
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='quota-heal-thumbnail',
        ownership=ownership,
        sizes=(1, 0, 1),
    )
    original = _image_bytes('RGB', (640, 320), (10, 40, 80), 'PNG')
    invalid_thumbnail = b'x' * 4096
    paths[0].write_bytes(original)
    paths[2].write_bytes(invalid_thumbnail)
    retained_without_filler = len(original) + len(invalid_thumbnail)
    _seed_retained(
        rows,
        root,
        artifact_id='quota-filler',
        ownership=ownership,
        sizes=(service_module.MAX_SESSION_BYTES - retained_without_filler, 0, 0),
    )

    resolved = await MediaAssetService().resolve_variant_file(
        str(artifact.id), ownership, 'thumbnail'
    )

    assert resolved.path.is_file()
    assert resolved.path.stat().st_size < len(invalid_thumbnail)
    assert not paths[2].exists()


@pytest.mark.asyncio
async def test_cancellation_after_lazy_metadata_save_keeps_the_committed_thumbnail(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='post-save-cancel',
        ownership=ownership,
    )
    paths[0].write_bytes(_image_bytes('RGB', (640, 320), (10, 40, 80), 'PNG'))
    paths[2].write_bytes(b'invalid old thumbnail')
    previous_key = artifact.thumbnail_storage_key
    previous_path = paths[2]
    cleanup_started = asyncio.Event()
    original_remove_paths = service_module._remove_paths

    async def block_old_cleanup(candidate_paths):
        if list(candidate_paths) == [previous_path]:
            cleanup_started.set()
            await asyncio.Event().wait()
        await original_remove_paths(candidate_paths)

    monkeypatch.setattr(service_module, '_remove_paths', block_old_cleanup)
    task = asyncio.create_task(
        MediaAssetService().resolve_variant_file(
            str(artifact.id), ownership, 'thumbnail'
        )
    )
    try:
        await asyncio.wait_for(cleanup_started.wait(), 1)
        committed_key = artifact.thumbnail_storage_key
        assert committed_key and committed_key != previous_key
        committed_path = root / committed_key
        assert committed_path.is_file()

        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert artifact.thumbnail_storage_key == committed_key
        assert committed_path.is_file()
        assert previous_path.is_file()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_thumbnail_encode_failure_keeps_required_variants(
    monkeypatch, artifact_store, tmp_path, rgba_png_bytes
):
    from cognitrix.media import processing

    rows, _ = artifact_store

    def fail_thumbnail(image):
        raise OSError('webp unavailable')

    monkeypatch.setattr(processing, '_encode_thumbnail', fail_thumbnail)
    artifact = await _ingest(
        MediaAssetService(),
        tmp_path,
        rgba_png_bytes,
        filename='alpha.png',
        declared_mime='image/png',
    )

    assert rows[str(artifact.id)] is artifact
    assert artifact.thumbnail_storage_key is None
    assert variant_path(artifact, 'original').is_file()
    assert variant_path(artifact, 'vision').is_file()


@pytest.mark.asyncio
async def test_legacy_store_png_wrapper_routes_through_media_assets(
    monkeypatch, artifact_store
):
    from cognitrix import artifacts
    from cognitrix.media import service as service_module

    row = Artifact(
        _id='stored',
        session_id='session',
        user_id='user',
        agent_id='agent',
        storage_key='safe/stored.png',
    )
    captured = {}

    async def store_generated_image(data, metadata, ownership):
        captured.update(data=data, metadata=metadata, ownership=ownership)
        return SimpleNamespace(id='stored')

    async def get(artifact_id):
        assert artifact_id == 'stored'
        return row

    async def old_save(candidate):
        return candidate

    monkeypatch.setattr(
        service_module.media_assets,
        'store_generated_image',
        store_generated_image,
    )
    monkeypatch.setattr(Artifact, 'get', get)
    monkeypatch.setattr(Artifact, 'save', old_save)

    result = await artifacts.store_png(
        b'provider image',
        session_id='session',
        user_id='user',
        agent_id='agent',
        prompt='draw',
        source_artifact_id='source',
        model='provider-model',
        width=12,
        height=8,
    )

    assert result is row
    assert captured == {
        'data': b'provider image',
        'metadata': {
            'prompt': 'draw',
            'source_artifact_id': 'source',
            'model': 'provider-model',
            'width': 12,
            'height': 8,
        },
        'ownership': MediaOwnership('session', 'user', 'agent'),
    }


@pytest.mark.asyncio
async def test_legacy_source_image_wrapper_routes_through_media_assets(
    monkeypatch, artifact_store
):
    from cognitrix import artifacts
    from cognitrix.media import service as service_module

    row = Artifact(
        _id='source',
        session_id='session',
        user_id='user',
        storage_key='safe/source.png',
        mime_type='image/png',
    )
    rows, root = artifact_store
    rows['source'] = row
    path = root / row.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'old implementation')
    ownerships = []

    async def resolve_image(artifact_id, ownership, variant='original'):
        assert artifact_id == 'source'
        ownerships.append(ownership)
        return SimpleNamespace(ref=SimpleNamespace(id='source'), data=b'via media service')

    monkeypatch.setattr(service_module.media_assets, 'resolve_image', resolve_image)

    result, data = await artifacts.source_image(
        'source',
        session_id='session',
        user_id='user',
    )

    assert result is row
    assert data == b'via media service'
    assert ownerships == [MediaOwnership('session', 'user', None)]


@pytest.mark.asyncio
async def test_legacy_session_delete_wrapper_only_cleans_unbound_image_media(
    monkeypatch, artifact_store
):
    from cognitrix import artifacts
    from cognitrix.media.documents import document_assets
    from cognitrix.media import service as service_module

    deleted = []
    deleted_documents = []

    async def delete_session_media(session_id, ownership=None):
        deleted.append((session_id, ownership))

    async def delete_session_documents(session_id):
        deleted_documents.append(session_id)

    monkeypatch.setattr(
        service_module.media_assets,
        'delete_session_media',
        delete_session_media,
    )
    monkeypatch.setattr(
        document_assets,
        'delete_session_documents',
        delete_session_documents,
    )

    await artifacts.delete_session_artifacts('session')

    assert deleted == [('session', None)]
    # Document cleanup requires exact user/session/agent authority and is
    # therefore handled by delete_owned_session_artifacts, never this legacy
    # session-id-only compatibility wrapper.
    assert deleted_documents == []


@pytest.mark.asyncio
async def test_resolve_image_rejects_legacy_file_over_ten_mib_before_read(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='legacy-large',
        ownership=ownership,
        sizes=(10 * 1024 * 1024 + 1, 1, 1),
    )
    reads = []

    def unexpected_read(path):
        reads.append(path)
        raise AssertionError('oversized legacy files must be rejected before read')

    monkeypatch.setattr(Path, 'read_bytes', unexpected_read)

    with pytest.raises(MediaValidationError, match='10 MiB'):
        await MediaAssetService().resolve_image(str(artifact.id), ownership)
    assert reads == []
    assert paths[0].stat().st_size == 10 * 1024 * 1024 + 1


@pytest.mark.asyncio
async def test_cancelled_row_save_removes_promoted_and_temporary_files(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    _, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())

    async def cancel_save(row):
        raise asyncio.CancelledError

    monkeypatch.setattr(Artifact, 'save', cancel_save)

    with pytest.raises(asyncio.CancelledError):
        await MediaAssetService().store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )

    assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))


@pytest.mark.asyncio
async def test_staged_read_failure_logs_one_safe_failure_metric(
    caplog, artifact_store, tmp_path
):
    missing = tmp_path / 'secret-user-path.png'
    staged = StagedAttachment(
        path=missing,
        filename='private-prompt.png',
        declared_mime='image/png',
        size_bytes=123,
    )

    with caplog.at_level('INFO', logger='cognitrix.log'):
        with pytest.raises(FileNotFoundError):
            await MediaAssetService().ingest_staged_image(
                staged,
                MediaOwnership('session-secret', 'user-secret', 'agent-secret'),
            )

    metrics = [record.getMessage() for record in caplog.records if 'media_ingest ' in record.getMessage()]
    assert len(metrics) == 1
    metric = metrics[0]
    assert 'status=failure' in metric
    assert 'origin=uploaded' in metric
    assert 'input_bytes=0' in metric
    assert 'original_bytes=0' in metric
    assert 'vision_bytes=0' in metric
    assert 'thumbnail_bytes=0' in metric
    assert 'width=0' in metric and 'height=0' in metric
    assert 'media_ingest_ms=' in metric
    assert all(secret not in metric for secret in ('secret-user-path', 'private-prompt', 'user-secret'))


@pytest.mark.asyncio
async def test_lazy_thumbnail_rejects_oversized_legacy_original_before_read(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='legacy-large-thumbnail',
        ownership=ownership,
        sizes=(10 * 1024 * 1024 + 1, 1, 1),
    )
    artifact.thumbnail_storage_key = None
    paths[2].unlink()
    reads = []

    def unexpected_read(path):
        reads.append(path)
        raise AssertionError('oversized legacy files must be rejected before read')

    monkeypatch.setattr(Path, 'read_bytes', unexpected_read)

    with pytest.raises(MediaValidationError, match='10 MiB'):
        await MediaAssetService().resolve_variant_file(
            str(artifact.id),
            ownership,
            'thumbnail',
        )
    assert reads == []


@pytest.mark.asyncio
async def test_delete_unlink_failure_retains_row_for_retry(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='locked',
        ownership=ownership,
    )
    original_unlink = Path.unlink

    def unlink(path, missing_ok=False):
        if path == paths[1]:
            raise PermissionError('variant locked')
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, 'unlink', unlink)

    with pytest.raises(PermissionError, match='variant locked'):
        await MediaAssetService().delete_artifacts([str(artifact.id)], ownership)
    assert rows['locked'] is artifact
    assert not paths[0].exists()
    assert paths[1].is_file()
    assert paths[2].is_file()


@pytest.mark.asyncio
async def test_session_delete_unlink_failure_retains_all_rows_until_retry(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    first, first_paths = _seed_retained(
        rows,
        root,
        artifact_id='session-first',
        ownership=ownership,
    )
    second, second_paths = _seed_retained(
        rows,
        root,
        artifact_id='session-second',
        ownership=ownership,
    )
    original_unlink = Path.unlink
    blocked = True

    def unlink(path, missing_ok=False):
        if blocked and path == first_paths[1]:
            raise PermissionError('variant locked')
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, 'unlink', unlink)

    with pytest.raises(PermissionError, match='variant locked'):
        await MediaAssetService().delete_session_media('session', ownership)

    assert rows == {
        'session-first': first,
        'session-second': second,
    }
    assert not first_paths[0].exists()
    assert first_paths[1].is_file()
    assert all(path.is_file() for path in second_paths)

    blocked = False
    await MediaAssetService().delete_session_media('session', ownership)
    assert rows == {}
    assert not any(path.exists() for path in [*first_paths, *second_paths])


@pytest.mark.asyncio
async def test_delete_fails_closed_on_a_corrupt_variant_storage_key(artifact_store):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='corrupt-key',
        ownership=ownership,
    )
    artifact.vision_storage_key = '../outside.png'

    with pytest.raises(ValueError, match='storage key'):
        await MediaAssetService().delete_artifacts([str(artifact.id)], ownership)

    assert rows['corrupt-key'] is artifact
    assert paths[0].is_file()
    assert paths[2].is_file()


@pytest.mark.asyncio
async def test_database_delete_failure_retains_row_after_files_are_removed(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='database-failure',
        ownership=ownership,
    )

    async def fail_delete(query):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(Artifact, 'delete_many', fail_delete)

    with pytest.raises(RuntimeError, match='database unavailable'):
        await MediaAssetService().delete_artifacts([str(artifact.id)], ownership)

    assert rows['database-failure'] is artifact
    assert not any(path.exists() for path in paths)
    assert not list(root.rglob('*.tombstone'))


@pytest.mark.asyncio
async def test_multi_delete_keeps_each_artifact_consistent_when_second_row_delete_fails(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    first, first_paths = _seed_retained(
        rows,
        root,
        artifact_id='first-delete',
        ownership=ownership,
    )
    second, second_paths = _seed_retained(
        rows,
        root,
        artifact_id='second-delete',
        ownership=ownership,
    )

    async def fail_second_delete(query):
        artifact_id = query['id']
        if artifact_id == str(second.id):
            raise RuntimeError('second delete unavailable')
        rows.pop(artifact_id)
        return 1

    monkeypatch.setattr(Artifact, 'delete_many', fail_second_delete)

    with pytest.raises(RuntimeError, match='second delete unavailable'):
        await MediaAssetService().delete_artifacts(
            [str(first.id), str(second.id)],
            ownership,
        )

    assert 'first-delete' not in rows
    assert not any(path.exists() for path in first_paths)
    assert rows['second-delete'] is second
    assert not any(path.exists() for path in second_paths)
    assert not list(root.rglob('*.tombstone'))


@pytest.mark.asyncio
async def test_cancelled_delete_retains_row_after_started_unlink_settles(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='cancel-delete',
        ownership=ownership,
    )
    started = threading.Event()
    release = threading.Event()
    original_unlink = Path.unlink

    def unlink(path, missing_ok=False):
        result = original_unlink(path, missing_ok=missing_ok)
        if path == paths[0]:
            started.set()
            release.wait(timeout=5)
        return result

    monkeypatch.setattr(Path, 'unlink', unlink)
    task = asyncio.create_task(
        MediaAssetService().delete_artifacts([str(artifact.id)], ownership)
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rows['cancel-delete'] is artifact
        assert not paths[0].exists()
        assert paths[1].is_file()
        assert paths[2].is_file()
        assert not list(root.rglob('*.tombstone'))
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_after_row_delete_settles_as_a_complete_deletion(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='delete-effect',
        ownership=ownership,
    )
    deleted = asyncio.Event()
    release_delete = asyncio.Event()
    delete_interrupted = asyncio.Event()

    async def effect_then_wait(query):
        rows.pop(query['id'])
        deleted.set()
        try:
            await release_delete.wait()
        except asyncio.CancelledError:
            delete_interrupted.set()
            raise
        return 1

    monkeypatch.setattr(Artifact, 'delete_many', effect_then_wait)
    task = asyncio.create_task(
        MediaAssetService().delete_artifacts([str(artifact.id)], ownership)
    )
    try:
        await asyncio.wait_for(deleted.wait(), 1)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not delete_interrupted.is_set()
        assert not task.done()

        release_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert 'delete-effect' not in rows
        assert not any(path.exists() for path in paths)
        assert not list(root.rglob('*.tombstone'))
    finally:
        release_delete.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_delete_removes_every_variant_before_deleting_the_row(
    monkeypatch, artifact_store
):
    rows, root = artifact_store
    ownership = MediaOwnership('session', 'user', 'agent')
    artifact, paths = _seed_retained(
        rows,
        root,
        artifact_id='purge-after-delete',
        ownership=ownership,
    )
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()
    original_unlink = Path.unlink

    def unlink(path, missing_ok=False):
        result = original_unlink(path, missing_ok=missing_ok)
        if path == paths[0]:
            first_started.set()
            release_first.wait(timeout=5)
        elif path == paths[1]:
            second_started.set()
            release_second.wait(timeout=5)
        return result

    monkeypatch.setattr(Path, 'unlink', unlink)
    task = asyncio.create_task(
        MediaAssetService().delete_artifacts([str(artifact.id)], ownership)
    )
    try:
        assert await asyncio.to_thread(first_started.wait, 1)
        assert rows['purge-after-delete'] is artifact
        assert not paths[0].exists()
        assert not task.done()

        release_first.set()
        assert await asyncio.to_thread(second_started.wait, 1)
        assert rows['purge-after-delete'] is artifact
        assert not paths[1].exists()
        assert not task.done()

        release_second.set()
        await task
        assert 'purge-after-delete' not in rows
        assert not any(path.exists() for path in paths)
        assert not list(root.rglob('*.tombstone'))
    finally:
        release_first.set()
        release_second.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_generated_image_round_trips_through_real_sqlite(
    monkeypatch, tmp_path, rgba_png_bytes
):
    from odbms import DBMS

    from cognitrix import artifacts
    from cognitrix.config import _patch_odbms_sqlite

    database = tmp_path / 'media-roundtrip.db'
    monkeypatch.setattr(DBMS, 'Database', DBMS.Database)
    initialize_async = getattr(DBMS, 'initialize_async', None)
    if initialize_async is not None:
        await initialize_async('sqlite', database=str(database))
    else:
        DBMS.initialize('sqlite', database=str(database))
    _patch_odbms_sqlite()
    create = getattr(Artifact, '_create_table_async', None) or Artifact.create_table
    await create()
    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path / 'real-artifacts')

    stored_ref = await MediaAssetService().store_generated_image(
        rgba_png_bytes,
        {'prompt': 'round trip'},
        MediaOwnership('sqlite-session', 'sqlite-user', 'sqlite-agent'),
    )

    loaded = await Artifact.get(stored_ref.id)
    assert loaded is not None
    assert loaded.session_id == 'sqlite-session'
    assert loaded.storage_key
    assert variant_path(loaded, 'original').is_file()


@pytest.mark.asyncio
async def test_cancelled_media_worker_holds_concurrency_slot_until_thread_finishes(
    monkeypatch,
):
    from cognitrix.media import processing

    monkeypatch.setattr(processing, 'MEDIA_PROCESSING_CONCURRENCY', 1)
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()

    def blocking(started, release):
        started.set()
        release.wait(timeout=5)

    first = asyncio.create_task(
        processing.run_media_cpu(blocking, first_started, release_first)
    )
    second = None
    try:
        assert await asyncio.to_thread(first_started.wait, 1)
        first.cancel()
        await asyncio.sleep(0.02)
        second = asyncio.create_task(
            processing.run_media_cpu(blocking, second_started, release_second)
        )
        await asyncio.sleep(0.05)

        assert not first.done()
        assert not second_started.is_set()

        release_first.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await asyncio.to_thread(second_started.wait, 1)
        release_second.set()
        await second
    finally:
        release_first.set()
        release_second.set()
        tasks = [task for task in (first, second) if task is not None and not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked_operation', ['write', 'replace'])
async def test_cancelled_filesystem_mutation_joins_worker_before_cleanup(
    monkeypatch, artifact_store, blocked_operation
):
    from cognitrix.media import service as service_module

    _, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    started = threading.Event()
    release = threading.Event()
    original_write = Path.write_bytes
    original_replace = service_module.os.replace

    def write_bytes(path, data):
        if blocked_operation == 'write' and path.name.endswith('.tmp') and not started.is_set():
            started.set()
            release.wait(timeout=5)
        return original_write(path, data)

    def replace(source, destination):
        if blocked_operation == 'replace' and not started.is_set():
            started.set()
            release.wait(timeout=5)
        return original_replace(source, destination)

    monkeypatch.setattr(Path, 'write_bytes', write_bytes)
    monkeypatch.setattr(service_module.os, 'replace', replace)
    task = asyncio.create_task(
        MediaAssetService().store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)
        for path in sorted(root.rglob('*'), reverse=True) if root.exists() else []:
            if path.is_file():
                path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_commit_rollback(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    save_started = asyncio.Event()
    save_release = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_interrupted = asyncio.Event()
    original_remove_paths = service_module._remove_paths

    async def definitively_failed_save(row):
        save_started.set()
        await save_release.wait()
        raise RuntimeError('database write failed')

    async def gated_remove_paths(paths):
        cleanup_started.set()
        try:
            await cleanup_release.wait()
        except asyncio.CancelledError:
            cleanup_interrupted.set()
            raise
        await original_remove_paths(paths)

    monkeypatch.setattr(Artifact, 'save', definitively_failed_save)
    monkeypatch.setattr(service_module, '_remove_paths', gated_remove_paths)
    task = asyncio.create_task(
        MediaAssetService().store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )
    )
    try:
        await asyncio.wait_for(save_started.wait(), 1)
        task.cancel()
        for _ in range(3):
            await asyncio.sleep(0)
        assert not cleanup_started.is_set()

        save_release.set()
        await asyncio.wait_for(cleanup_started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)

        assert not task.done()
        assert not cleanup_interrupted.is_set()

        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rows == {}
        assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))
    finally:
        save_release.set()
        cleanup_release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_after_artifact_insert_compensates_after_save_settles(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    inserted = asyncio.Event()
    release_save = asyncio.Event()
    save_interrupted = asyncio.Event()

    async def effect_then_wait(row):
        if row.id is None:
            object.__setattr__(row, 'id', str(uuid.uuid4()))
        rows[str(row.id)] = row
        inserted.set()
        try:
            await release_save.wait()
        except asyncio.CancelledError:
            save_interrupted.set()
            raise
        return row

    monkeypatch.setattr(Artifact, 'save', effect_then_wait)
    task = asyncio.create_task(
        MediaAssetService().store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )
    )
    try:
        await asyncio.wait_for(inserted.wait(), 1)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not save_interrupted.is_set()
        assert not task.done()

        release_save.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rows == {}
        assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))
    finally:
        release_save.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['generated', 'uploaded'])
async def test_cancellation_after_commit_return_compensates_the_unreported_asset(
    monkeypatch, artifact_store, tmp_path, rgba_png_bytes, source
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    service = MediaAssetService()
    original_commit = service._commit_processed
    outer_task = None

    async def commit_then_cancel_outer(*args, **kwargs):
        artifact = await original_commit(*args, **kwargs)
        assert outer_task is not None
        outer_task.cancel()
        return artifact

    monkeypatch.setattr(service, '_commit_processed', commit_then_cancel_outer)
    ownership = MediaOwnership('session', 'user', 'agent')
    if source == 'generated':
        operation = service.store_generated_image(
            b'provider bytes',
            {},
            ownership,
        )
    else:
        operation = service.ingest_staged_image(
            _stage(
                tmp_path,
                rgba_png_bytes,
                filename='upload.png',
                declared_mime='image/png',
            ),
            ownership,
        )
    outer_task = asyncio.create_task(operation)

    with pytest.raises(asyncio.CancelledError):
        await outer_task

    assert rows == {}
    assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))
    assert not root.exists() or list(root.rglob('*.tmp')) == []
    assert not root.exists() or list(root.rglob('*.tombstone')) == []


@pytest.mark.asyncio
async def test_failed_cancelled_commit_compensation_exposes_the_unreported_ref(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, _ = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    service = MediaAssetService()
    original_commit = service._commit_processed
    outer_task = None

    async def commit_then_cancel_outer(*args, **kwargs):
        artifact = await original_commit(*args, **kwargs)
        assert outer_task is not None
        outer_task.cancel()
        return artifact

    async def fail_compensating_delete(_query):
        raise RuntimeError('artifact database is unavailable')

    monkeypatch.setattr(service, '_commit_processed', commit_then_cancel_outer)
    monkeypatch.setattr(Artifact, 'delete_many', fail_compensating_delete)
    outer_task = asyncio.create_task(
        service.store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )
    )

    with pytest.raises(asyncio.CancelledError) as captured:
        await outer_task

    cleanup_error = captured.value
    assert cleanup_error.artifact_ref.id in rows
    assert isinstance(cleanup_error.cleanup_error, RuntimeError)


@pytest.mark.asyncio
async def test_cancelled_commit_unlink_failure_retains_row_for_bounded_retry(
    monkeypatch, artifact_store
):
    from cognitrix.media import service as service_module

    rows, root = artifact_store
    monkeypatch.setattr(service_module, '_process_image', lambda data: _fake_processed())
    service = MediaAssetService()
    original_commit = service._commit_processed
    original_unlink = Path.unlink
    outer_task = None

    async def commit_then_cancel_outer(*args, **kwargs):
        artifact = await original_commit(*args, **kwargs)
        assert outer_task is not None
        outer_task.cancel()
        return artifact

    def fail_vision_unlink(path, missing_ok=False):
        if '-vision.' in path.name and not path.name.endswith('.tmp'):
            raise OSError('artifact storage is unavailable')
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(service, '_commit_processed', commit_then_cancel_outer)
    monkeypatch.setattr(Path, 'unlink', fail_vision_unlink)
    outer_task = asyncio.create_task(
        service.store_generated_image(
            b'provider bytes',
            {},
            MediaOwnership('session', 'user', 'agent'),
        )
    )

    with pytest.raises(service_module.CommittedArtifactCleanupError) as captured:
        await outer_task

    assert captured.value.artifact_ref.id in rows
    assert isinstance(captured.value.cleanup_error, OSError)
    assert any(path.is_file() for path in root.rglob('*'))
    assert list(root.rglob('*.tombstone')) == []


@pytest.mark.asyncio
async def test_staged_ingest_rejects_missing_session_before_open(
    monkeypatch, artifact_store, tmp_path, rgba_png_bytes
):
    staged = _stage(tmp_path, rgba_png_bytes, filename='image.png', declared_mime='image/png')
    opens = []

    def unexpected_open(path, *args, **kwargs):
        opens.append(path)
        raise AssertionError('staged I/O must follow ownership validation')

    monkeypatch.setattr(Path, 'open', unexpected_open)

    with pytest.raises(MediaValidationError, match='requires a session'):
        await MediaAssetService().ingest_staged_image(
            staged,
            MediaOwnership(None, 'user', 'agent'),
        )
    assert opens == []


@pytest.mark.asyncio
async def test_staged_ingest_rejects_declared_and_actual_size_mismatch(
    artifact_store, tmp_path, rgba_png_bytes
):
    rows, root = artifact_store
    staged = _stage(tmp_path, rgba_png_bytes, filename='image.png', declared_mime='image/png')
    staged = StagedAttachment(
        path=staged.path,
        filename=staged.filename,
        declared_mime=staged.declared_mime,
        size_bytes=staged.size_bytes + 1,
    )

    with pytest.raises(MediaValidationError, match='size changed'):
        await MediaAssetService().ingest_staged_image(
            staged,
            MediaOwnership('session', 'user', 'agent'),
        )
    assert rows == {}
    assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))


@pytest.mark.asyncio
async def test_staged_ingest_bounded_read_rejects_replaced_oversized_file(
    artifact_store, tmp_path
):
    rows, root = artifact_store
    path = tmp_path / 'replaced-upload.png'
    with path.open('wb') as stream:
        stream.truncate(10 * 1024 * 1024 + 1)
    staged = StagedAttachment(
        path=path,
        filename='image.png',
        declared_mime='image/png',
        size_bytes=10 * 1024 * 1024 + 1,
    )

    with pytest.raises(MediaValidationError, match='10 MiB'):
        await MediaAssetService().ingest_staged_image(
            staged,
            MediaOwnership('session', 'user', 'agent'),
        )
    assert rows == {}
    assert not root.exists() or not any(path.is_file() for path in root.rglob('*'))
