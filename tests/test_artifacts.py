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

    monkeypatch.setattr(artifacts.Artifact, 'save', save)
    token = set_execution_context(
        ToolExecutionContext(user_id='user-1', run_id='run-immutable')
    )
    try:
        artifact = await artifacts.store_png(
            b'image', session_id='task-session', user_id='user-1'
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

    async def save(self):
        return self

    monkeypatch.setattr(artifacts.Artifact, 'save', save)
    token = set_execution_context(ToolExecutionContext(
        user_id='durable-owner',
        task_id='task-1',
        run_id='durable-run',
    ))
    try:
        artifact = await artifacts.store_png(
            b'image',
            session_id='task-session',
            run_id='caller-selected-run',
            user_id='caller-selected-user',
        )
    finally:
        reset_execution_context(token)

    assert artifact.run_id == 'durable-run'
    assert artifact.user_id == 'durable-owner'


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

    artifact = artifacts.Artifact(
        id='image-1',
        session_id='upstream-session',
        run_id='run-1',
        user_id='owner',
        storage_key='safe/image.png',
        size_bytes=5,
    )
    (tmp_path / 'safe').mkdir()
    (tmp_path / 'safe' / 'image.png').write_bytes(b'image')
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
    assert data == b'image'


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
    (tmp_path / 'safe' / 'image.png').write_bytes(b'image')
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
    monkeypatch.setattr(routes, 'absolute_path', lambda _artifact: path)

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
    monkeypatch.setattr(routes, 'absolute_path', lambda _artifact: path)

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
