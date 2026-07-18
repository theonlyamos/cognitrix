from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException


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
