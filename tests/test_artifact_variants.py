import asyncio
import io
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image


async def _async(value):
    return value


def _artifact(*, user_id='owner', agent_id='agent-1', run_id=None):
    from cognitrix.artifacts import Artifact

    return Artifact(
        id='artifact-1',
        session_id='session-1',
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        storage_key='private/master.png',
        vision_storage_key='private/vision.png',
        thumbnail_storage_key='private/thumbnail.webp',
        mime_type='image/png',
        filename='edited-master.png',
    )


def _png_bytes(*, size=(640, 320), color=(10, 40, 80)):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format='PNG')
    return output.getvalue()


@pytest.fixture
def artifact_store(monkeypatch, tmp_path):
    from cognitrix import artifacts
    from cognitrix.artifacts import Artifact

    rows = {}
    root = tmp_path / 'artifact-root'

    async def save(row):
        if row.id is None:
            object.__setattr__(row, 'id', str(uuid.uuid4()))
        rows[str(row.id)] = row
        return row

    async def get(artifact_id):
        return rows.get(str(artifact_id))

    async def find(query):
        return [
            row for row in rows.values()
            if all(getattr(row, key) == value for key, value in query.items())
        ]

    monkeypatch.setattr(artifacts, '_root', lambda: root)
    monkeypatch.setattr(Artifact, 'save', save)
    monkeypatch.setattr(Artifact, 'get', get)
    monkeypatch.setattr(Artifact, 'find', find)
    return rows, root


async def _legacy_generated(artifact_store):
    from cognitrix.artifacts import Artifact, variant_path
    from cognitrix.media import MediaAssetService, MediaOwnership

    ownership = MediaOwnership('session-1', 'owner', 'agent-1')
    artifact_ref = await MediaAssetService().store_generated_image(
        _png_bytes(), {'filename': 'edited-master.png'}, ownership
    )
    artifact = await Artifact.get(artifact_ref.id)
    thumbnail = variant_path(artifact, 'thumbnail')
    artifact.thumbnail_storage_key = None
    thumbnail.unlink()
    return artifact, ownership


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('variant', 'expected_variant', 'mime_type', 'filename'),
    [
        (None, 'original', 'image/png', 'edited-master.png'),
        ('vision', 'vision', 'image/png', 'edited-master.png'),
        ('thumbnail', 'thumbnail', 'image/webp', 'edited-master.webp'),
    ],
)
async def test_authorized_variants_keep_variant_metadata_and_private_cache_headers(
    monkeypatch, tmp_path, variant, expected_variant, mime_type, filename
):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext
    from cognitrix.media.types import ResolvedMediaFile
    from cognitrix.tools.utils import ArtifactRef

    artifact = _artifact()
    path = tmp_path / filename
    path.write_bytes(b'image')
    calls = []

    async def resolve(artifact_id, ownership, requested_variant):
        calls.append((artifact_id, ownership, requested_variant))
        return ResolvedMediaFile(
            ref=ArtifactRef(id='artifact-1', mime_type=mime_type, filename=filename),
            variant=requested_variant,
            mime_type=mime_type,
            filename=filename,
            path=path,
        )

    monkeypatch.setattr(Artifact, 'get', lambda _id: _async(artifact))
    monkeypatch.setattr(route.media_assets, 'resolve_variant_file', resolve)

    kwargs = {} if variant is None else {'variant': variant}
    response = await route.get_artifact(
        'artifact-1',
        ctx=AuthContext(user=SimpleNamespace(id='owner')),
        **kwargs,
    )

    assert calls[0][2] == expected_variant
    assert response.media_type == mime_type
    assert response.filename == filename
    assert response.headers['cache-control'] == 'private, max-age=86400, immutable'
    assert response.headers['vary'] == 'Authorization'


@pytest.mark.asyncio
async def test_route_hides_other_user_before_variant_resolution(monkeypatch):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext

    monkeypatch.setattr(Artifact, 'get', lambda _id: _async(_artifact()))
    called = False

    async def resolve(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr(route.media_assets, 'resolve_variant_file', resolve)

    with pytest.raises(HTTPException) as captured:
        await route.get_artifact(
            'artifact-1',
            ctx=AuthContext(user=SimpleNamespace(id='other')),
            variant='vision',
        )

    assert captured.value.status_code == 404
    assert called is False


@pytest.mark.asyncio
async def test_route_preserves_agent_allowlist_for_authorized_owner(monkeypatch):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext
    from cognitrix.models.api_key import APIKey

    monkeypatch.setattr(Artifact, 'get', lambda _id: _async(_artifact()))
    key = APIKey(
        _id='limited', name='limited', user_id='owner', key_hash='hash', prefix='ctx',
        scopes=['chat'], allowed_agents=['other-agent'],
    )

    with pytest.raises(HTTPException) as captured:
        await route.get_artifact(
            'artifact-1',
            ctx=AuthContext(user=SimpleNamespace(id='owner'), api_key=key),
        )

    assert captured.value.status_code == 403


@pytest.mark.asyncio
async def test_unknown_variant_is_rejected_before_media_resolution(monkeypatch):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.common.security import AuthContext, get_auth_context

    app = FastAPI()
    app.include_router(route.artifacts_api)
    route_dependency = route.artifacts_api.routes[0].dependant.dependencies[0].call
    app.dependency_overrides[route_dependency] = lambda: None
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user=SimpleNamespace(id='owner')
    )
    called = False

    async def resolve(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr(route.media_assets, 'resolve_variant_file', resolve)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get('/artifacts/artifact-1?variant=outside')

    assert response.status_code == 422
    assert called is False


@pytest.mark.asyncio
async def test_real_route_serves_retained_original_vision_and_thumbnail_variants(
    artifact_store,
):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.artifacts import Artifact, variant_path
    from cognitrix.common.security import AuthContext
    from cognitrix.media import MediaAssetService, MediaOwnership

    ownership = MediaOwnership('session-1', 'owner', 'agent-1')
    artifact_ref = await MediaAssetService().store_generated_image(
        _png_bytes(), {'filename': 'edited-master.png'}, ownership
    )
    ctx = AuthContext(user=SimpleNamespace(id='owner'))

    for variant, mime_type, filename in (
        ('original', 'image/png', 'edited-master.png'),
        ('vision', 'image/png', 'edited-master.png'),
        ('thumbnail', 'image/webp', 'edited-master.webp'),
    ):
        response = await route.get_artifact(
            artifact_ref.id,
            ctx=ctx,
            variant=variant,
        )
        artifact = await Artifact.get(artifact_ref.id)
        assert Path(response.path) == variant_path(
            artifact,
            variant,
        )
        assert Path(response.path).is_file()
        assert response.media_type == mime_type
        assert response.filename == filename


@pytest.mark.asyncio
async def test_legacy_thumbnail_failure_keeps_original_healthy_and_retryable(
    monkeypatch, artifact_store
):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.common.security import AuthContext
    from cognitrix.media import service as service_module

    artifact, _ = await _legacy_generated(artifact_store)
    original_make_thumbnail = service_module._make_thumbnail

    def fail_thumbnail(_data):
        raise OSError('webp unavailable')

    monkeypatch.setattr(service_module, '_make_thumbnail', fail_thumbnail)
    ctx = AuthContext(user=SimpleNamespace(id='owner'))
    with pytest.raises(HTTPException) as failed_thumbnail:
        await route.get_artifact(artifact.id, ctx=ctx, variant='thumbnail')
    assert failed_thumbnail.value.status_code == 404
    assert artifact.thumbnail_storage_key is None

    original = await route.get_artifact(artifact.id, ctx=ctx)
    assert Path(original.path).is_file()
    assert original.media_type == 'image/png'

    monkeypatch.setattr(service_module, '_make_thumbnail', original_make_thumbnail)
    retried = await route.get_artifact(artifact.id, ctx=ctx, variant='thumbnail')
    assert Path(retried.path).is_file()
    assert artifact.thumbnail_storage_key


@pytest.mark.asyncio
async def test_concurrent_legacy_thumbnail_requests_encode_once(
    monkeypatch, artifact_store
):
    from cognitrix.media import MediaAssetService
    from cognitrix.media import service as service_module

    artifact, ownership = await _legacy_generated(artifact_store)
    original_make_thumbnail = service_module._make_thumbnail
    encodes = 0
    guard = threading.Lock()

    def counted_thumbnail(data):
        nonlocal encodes
        with guard:
            encodes += 1
        return original_make_thumbnail(data)

    monkeypatch.setattr(service_module, '_make_thumbnail', counted_thumbnail)
    first, second = await asyncio.gather(
        MediaAssetService().resolve_variant_file(artifact.id, ownership, 'thumbnail'),
        MediaAssetService().resolve_variant_file(artifact.id, ownership, 'thumbnail'),
    )

    assert encodes == 1
    assert first.path == second.path
    assert artifact.thumbnail_storage_key


@pytest.mark.asyncio
async def test_unauthorized_and_missing_artifacts_have_identical_generic_404s(
    artifact_store,
):
    import cognitrix.api.routes.artifacts as route
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext

    artifact = _artifact(user_id='other-owner')
    artifact.id = 'owned-by-other'
    artifact.storage_key = '../private/secret.png'
    artifact_store[0][artifact.id] = artifact
    ctx = AuthContext(user=SimpleNamespace(id='owner'))

    async def response_for(artifact_id):
        with pytest.raises(HTTPException) as captured:
            await route.get_artifact(artifact_id, ctx=ctx)
        return captured.value

    unauthorized, missing = await asyncio.gather(
        response_for('owned-by-other'), response_for('does-not-exist')
    )
    assert (unauthorized.status_code, unauthorized.detail) == (
        missing.status_code,
        missing.detail,
    ) == (404, 'Artifact not found')
