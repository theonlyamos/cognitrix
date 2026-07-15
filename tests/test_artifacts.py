import pytest


@pytest.mark.asyncio
async def test_artifact_session_ids_remain_exact_and_collision_free(monkeypatch, tmp_path):
    from cognitrix import artifacts

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    saved = []
    async def save(self):
        saved.append(self)
        return self
    monkeypatch.setattr(artifacts.Artifact, 'save', save)

    first = await artifacts.store_png(b'a', session_id='a.b', user_id='u1')
    second = await artifacts.store_png(b'b', session_id='ab', user_id='u1')
    assert first.session_id == 'a.b'
    assert second.session_id == 'ab'
    assert first.storage_key.split('/')[0] != second.storage_key.split('/')[0]
    assert first.user_id == 'u1'


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
