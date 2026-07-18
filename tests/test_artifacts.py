import io
from datetime import datetime, timezone

import pytest
from PIL import Image


def _png_bytes(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (2, 2), color).save(output, format='PNG')
    return output.getvalue()


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
async def test_task_artifact_captures_immutable_run_provenance(monkeypatch, tmp_path):
    from cognitrix import artifacts
    from cognitrix.tools.utils import (
        ToolExecutionContext,
        reset_execution_context,
        set_execution_context,
    )

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    saved = []

    async def save(self):
        saved.append(self)
        return self

    async def get(artifact_id):
        return next((item for item in saved if str(item.id) == str(artifact_id)), None)

    monkeypatch.setattr(artifacts.Artifact, 'save', save)
    monkeypatch.setattr(artifacts.Artifact, 'get', get)
    monkeypatch.setattr(artifacts.Artifact, 'find', lambda _query: _async([]))
    token = set_execution_context(
        ToolExecutionContext(user_id='user-1', run_id='run-immutable')
    )
    try:
        artifact = await artifacts.store_png(
            _png_bytes(), session_id='task-session', user_id='user-1'
        )
    finally:
        reset_execution_context(token)

    assert artifact.run_id == 'run-immutable'
    assert saved[0].run_id == 'run-immutable'


@pytest.mark.asyncio
async def test_task_artifact_context_cannot_be_overridden_by_caller(monkeypatch, tmp_path):
    from cognitrix import artifacts
    from cognitrix.tools.utils import (
        ToolExecutionContext,
        reset_execution_context,
        set_execution_context,
    )

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)

    saved = []

    async def save(self):
        saved.append(self)
        return self

    async def get(artifact_id):
        return next((item for item in saved if str(item.id) == str(artifact_id)), None)

    monkeypatch.setattr(artifacts.Artifact, 'save', save)
    monkeypatch.setattr(artifacts.Artifact, 'get', get)
    monkeypatch.setattr(artifacts.Artifact, 'find', lambda _query: _async([]))
    token = set_execution_context(ToolExecutionContext(
        user_id='durable-owner',
        task_id='task-1',
        run_id='durable-run',
    ))
    try:
        artifact = await artifacts.store_png(
            _png_bytes(),
            session_id='task-session',
            run_id='caller-selected-run',
            user_id='caller-selected-user',
        )
    finally:
        reset_execution_context(token)

    assert artifact.run_id == 'durable-run'
    assert artifact.user_id == 'durable-owner'


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
                _id=f'{origin}-artifact',
                session_id=session_id,
                user_id='owner',
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
    await artifacts.delete_session_artifacts(session_id, user_id='owner')

    assert not any(path.exists() for path in stored_paths)
    assert deleted == [
        {'id': 'uploaded-artifact'},
        {'id': 'generated-artifact'},
    ]


@pytest.mark.asyncio
async def test_owned_cleanup_requires_exact_rotated_lifecycle_authority(monkeypatch):
    from cognitrix import artifacts
    from cognitrix.media.documents import document_assets
    from cognitrix.media.service import media_assets
    from cognitrix.media.types import MediaOwnership
    from cognitrix.session_ownership import OwnershipState, session_ownerships

    calls = []

    async def require_owned(session_id, user_id, agent_id):
        calls.append(('authorize', session_id, user_id, agent_id))
        return type('Binding', (), {
            'generation': 4,
            'state': OwnershipState.CLEARING,
        })()

    async def delete_media(session_id, ownership):
        calls.append(('images', session_id, ownership))

    async def delete_documents(ownership):
        calls.append(('documents', ownership))

    monkeypatch.setattr(session_ownerships, 'require_owned', require_owned)
    monkeypatch.setattr(media_assets, 'delete_session_media', delete_media)
    monkeypatch.setattr(document_assets, 'delete_session_documents', delete_documents)
    ownership = MediaOwnership('session', 'user', 'agent')

    await artifacts.delete_owned_session_artifacts(
        session_id='session',
        user_id='user',
        agent_id='agent',
        generation=4,
    )

    assert calls == [
        ('authorize', 'session', 'user', 'agent'),
        ('images', 'session', ownership),
        ('documents', ownership),
    ]


@pytest.mark.asyncio
async def test_owned_cleanup_rejects_stale_generation_before_mutation(monkeypatch):
    from cognitrix import artifacts
    from cognitrix.media.service import media_assets
    from cognitrix.session_ownership import (
        OwnershipConflict,
        OwnershipState,
        session_ownerships,
    )

    async def require_owned(*_args):
        return type('Binding', (), {
            'generation': 5,
            'state': OwnershipState.DELETING,
        })()

    monkeypatch.setattr(session_ownerships, 'require_owned', require_owned)
    monkeypatch.setattr(
        media_assets,
        'delete_session_media',
        lambda *_args: pytest.fail('stale cleanup must not mutate artifacts'),
    )

    with pytest.raises(OwnershipConflict, match='stale'):
        await artifacts.delete_owned_session_artifacts(
            session_id='session',
            user_id='user',
            agent_id='agent',
            generation=4,
        )


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


@pytest.mark.asyncio
async def test_source_image_allows_same_task_run_across_ephemeral_sessions(
    monkeypatch,
    tmp_path,
):
    from types import SimpleNamespace

    from cognitrix import artifacts
    from cognitrix.tools.utils import (
        ToolExecutionContext,
        reset_execution_context,
        set_execution_context,
    )

    image_data = _png_bytes()
    artifact = artifacts.Artifact(
        id='image-1',
        session_id='upstream-session',
        run_id='run-1',
        user_id='owner',
        storage_key='safe/image.png',
        size_bytes=len(image_data),
    )
    (tmp_path / 'safe').mkdir()
    (tmp_path / 'safe' / 'image.png').write_bytes(image_data)
    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(
        artifacts.Artifact,
        'get',
        lambda artifact_id: _async(artifact),
    )
    monkeypatch.setattr(
        'cognitrix.tasks.run.TaskRun.get',
        lambda run_id: _async(SimpleNamespace(id=run_id, task_id='task-1')),
    )
    token = set_execution_context(ToolExecutionContext(
        user_id='owner',
        task_id='task-1',
        run_id='run-1',
    ))
    try:
        loaded, data = await artifacts.source_image(
            'image-1',
            session_id='downstream-session',
        )
    finally:
        reset_execution_context(token)

    assert loaded is artifact
    assert data == image_data


@pytest.mark.asyncio
async def test_source_image_denies_cross_run_and_cross_user_reads(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from cognitrix import artifacts
    from cognitrix.tools.utils import (
        ToolExecutionContext,
        reset_execution_context,
        set_execution_context,
    )

    artifact = artifacts.Artifact(
        id='image-1',
        session_id='upstream-session',
        run_id='run-1',
        user_id='owner',
        storage_key='safe/image.png',
    )
    (tmp_path / 'safe').mkdir()
    (tmp_path / 'safe' / 'image.png').write_bytes(_png_bytes())
    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(
        artifacts.Artifact,
        'get',
        lambda artifact_id: _async(artifact),
    )
    monkeypatch.setattr(
        'cognitrix.tasks.run.TaskRun.get',
        lambda run_id: _async(SimpleNamespace(id=run_id, task_id='task-1')),
    )

    token = set_execution_context(ToolExecutionContext(
        user_id='owner',
        task_id='task-1',
        run_id='run-2',
    ))
    try:
        with pytest.raises(ValueError, match='different task run'):
            await artifacts.source_image('image-1', session_id='downstream')
    finally:
        reset_execution_context(token)

    token = set_execution_context(ToolExecutionContext(
        user_id='other',
        task_id='task-1',
        run_id='run-1',
    ))
    try:
        with pytest.raises(ValueError, match='different user'):
            await artifacts.source_image('image-1', session_id='downstream')
    finally:
        reset_execution_context(token)


@pytest.mark.asyncio
async def test_source_image_preserves_same_session_chat_access(monkeypatch, tmp_path):
    from cognitrix import artifacts

    artifact = artifacts.Artifact(
        id='chat-image',
        session_id='chat-session',
        user_id='owner',
        storage_key='safe/chat.png',
    )
    (tmp_path / 'safe').mkdir()
    (tmp_path / 'safe' / 'chat.png').write_bytes(b'chat')
    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(
        artifacts.Artifact,
        'get',
        lambda artifact_id: _async(artifact),
    )

    loaded, data = await artifacts.source_image(
        'chat-image',
        session_id='chat-session',
        user_id='owner',
    )

    assert loaded is artifact
    assert data == b'chat'


@pytest.mark.asyncio
async def test_source_image_denies_task_artifact_without_task_run_context(
    monkeypatch,
    tmp_path,
):
    from cognitrix import artifacts

    artifact = artifacts.Artifact(
        id='task-image',
        session_id='guessed-session',
        run_id='run-1',
        user_id='owner',
        storage_key='safe/task.png',
    )
    (tmp_path / 'safe').mkdir()
    (tmp_path / 'safe' / 'task.png').write_bytes(b'task')
    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(
        artifacts.Artifact,
        'get',
        lambda artifact_id: _async(artifact),
    )

    with pytest.raises(ValueError, match='different task run'):
        await artifacts.source_image(
            'task-image',
            session_id='guessed-session',
            user_id='owner',
        )


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


@pytest.mark.asyncio
async def test_restricted_key_cannot_bypass_run_acl_through_generic_artifact_route(
    monkeypatch,
    tmp_path,
):
    from types import SimpleNamespace

    from fastapi import HTTPException

    import cognitrix.api.routes.artifacts as routes
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext
    from cognitrix.models.api_key import APIKey

    artifact = Artifact(
        _id='known-artifact-uuid',
        session_id='ephemeral-run-session',
        run_id='private-run',
        user_id='owner',
        # The per-artifact agent is deliberately allowed. The immutable run
        # can still require a private team or additional private agents.
        agent_id='agent-public',
        storage_key='safe/task.png',
        filename='task.png',
    )
    path = tmp_path / 'task.png'
    path.write_bytes(b'task artifact')
    key = APIKey(
        _id='restricted-key',
        name='restricted',
        user_id='owner',
        key_hash='hash',
        prefix='ctx_test',
        scopes=['chat'],
        allowed_agents=['agent-public'],
        allowed_teams=['team-public'],
    )
    monkeypatch.setattr(Artifact, 'get', lambda artifact_id: _async(artifact))
    with pytest.raises(HTTPException) as caught:
        await routes.get_artifact(
            artifact.id,
            AuthContext(user=SimpleNamespace(id='owner'), api_key=key),
        )

    # Durable artifacts are available only through the task-run URL, whose
    # immutable ACL and persisted result reference are both verified.
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_generic_artifact_route_keeps_serving_owned_chat_artifacts(
    monkeypatch,
    tmp_path,
):
    from pathlib import Path
    from types import SimpleNamespace

    import cognitrix.api.routes.artifacts as routes
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext
    from cognitrix.media.types import ResolvedMediaFile
    from cognitrix.tools.utils import ArtifactRef

    artifact = Artifact(
        _id='chat-artifact',
        session_id='owned-chat',
        user_id='owner',
        storage_key='safe/chat.png',
        filename='chat.png',
    )
    path = tmp_path / 'chat.png'
    path.write_bytes(b'chat artifact')
    monkeypatch.setattr(Artifact, 'get', lambda artifact_id: _async(artifact))
    async def resolve(*_args):
        return ResolvedMediaFile(
            ref=ArtifactRef(id=str(artifact.id), mime_type='image/png'),
            variant='original',
            mime_type='image/png',
            filename='chat.png',
            path=path,
        )

    monkeypatch.setattr(routes.media_assets, 'resolve_variant_file', resolve)

    response = await routes.get_artifact(
        artifact.id,
        AuthContext(user=SimpleNamespace(id='owner')),
    )

    assert Path(response.path) == path


@pytest.mark.asyncio
async def test_session_cleanup_cannot_delete_durable_artifacts_with_matching_id(
    monkeypatch,
    tmp_path,
):
    from cognitrix import artifacts

    session_id = 'forged-ephemeral-session'
    ordinary = artifacts.Artifact(
        _id='chat-artifact',
        session_id=session_id,
        user_id='owner',
        storage_key='shared/chat.png',
    )
    other_user = artifacts.Artifact(
        _id='other-chat-artifact',
        session_id=session_id,
        user_id='other-owner',
        storage_key='shared/other.png',
    )
    legacy = artifacts.Artifact(
        _id='legacy-chat-artifact',
        session_id=session_id,
        storage_key='shared/legacy.png',
    )
    durable = artifacts.Artifact(
        _id='task-artifact',
        session_id=session_id,
        run_id='private-run',
        user_id='owner',
        storage_key='shared/task.png',
    )
    directory = tmp_path / 'shared'
    directory.mkdir()
    chat_path = directory / 'chat.png'
    other_path = directory / 'other.png'
    legacy_path = directory / 'legacy.png'
    task_path = directory / 'task.png'
    chat_path.write_bytes(b'chat')
    other_path.write_bytes(b'other')
    legacy_path.write_bytes(b'legacy')
    task_path.write_bytes(b'task')
    rows = [ordinary, other_user, legacy, durable]

    def matches(row, query):
        return all(getattr(row, key) == value for key, value in query.items())

    async def find(query):
        return [row for row in rows if matches(row, query)]

    async def delete_many(query):
        rows[:] = [row for row in rows if not matches(row, query)]

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(artifacts.Artifact, 'find', staticmethod(find))
    monkeypatch.setattr(artifacts.Artifact, 'delete_many', staticmethod(delete_many))

    await artifacts.delete_session_artifacts(session_id, user_id='owner')

    assert not chat_path.exists()
    assert other_path.read_bytes() == b'other'
    assert legacy_path.read_bytes() == b'legacy'
    assert task_path.read_bytes() == b'task'
    assert rows == [other_user, legacy, durable]


@pytest.mark.asyncio
async def test_session_cleanup_fails_closed_without_an_owner(monkeypatch, tmp_path):
    from cognitrix import artifacts

    artifact = artifacts.Artifact(
        _id='legacy-chat-artifact',
        session_id='legacy-session',
        storage_key='safe/legacy.png',
    )
    path = tmp_path / 'safe' / 'legacy.png'
    path.parent.mkdir()
    path.write_bytes(b'legacy')
    deleted = False

    async def find(_query):
        return [artifact]

    async def delete_many(_query):
        nonlocal deleted
        deleted = True

    monkeypatch.setattr(artifacts, '_root', lambda: tmp_path)
    monkeypatch.setattr(artifacts.Artifact, 'find', staticmethod(find))
    monkeypatch.setattr(artifacts.Artifact, 'delete_many', staticmethod(delete_many))

    await artifacts.delete_session_artifacts('legacy-session', user_id=None)

    assert path.read_bytes() == b'legacy'
    assert deleted is False


@pytest.mark.asyncio
async def test_artifact_route_resolves_requested_thumbnail_with_exact_ownership(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from cognitrix.api.routes import artifacts as route
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext
    from cognitrix.media.types import MediaOwnership, ResolvedMediaFile
    from cognitrix.tools.utils import ArtifactRef

    artifact = Artifact(
        id='x',
        session_id='session-1',
        user_id='owner',
        agent_id='agent-1',
        storage_key='safe/master.png',
    )
    thumbnail = tmp_path / 'thumb.webp'
    thumbnail.write_bytes(b'thumbnail')
    calls = []

    async def resolve(artifact_id, ownership, variant):
        calls.append((artifact_id, ownership, variant))
        return ResolvedMediaFile(
            ref=ArtifactRef(id='x', mime_type='image/png'),
            variant='thumbnail',
            mime_type='image/webp',
            filename='preview.webp',
            path=thumbnail,
        )

    monkeypatch.setattr(Artifact, 'get', lambda _artifact_id: _async(artifact))
    monkeypatch.setattr(route.media_assets, 'resolve_variant_file', resolve)

    response = await route.get_artifact(
        'x',
        variant='thumbnail',
        ctx=AuthContext(user=SimpleNamespace(id='owner')),
    )

    assert calls == [(
        'x',
        MediaOwnership('session-1', 'owner', 'agent-1'),
        'thumbnail',
    )]
    assert response.path == thumbnail
    assert response.media_type == 'image/webp'
    assert response.filename == 'preview.webp'


@pytest.mark.asyncio
async def test_artifact_route_hides_missing_variant_without_path_exposure(
    monkeypatch,
):
    from types import SimpleNamespace
    from fastapi import HTTPException

    from cognitrix.api.routes import artifacts as route
    from cognitrix.artifacts import Artifact
    from cognitrix.common.security import AuthContext
    from cognitrix.media.types import MediaNotFoundError

    artifact = Artifact(
        id='x',
        session_id='session-1',
        user_id='owner',
        agent_id='agent-1',
        storage_key='private/master.png',
    )
    monkeypatch.setattr(Artifact, 'get', lambda _artifact_id: _async(artifact))

    async def missing(*_args):
        raise MediaNotFoundError('private/master.png is missing')

    monkeypatch.setattr(route.media_assets, 'resolve_variant_file', missing)

    with pytest.raises(HTTPException) as caught:
        await route.get_artifact(
            'x',
            variant='vision',
            ctx=AuthContext(user=SimpleNamespace(id='owner')),
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == 'Artifact not found'
