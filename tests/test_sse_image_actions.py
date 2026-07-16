"""Promotion of staged chat attachments into session-owned safe references."""

from __future__ import annotations

import asyncio
import io
import json
import types
from pathlib import Path

import pytest
from PIL import Image

import cognitrix.media.staging as staging
import cognitrix.media.service as media_service
from cognitrix.media import (
    MediaAccessError,
    MediaAssetService,
    MediaOwnership,
    MediaValidationError,
    StagedAttachment,
)
from cognitrix.tools.utils import ArtifactRef
from cognitrix.utils import sse


class Request:
    disconnected = False

    async def is_disconnected(self):
        return self.disconnected


class FakeStaged:
    def __init__(self):
        self.cleanup_calls = 0

    async def cleanup(self):
        self.cleanup_calls += 1


class Upload:
    def __init__(self, name: str, data: bytes, mime: str = 'application/octet-stream'):
        self.filename = name
        self.content_type = mime
        self.data = data
        self.offset = 0

    async def read(self, size: int = -1):
        if self.offset >= len(self.data):
            return b''
        end = len(self.data) if size < 0 else self.offset + size
        chunk = self.data[self.offset:end]
        self.offset += len(chunk)
        return chunk

    async def close(self):
        return None


def _agent(agent_id: str = 'agent-1'):
    return types.SimpleNamespace(id=agent_id, name='Agent', llm=object())


def _ref(artifact_id: str = 'image-1') -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        mime_type='image/png',
        filename='photo.png',
        width=2,
        height=2,
        origin='uploaded',
    )


def _png() -> bytes:
    output = io.BytesIO()
    Image.new('RGB', (2, 2), (255, 0, 0)).save(output, format='PNG')
    return output.getvalue()


async def _next_json(events):
    event = await asyncio.wait_for(anext(events), timeout=1)
    return json.loads(event['data'])


@pytest.mark.asyncio
async def test_resolves_session_and_selected_source_before_promoting_then_emits_safe_refs(
    monkeypatch,
):
    calls = []
    captured = {}
    staged = FakeStaged()
    image_ref = _ref()
    selected_ref = _ref('source-1')

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, prompt, _agent, *, attachments, output, **_kwargs):
            calls.append('session')
            captured['attachments'] = attachments
            await output({'type': 'generate', 'content': 'ok'})

    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()

    async def resolve_session(session_id):
        calls.append(('resolve_session', session_id))
        return Session()

    async def resolve_ref(artifact_id, ownership):
        calls.append(('resolve_source', artifact_id, ownership))
        return selected_ref

    async def promote(value, ownership):
        calls.append(('promote', value, ownership))
        return staging.PromotedAttachments(
            image_refs=[image_ref],
            document_paths=[{'name': 'notes.txt', 'path': 'uploads/doc/notes.txt'}],
        )

    manager._resolve_session = resolve_session
    monkeypatch.setattr(sse.media_assets, 'resolve_ref', resolve_ref)
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    monkeypatch.setattr(sse, 'is_multi_step_task', lambda _prompt: False)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'edit it',
        'session_id': 'session-1',
        'staged_attachments': staged,
        'edit_source_artifact_id': 'source-1',
    })

    response = await manager.sse_endpoint(Request())
    events = response.body_iterator
    ingested = await _next_json(events)

    ownership = MediaOwnership(
        session_id='session-1', user_id='user-1', agent_id='agent-1'
    )
    assert calls[:3] == [
        ('resolve_session', 'session-1'),
        ('resolve_source', 'source-1', ownership),
        ('promote', staged, ownership),
    ]
    assert ingested == {
        'type': 'attachments_ingested',
        'artifacts': [image_ref.model_dump()],
        'document_count': 1,
        'session_id': 'session-1',
    }
    serialized = json.dumps(ingested)
    assert 'uploads/' not in serialized
    assert 'storage_key' not in serialized
    assert 'data:' not in serialized

    assert (await _next_json(events))['type'] == 'generate'
    assert captured['attachments'] == {
        'images': [image_ref.model_dump()],
        'files': [{'name': 'notes.txt', 'path': 'uploads/doc/notes.txt'}],
        'image_selection': selected_ref.model_dump(),
    }
    assert staged.cleanup_calls >= 1
    await events.aclose()


@pytest.mark.asyncio
async def test_resolution_failure_cleans_staging_and_never_promotes(monkeypatch):
    staged = FakeStaged()
    promoted = False
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()

    async def fail_resolve(_session_id):
        raise RuntimeError('C:/secret/database.sqlite')

    async def promote(*_args):
        nonlocal promoted
        promoted = True

    manager._resolve_session = fail_resolve
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })

    response = await manager.sse_endpoint(Request())
    events = response.body_iterator
    error = await _next_json(events)

    assert error['type'] == 'error'
    assert 'secret' not in error['content']
    assert promoted is False
    assert staged.cleanup_calls >= 1
    await events.aclose()


@pytest.mark.asyncio
async def test_session_persists_only_safe_image_and_selection_history(monkeypatch):
    from cognitrix.sessions.base import Session

    session = Session(agent_id='agent-1')
    monkeypatch.setattr(session, 'save', lambda: asyncio.sleep(0))

    class Context:
        async def build_prompt(self, _agent, current):
            raise RuntimeError(current.chat)

    agent = types.SimpleNamespace(
        tools=[],
        process_prompt=lambda prompt: {'role': 'User', 'type': 'text', 'content': prompt},
        get_context_manager=lambda: Context(),
    )
    current = _ref().model_dump()
    selected = _ref('source-1').model_dump()

    with pytest.raises(RuntimeError):
        await session(
            'edit',
            agent,
            save_history=True,
            attachments={
                'images': [current],
                'files': [{'name': 'note.txt', 'path': 'uploads/opaque/note.txt'}],
                'image_selection': selected,
            },
        )

    assert session.chat[1] == {
        'role': 'User',
        'type': 'image',
        'content': '[Current image artifact: image-1]',
        'artifact': current,
    }
    assert session.chat[2] == {
        'role': 'User',
        'type': 'image_selection',
        'content': '[Selected source image artifact: source-1]',
        'artifact': selected,
    }
    assert session.chat[3]['content'].endswith('uploads/opaque/note.txt')
    history = json.dumps(session.chat)
    assert 'C:/' not in history
    assert 'storage_key' not in history
    assert 'data:' not in history


@pytest.mark.asyncio
async def test_media_classifier_ignores_declared_mime_but_rejects_recognized_corruption(
    tmp_path,
):
    service = MediaAssetService()
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    text_path = tmp_path / 'declared-image'
    text_path.write_bytes(b'plain text')

    assert await service.ingest_staged_image_if_recognized(
        StagedAttachment(text_path, 'fake.png', 'image/png', len(b'plain text')),
        ownership,
    ) is None

    corrupt = tmp_path / 'declared-text'
    corrupt.write_bytes(b'\x89PNG\r\n\x1a\ncorrupt')
    with pytest.raises(MediaValidationError):
        await service.ingest_staged_image_if_recognized(
            StagedAttachment(
                corrupt,
                'fake.txt',
                'text/plain',
                corrupt.stat().st_size,
            ),
            ownership,
        )


@pytest.mark.asyncio
async def test_media_classifier_rejects_recognized_image_over_bound_before_decode(
    tmp_path, monkeypatch
):
    path = tmp_path / 'large.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * 8)
    monkeypatch.setattr(media_service, 'MAX_IMAGE_BYTES', 10)

    with pytest.raises(MediaValidationError, match='10 MiB'):
        await MediaAssetService().ingest_staged_image_if_recognized(
            StagedAttachment(path, 'large.png', 'image/png', path.stat().st_size),
            MediaOwnership('session-1', 'user-1', 'agent-1'),
        )


@pytest.mark.asyncio
async def test_promotion_preflights_every_entry_before_any_media_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tmp_path / 'tools')
    staged = await staging.stage_upload_files(
        [Upload('first.png', _png()), Upload('changed.txt', b'ok')],
        user_key='user-1',
        stream_id='browser-1',
    )
    staged.entries[1].path.write_bytes(b'changed')
    calls = []

    async def ingest(*_args):
        calls.append('ingest')

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', ingest
    )

    with pytest.raises(MediaValidationError, match='size changed'):
        await staging.promote_staged_attachments(
            staged, MediaOwnership('session-1', 'user-1', 'agent-1')
        )

    assert calls == []
    assert not staged.batch_dir.exists()


@pytest.mark.asyncio
async def test_second_image_failure_rolls_back_first_artifact_and_staging(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tmp_path / 'tools')
    staged = await staging.stage_upload_files(
        [Upload('one.png', _png()), Upload('two.png', _png())],
        user_key='user-1',
        stream_id='browser-1',
    )
    ingested = []
    deleted = []

    async def ingest(_entry, _ownership):
        if ingested:
            raise MediaValidationError('second image is corrupt')
        ref = _ref('first-image')
        ingested.append(ref)
        return ref

    async def delete(ids, ownership):
        deleted.append((ids, ownership))

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', ingest
    )
    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', delete)
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')

    with pytest.raises(MediaValidationError, match='second image'):
        await staging.promote_staged_attachments(staged, ownership)

    assert deleted == [(['first-image'], ownership)]
    assert not staged.batch_dir.exists()


@pytest.mark.asyncio
async def test_second_document_failure_removes_first_promoted_document(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('one.txt', b'one'), Upload('two.txt', b'two')],
        user_key='user-1',
        stream_id='browser-1',
    )

    async def not_image(*_args):
        return None

    original = staging._copy_document_transaction
    copies = 0

    def fail_second(*args):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise OSError('second copy failed')
        return original(*args)

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', not_image
    )
    monkeypatch.setattr(staging, '_copy_document_transaction', fail_second)

    with pytest.raises(OSError, match='second copy'):
        await staging.promote_staged_attachments(
            staged, MediaOwnership('session-1', 'user-1', 'agent-1')
        )

    files = [item for item in (tools_root / 'uploads').rglob('*') if item.is_file()]
    assert files == []
    assert not staged.batch_dir.exists()


@pytest.mark.asyncio
async def test_stop_before_action_task_first_step_still_cleans_staging():
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })
    assert manager.stop_current_turn()

    response = await manager.sse_endpoint(Request())
    event = await _next_json(response.body_iterator)

    assert event['type'] == 'turn_stopped'
    assert staged.cleanup_calls >= 1
    assert manager.turn_pending is False
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_stop_after_promotion_before_session_rolls_back(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    rolled_back = []

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, **_kwargs):
            pytest.fail('Session must not receive stopped attachments')

    async def resolve(_sid):
        return Session()

    async def promote(_staged, _ownership):
        manager.stop_requested = True
        return staging.PromotedAttachments([_ref()], [])

    async def rollback(value, ownership):
        rolled_back.append((value, ownership))

    manager._resolve_session = resolve
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    monkeypatch.setattr(sse, 'rollback_promoted_attachments', rollback)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })

    response = await manager.sse_endpoint(Request())
    assert (await _next_json(response.body_iterator))['type'] == 'turn_stopped'
    assert len(rolled_back) == 1
    assert staged.cleanup_calls >= 1
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_reconnect_after_dequeue_replays_ingestion_before_turn_output(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    promotion_started = asyncio.Event()
    release = asyncio.Event()

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, output, **_kwargs):
            await output({'type': 'generate', 'content': 'ok'})

    async def resolve(_sid):
        return Session()

    async def promote(_staged, _ownership):
        promotion_started.set()
        await release.wait()
        return staging.PromotedAttachments([_ref()], [])

    manager._resolve_session = resolve
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    monkeypatch.setattr(sse, 'is_multi_step_task', lambda _prompt: False)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })

    first = await manager.sse_endpoint(Request())
    first_next = asyncio.create_task(anext(first.body_iterator))
    await asyncio.wait_for(promotion_started.wait(), timeout=1)
    second = await manager.sse_endpoint(Request())
    second_next = asyncio.create_task(anext(second.body_iterator))
    release.set()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(first_next, timeout=1)
    assert (await asyncio.wait_for(second_next, timeout=1))['event'] == 'message'
    # The first manager-owned item is the safe ingestion acknowledgement.
    first_payload = json.loads(second_next.result()['data'])
    assert first_payload['type'] == 'attachments_ingested'
    assert (await _next_json(second.body_iterator))['type'] == 'generate'
    await second.body_iterator.aclose()


@pytest.mark.asyncio
async def test_attachment_multistep_prompt_uses_session_persistence(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    called = []

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, attachments, **_kwargs):
            called.append(attachments)

    async def resolve(_sid):
        return Session()

    async def promote(_staged, _ownership):
        return staging.PromotedAttachments([_ref()], [])

    async def forbidden_multistep(*_args, **_kwargs):
        pytest.fail('Attachment-bearing prompts must persist through Session')

    manager._resolve_session = resolve
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    monkeypatch.setattr(sse, 'is_multi_step_task', lambda _prompt: True)
    monkeypatch.setattr(sse, 'handle_multi_step_task', forbidden_multistep)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'first do this, then do that',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })

    response = await manager.sse_endpoint(Request())
    assert (await _next_json(response.body_iterator))['type'] == 'attachments_ingested'
    assert (await _next_json(response.body_iterator))['type'] == 'turn_complete'
    assert called and called[0]['images'][0]['id'] == 'image-1'
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_selected_source_access_failure_is_generic_and_precedes_promotion(
    monkeypatch,
):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    promoted = False

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

    async def resolve(_sid):
        return Session()

    async def deny(_artifact_id, _ownership):
        raise MediaAccessError('C:/private/artifact owner=user-2')

    async def promote(*_args):
        nonlocal promoted
        promoted = True

    manager._resolve_session = resolve
    monkeypatch.setattr(sse.media_assets, 'resolve_ref', deny)
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'edit',
        'session_id': 'session-1',
        'staged_attachments': staged,
        'edit_source_artifact_id': 'other-owner',
    })

    response = await manager.sse_endpoint(Request())
    error = await _next_json(response.body_iterator)
    assert error['type'] == 'error'
    assert error['content'] == sse._ATTACHMENT_UNAVAILABLE
    assert 'private' not in json.dumps(error)
    assert promoted is False
    assert staged.cleanup_calls >= 1
    await response.body_iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('session_value', [None, types.SimpleNamespace(
    id='session-1', agent_id='wrong-agent'
)])
async def test_stale_or_wrong_agent_session_cleans_staging(
    monkeypatch, session_value
):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()

    async def resolve(_sid):
        return session_value

    manager._resolve_session = resolve
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })
    response = await manager.sse_endpoint(Request())
    error = await _next_json(response.body_iterator)
    assert error == {
        'type': 'error',
        'content': sse._ATTACHMENT_UNAVAILABLE,
        'session_id': 'session-1',
    }
    assert staged.cleanup_calls >= 1
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_task_cancellation_during_promotion_cleans_staging(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    started = asyncio.Event()

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

    async def resolve(_sid):
        return Session()

    async def blocked_promotion(*_args):
        started.set()
        await asyncio.Event().wait()

    manager._resolve_session = resolve
    monkeypatch.setattr(sse, 'promote_staged_attachments', blocked_promotion)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })
    response = await manager.sse_endpoint(Request())
    pending = asyncio.create_task(anext(response.body_iterator))
    await asyncio.wait_for(started.wait(), timeout=1)

    assert manager.stop_current_turn()
    stopped = json.loads((await asyncio.wait_for(pending, timeout=1))['data'])
    assert stopped['type'] == 'turn_stopped'
    assert staged.cleanup_calls >= 1
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_http_generator_cancellation_does_not_orphan_dequeued_staging(
    monkeypatch,
):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    started = asyncio.Event()
    release = asyncio.Event()

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, **_kwargs):
            return None

    async def resolve(_sid):
        return Session()

    async def promote(*_args):
        started.set()
        await release.wait()
        return staging.PromotedAttachments([_ref()], [])

    manager._resolve_session = resolve
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    monkeypatch.setattr(sse, 'is_multi_step_task', lambda _prompt: False)
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })
    response = await manager.sse_endpoint(Request())
    pending = asyncio.create_task(anext(response.body_iterator))
    await asyncio.wait_for(started.wait(), timeout=1)
    active = manager.active_task
    assert active is not None

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert active.cancelled() is False
    release.set()
    await asyncio.wait_for(active, timeout=1)
    assert staged.cleanup_calls >= 1
    assert manager.turn_pending is False


@pytest.mark.asyncio
async def test_promoted_image_is_deleted_when_later_document_copy_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tmp_path / 'tools')
    staged = await staging.stage_upload_files(
        [Upload('image.bin', b'image'), Upload('document.txt', b'document')],
        user_key='user-1',
        stream_id='browser-1',
    )
    calls = 0
    deleted = []

    async def classify(_entry, _ownership):
        nonlocal calls
        calls += 1
        return _ref('created-image') if calls == 1 else None

    async def delete(ids, ownership):
        deleted.append((ids, ownership))

    def fail_copy(*_args):
        raise OSError('document destination failed')

    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', classify
    )
    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', delete)
    monkeypatch.setattr(staging, '_copy_document_transaction', fail_copy)

    with pytest.raises(OSError, match='destination'):
        await staging.promote_staged_attachments(staged, ownership)
    assert deleted == [(['created-image'], ownership)]
    assert not staged.batch_dir.exists()


@pytest.mark.asyncio
async def test_cancelling_document_rollback_waits_for_unlink_to_finish(
    tmp_path, monkeypatch
):
    tools_root = tmp_path / 'tools'
    target = tools_root / 'uploads' / 'opaque' / 'note.txt'
    target.parent.mkdir(parents=True)
    target.write_text('note')
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    started = __import__('threading').Event()
    release = __import__('threading').Event()
    original = Path.unlink

    def blocking_unlink(path, *args, **kwargs):
        if path == target:
            started.set()
            release.wait(timeout=5)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', blocking_unlink)
    task = asyncio.create_task(staging.rollback_promoted_attachments(
        staging.PromotedAttachments(
            image_refs=[],
            document_paths=[{'name': 'note.txt', 'path': 'uploads/opaque/note.txt'}],
        ),
        MediaOwnership('session-1', 'user-1', 'agent-1'),
    ))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not target.exists()


@pytest.mark.asyncio
async def test_session_ignores_document_paths_outside_promoted_upload_namespace(
    monkeypatch,
):
    from cognitrix.sessions.base import Session

    session = Session(agent_id='agent-1')

    class Context:
        async def build_prompt(self, _agent, current):
            raise RuntimeError(current.chat)

    agent = types.SimpleNamespace(
        tools=[],
        process_prompt=lambda prompt: {'role': 'User', 'type': 'text', 'content': prompt},
        get_context_manager=lambda: Context(),
    )
    with pytest.raises(RuntimeError):
        await session(
            'inspect',
            agent,
            attachments={
                'files': [
                    {'path': 'secrets.txt'},
                    {'path': 'uploads/../secrets.txt'},
                    {'path': 'C:secrets.txt'},
                    {'path': '/absolute/secrets.txt'},
                ],
            },
        )
    serialized = json.dumps(session.chat)
    assert 'secrets.txt' not in serialized
