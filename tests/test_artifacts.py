import io
from datetime import datetime, timezone

import pytest
from PIL import Image


def test_artifact_declares_persistent_media_metadata():
    from cognitrix.artifacts import Artifact

    assert {
        'origin',
        'vision_storage_key',
        'thumbnail_storage_key',
        'created_at',
    } <= Artifact.model_fields.keys()
    assert Artifact.model_fields['created_at'].annotation == str | None

    artifact = Artifact(
        session_id='session',
        storage_key='safe/original.png',
        origin='uploaded',
        vision_storage_key='safe/vision.png',
        thumbnail_storage_key='safe/thumbnail.png',
    )

    assert artifact.origin == 'uploaded'
    assert artifact.vision_storage_key == 'safe/vision.png'
    assert artifact.thumbnail_storage_key == 'safe/thumbnail.png'
    created_at = datetime.fromisoformat(artifact.created_at)
    assert created_at.utcoffset() == timezone.utc.utcoffset(created_at)


def test_artifact_ref_exposes_only_safe_transport_fields():
    from cognitrix.artifacts import Artifact, ref

    artifact = Artifact(
        _id='artifact',
        session_id='session',
        agent_id='agent',
        user_id='user',
        storage_key='safe/original.png',
        vision_storage_key='safe/vision.png',
        thumbnail_storage_key='safe/thumbnail.png',
        origin='uploaded',
        mime_type='image/png',
        filename='image.png',
        width=320,
        height=200,
    )

    assert ref(artifact).model_dump() == {
        'id': 'artifact',
        'mime_type': 'image/png',
        'filename': 'image.png',
        'width': 320,
        'height': 200,
        'origin': 'uploaded',
    }


@pytest.mark.parametrize('legacy_origin', [None, ''])
def test_artifact_ref_defaults_legacy_origin_to_generated(legacy_origin):
    from cognitrix.artifacts import Artifact, ref

    artifact = Artifact(
        id='artifact',
        session_id='session',
        storage_key='safe/original.png',
        origin=legacy_origin,
    )

    assert ref(artifact).origin == 'generated'


def test_variant_path_resolves_only_known_artifact_variants(monkeypatch, tmp_path):
    from cognitrix import artifacts

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    artifact = artifacts.Artifact(
        session_id='session',
        storage_key='safe/original.png',
        vision_storage_key='safe/vision.png',
        thumbnail_storage_key='safe/thumbnail.png',
    )

    assert artifacts.variant_path(artifact, 'original') == (tmp_path / 'safe/original.png').resolve()
    assert artifacts.variant_path(artifact, 'vision') == (tmp_path / 'safe/vision.png').resolve()
    assert artifacts.variant_path(artifact, 'thumbnail') == (tmp_path / 'safe/thumbnail.png').resolve()
    assert artifacts.absolute_path(artifact) == artifacts.variant_path(artifact, 'original')


@pytest.mark.parametrize(
    ('variant', 'storage_key'),
    [
        ('original', '../outside.png'),
        ('vision', None),
        ('thumbnail', None),
    ],
)
def test_variant_path_rejects_unsafe_or_missing_keys(monkeypatch, tmp_path, variant, storage_key):
    from cognitrix import artifacts

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    absolute_key = str((tmp_path.parent / 'outside.png').resolve())
    artifact = artifacts.Artifact(
        session_id='session',
        storage_key=storage_key if variant == 'original' else 'safe/original.png',
        vision_storage_key=absolute_key if variant == 'vision' else 'safe/vision.png',
        thumbnail_storage_key=storage_key if variant == 'thumbnail' else 'safe/thumbnail.png',
    )

    with pytest.raises(ValueError, match='Invalid artifact storage key'):
        artifacts.variant_path(artifact, variant)


def test_variant_path_rejects_unknown_variant(monkeypatch, tmp_path):
    from cognitrix import artifacts

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    artifact = artifacts.Artifact(session_id='session', storage_key='safe/original.png')

    with pytest.raises(ValueError, match='Invalid artifact variant'):
        artifacts.variant_path(artifact, 'preview')


@pytest.mark.asyncio
async def test_artifact_session_ids_remain_exact_and_collision_free(monkeypatch, tmp_path):
    from cognitrix import artifacts

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    saved = []

    async def save(self):
        if self.id is None:
            object.__setattr__(self, 'id', f'artifact-{len(saved) + 1}')
        saved.append(self)
        return self

    async def get(artifact_id):
        return next((item for item in saved if str(item.id) == artifact_id), None)

    async def find(query):
        return [
            item
            for item in saved
            if all(getattr(item, key) == value for key, value in query.items())
        ]

    monkeypatch.setattr(artifacts.Artifact, 'save', save)
    monkeypatch.setattr(artifacts.Artifact, 'get', get)
    monkeypatch.setattr(artifacts.Artifact, 'find', find)

    def png(color):
        output = io.BytesIO()
        Image.new('RGB', (2, 2), color).save(output, format='PNG')
        return output.getvalue()

    first = await artifacts.store_png(png('red'), session_id='a.b', user_id='u1')
    second = await artifacts.store_png(png('blue'), session_id='ab', user_id='u1')
    assert first.session_id == 'a.b'
    assert second.session_id == 'ab'
    assert first.storage_key.split('/')[0] != second.storage_key.split('/')[0]
    assert first.user_id == 'u1'


@pytest.mark.asyncio
async def test_delete_session_artifacts_removes_all_uploaded_and_generated_variants(
    monkeypatch, tmp_path
):
    from cognitrix import artifacts

    session_id = 'session'
    namespace = artifacts._storage_namespace(session_id)
    keys = {
        'uploaded': (
            f'{namespace}/uploaded-original.png',
            f'{namespace}/uploaded-vision.png',
            f'{namespace}/uploaded-thumbnail.png',
        ),
        'generated': (
            f'{namespace}/generated-original.png',
            f'{namespace}/generated-vision.png',
            f'{namespace}/generated-thumbnail.png',
        ),
    }
    stored_paths = []
    rows = []
    for origin, (original, vision, thumbnail) in keys.items():
        rows.append(
            artifacts.Artifact(
                session_id=session_id,
                storage_key=original,
                vision_storage_key=vision,
                thumbnail_storage_key=thumbnail,
                origin=origin,
            )
        )
        for key in (original, vision, thumbnail):
            path = tmp_path / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'image')
            stored_paths.append(path)

    deleted = []

    async def delete_many(query):
        deleted.append(query)

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(artifacts.Artifact, 'find', lambda query: _async(rows))
    monkeypatch.setattr(artifacts.Artifact, 'delete_many', delete_many)

    await artifacts.delete_session_artifacts(session_id)

    assert not any(path.exists() for path in stored_paths)
    assert deleted == [{'session_id': session_id}]


@pytest.mark.asyncio
async def test_source_image_requires_exact_session_and_owner(monkeypatch, tmp_path):
    from cognitrix import artifacts

    artifact = artifacts.Artifact(
        id='x', session_id='a.b', user_id='u1', storage_key='safe/x.png', size_bytes=1
    )
    (tmp_path / 'safe').mkdir()
    (tmp_path / 'safe' / 'x.png').write_bytes(b'x')
    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(artifacts.Artifact, 'get', lambda artifact_id: _async(artifact))

    with pytest.raises(ValueError, match='different session'):
        await artifacts.source_image('x', session_id='ab', user_id='u1')
    with pytest.raises(ValueError, match='different user'):
        await artifacts.source_image('x', session_id='a.b', user_id='u2')


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_artifact_route_hides_other_users_artifacts(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from cognitrix.api.routes.artifacts import get_artifact
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext

    artifact = Artifact(
        id='x', session_id='s', user_id='owner', agent_id='a', storage_key='safe/x.png'
    )
    monkeypatch.setattr(Artifact, 'get', lambda artifact_id: _async(artifact))
    with pytest.raises(HTTPException) as caught:
        await get_artifact('x', AuthContext(user=SimpleNamespace(id='other')))
    assert caught.value.status_code == 404
