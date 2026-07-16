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
            row.id = str(uuid.uuid4())
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
async def test_legacy_session_delete_wrapper_routes_through_media_assets(
    monkeypatch, artifact_store
):
    from cognitrix import artifacts
    from cognitrix.media import service as service_module

    deleted = []

    async def delete_session_media(session_id, ownership=None):
        deleted.append((session_id, ownership))

    monkeypatch.setattr(
        service_module.media_assets,
        'delete_session_media',
        delete_session_media,
    )

    await artifacts.delete_session_artifacts('session')

    assert deleted == [('session', None)]


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
async def test_delete_keeps_row_when_a_variant_cannot_be_unlinked(
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
