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
            captured['attachments'] = {
                key: value for key, value in attachments.items()
                if not key.startswith('_')
            }
            attachments['_on_adopted']()
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
async def test_session_acknowledges_adoption_only_after_history_save(monkeypatch):
    from cognitrix.sessions.base import Session

    session = Session(agent_id='agent-1')
    order = []

    async def save(_self):
        order.append('save-started')
        await asyncio.sleep(0)
        order.append('save-complete')

    monkeypatch.setattr(Session, 'save', save)

    class Context:
        async def build_prompt(self, _agent, _current):
            raise RuntimeError('stop after adoption')

    agent = types.SimpleNamespace(
        tools=[],
        process_prompt=lambda prompt: {
            'role': 'User', 'type': 'text', 'content': prompt,
        },
        get_context_manager=lambda: Context(),
    )
    with pytest.raises(RuntimeError, match='stop after adoption'):
        await session(
            'edit',
            agent,
            attachments={
                'images': [_ref().model_dump()],
                '_on_adopted': lambda: order.append('adopted'),
            },
        )

    assert order == ['save-started', 'save-complete', 'adopted']


@pytest.mark.asyncio
async def test_session_cancellation_during_history_save_settles_adoption(monkeypatch):
    from cognitrix.sessions.base import Session

    session = Session(agent_id='agent-1')
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    adopted = []

    async def save(_self):
        save_started.set()
        await release_save.wait()

    monkeypatch.setattr(Session, 'save', save)
    agent = types.SimpleNamespace(
        tools=[],
        process_prompt=lambda prompt: {
            'role': 'User', 'type': 'text', 'content': prompt,
        },
        get_context_manager=lambda: pytest.fail('cancel must propagate before prompt'),
    )
    task = asyncio.create_task(session(
        'edit',
        agent,
        attachments={
            'images': [_ref().model_dump()],
            '_on_adopted': lambda: adopted.append(True),
        },
    ))
    await asyncio.wait_for(save_started.wait(), timeout=1)
    task.cancel()
    release_save.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert adopted == [True]


@pytest.mark.asyncio
async def test_session_save_failure_removes_unadopted_refs_from_memory(monkeypatch):
    from cognitrix.sessions.base import Session

    session = Session(agent_id='agent-1')
    adopted = []

    async def fail_save(_self):
        raise OSError('database unavailable')

    monkeypatch.setattr(Session, 'save', fail_save)
    agent = types.SimpleNamespace(
        tools=[],
        process_prompt=lambda prompt: {
            'role': 'User', 'type': 'text', 'content': prompt,
        },
        get_context_manager=lambda: pytest.fail('save failure must stop the turn'),
    )

    with pytest.raises(OSError, match='database unavailable'):
        await session(
            'edit',
            agent,
            attachments={
                'images': [_ref().model_dump()],
                '_on_adopted': lambda: adopted.append(True),
            },
        )

    assert adopted == []
    assert [item['type'] for item in session.chat] == ['text']


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

    async def ingest(*_args, **_kwargs):
        calls.append('ingest')

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', ingest
    )

    with pytest.raises(MediaValidationError, match='attachment changed'):
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

    async def ingest(_entry, _ownership, **_kwargs):
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

    async def not_image(*_args, **_kwargs):
        return None

    original = staging._create_document_transaction
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
    monkeypatch.setattr(staging, '_create_document_transaction', fail_second)

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

        async def __call__(self, *_args, attachments, output, **_kwargs):
            attachments['_on_adopted']()
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
            called.append({
                key: value for key, value in attachments.items()
                if not key.startswith('_')
            })
            attachments['_on_adopted']()

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

        async def __call__(self, *_args, attachments, **_kwargs):
            attachments['_on_adopted']()
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

    async def classify(_entry, _ownership, **_kwargs):
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
    monkeypatch.setattr(staging, '_create_document_transaction', fail_copy)

    with pytest.raises(OSError, match='destination'):
        await staging.promote_staged_attachments(staged, ownership)
    assert deleted == [(['created-image'], ownership)]
    assert not staged.batch_dir.exists()


@pytest.mark.asyncio
async def test_cancelling_document_rollback_waits_for_unlink_to_finish(
    tmp_path, monkeypatch
):
    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'note')], user_key='user-1', stream_id='browser-1'
    )

    async def not_image(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', not_image
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    target = tools_root / promoted.document_paths[0]['path']
    assert target.read_bytes() == b'note'
    started = __import__('threading').Event()
    release = __import__('threading').Event()
    original = staging._delete_secure_document

    def blocking_delete(record):
        started.set()
        release.wait(timeout=5)
        return original(record)

    monkeypatch.setattr(staging, '_delete_secure_document', blocking_delete)
    task = asyncio.create_task(staging.rollback_promoted_attachments(
        promoted,
        ownership,
    ))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not target.exists()


@pytest.mark.asyncio
async def test_document_rollback_retains_work_when_upload_root_is_replaced(
    tmp_path, monkeypatch
):
    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'note')], user_key='user-1', stream_id='browser-1'
    )

    async def not_image(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', not_image
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    relative = Path(promoted.document_paths[0]['path']).relative_to('uploads')
    uploads = tools_root / 'uploads'
    original_uploads = tools_root / 'uploads-original'
    uploads.rename(original_uploads)
    uploads.mkdir()

    with pytest.raises(staging.AttachmentCleanupError):
        await staging.rollback_promoted_attachments(promoted, ownership)
    assert (original_uploads / relative).read_bytes() == b'note'
    assert staging.pending_attachment_cleanup_count() >= 1

    uploads.rmdir()
    original_uploads.rename(uploads)
    assert await staging.retry_pending_attachment_cleanups() == 0
    assert not (uploads / relative).exists()


@pytest.mark.asyncio
async def test_document_quarantine_retry_removes_orphan_before_releasing_capacity(
    tmp_path,
    monkeypatch,
):
    tools_root = tmp_path / 'tools'
    uploads = tools_root / 'uploads'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    baseline_pending = staging.pending_attachment_cleanup_count()
    baseline_units = staging._attachment_cleanup_obligation_count()
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'note')],
        user_key='user-1',
        stream_id='document-quarantine-retry',
    )

    async def not_image(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        not_image,
    )
    ownership = MediaOwnership(
        'session-document-quarantine', 'user-1', 'agent-1'
    )
    promoted = await staging.promote_staged_attachments(staged, ownership)
    record = promoted._rollback_records[0]
    quarantine_leaf = 'q_document_retry'
    quarantine_path = uploads / quarantine_leaf
    original_delete = staging.secure_fs.DirectoryCapability.delete_directory
    attempts = 0

    def quarantine_then_resume(
        capability,
        leaf,
        *,
        expected_identity,
    ):
        nonlocal attempts
        if leaf != record.directory_leaf:
            return original_delete(
                capability,
                leaf,
                expected_identity=expected_identity,
            )
        attempts += 1
        logical_path = uploads / leaf
        if attempts == 1:
            logical_path.rename(quarantine_path)
            raise OSError('simulated post-rename document delete failure')
        assert not logical_path.exists()
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
            await staging.rollback_promoted_attachments(promoted, ownership)
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


@pytest.mark.parametrize(
    ('phase', 'flush_number', 'delete_method'),
    [
        ('directory', 1, 'delete_directory'),
        ('file', 2, 'delete_file'),
    ],
)
@pytest.mark.asyncio
async def test_unsettled_promoted_document_create_retains_exact_rollback(
    tmp_path,
    monkeypatch,
    phase,
    flush_number,
    delete_method,
):
    tools_root = tmp_path / 'tools'
    uploads = tools_root / 'uploads'
    uploads.mkdir(parents=True)
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    baseline_pending = staging.pending_attachment_cleanup_count()
    baseline_units = staging._attachment_cleanup_obligation_count()
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'note')],
        user_key='user-1',
        stream_id=f'promoted-{phase}-flush-retained',
    )
    ownership = MediaOwnership(
        f'session-{phase}', 'user-1', 'agent-1'
    )

    async def not_image(*_args, **_kwargs):
        return None

    original_flush = staging.secure_fs.DirectoryCapability.flush
    original_delete = getattr(
        staging.secure_fs.DirectoryCapability,
        delete_method,
    )
    document_prefix = 'd_' if phase == 'directory' else 'f_'
    flushes = 0
    deletion_blocked = True
    exact_deletes = []

    def fail_selected_parent_flush(capability):
        nonlocal flushes
        flushes += 1
        if flushes == flush_number:
            raise OSError(f'simulated {phase} parent flush failure')
        return original_flush(capability)

    def block_exact_delete(capability, leaf, *, expected_identity):
        if not leaf.startswith(document_prefix):
            return original_delete(
                capability,
                leaf,
                expected_identity=expected_identity,
            )
        exact_deletes.append((leaf, expected_identity))
        if deletion_blocked:
            raise OSError(f'simulated persistent {phase} rollback failure')
        return original_delete(
            capability,
            leaf,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        not_image,
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
    promoted = None
    record = None
    try:
        with pytest.raises(staging.AttachmentCleanupError) as exc:
            await staging.promote_staged_attachments(staged, ownership)

        assert str(exc.value) == 'Attachment cleanup did not complete'
        with staging._PENDING_CLEANUP_LOCK:
            retained = [
                value
                for value in staging._PENDING_ROLLBACKS.values()
                if value[1] == ownership
            ]
        assert len(retained) == 1
        promoted = retained[0][0]
        assert len(promoted._rollback_records) == 1
        record = promoted._rollback_records[0]
        assert exact_deletes
        unsettled_identity = exact_deletes[0][1]
        assert record.directory_identity is not None
        if phase == 'directory':
            assert record.directory_identity == unsettled_identity
            assert record.file_identity is None
        else:
            assert record.file_identity == unsettled_identity

        orphan_directory = uploads / record.directory_leaf
        assert orphan_directory.is_dir()
        if phase == 'file':
            assert (orphan_directory / record.file_leaf).is_file()
        assert staging.pending_attachment_cleanup_count() == baseline_pending + 1
        assert staging._attachment_cleanup_obligation_count() == baseline_units + 1

        deletion_blocked = False
        assert await staging.retry_pending_attachment_cleanups() == baseline_pending
        assert not orphan_directory.exists()
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
        if uploads.exists():
            for child in uploads.iterdir():
                if child.is_dir():
                    __import__('shutil').rmtree(child)
                else:
                    child.unlink()
        await staging.retry_pending_attachment_cleanups()


@pytest.mark.asyncio
async def test_document_rollback_does_not_recreate_missing_upload_root(
    tmp_path, monkeypatch
):
    tools_root = tmp_path / 'tools'
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tools_root)
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'note')], user_key='user-1', stream_id='browser-1'
    )

    async def not_image(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        staging.media_assets, 'ingest_staged_image_if_recognized', not_image
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    relative = Path(promoted.document_paths[0]['path']).relative_to('uploads')
    uploads = tools_root / 'uploads'
    original_uploads = tools_root / 'uploads-original'
    uploads.rename(original_uploads)

    with pytest.raises(staging.AttachmentCleanupError):
        await staging.rollback_promoted_attachments(promoted, ownership)
    assert not uploads.exists()
    assert (original_uploads / relative).read_bytes() == b'note'

    original_uploads.rename(uploads)
    assert await staging.retry_pending_attachment_cleanups() == 0


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


@pytest.mark.asyncio
async def test_session_failure_before_adoption_rolls_promoted_media_back(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    rolled_back = []

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, **_kwargs):
            raise ValueError('process_prompt failed before attachment adoption')

    async def promote(*_args):
        return staging.PromotedAttachments([_ref()], [])

    async def rollback(value, ownership):
        rolled_back.append((value, ownership))

    manager._resolve_session = lambda _sid: asyncio.sleep(0, result=Session())
    monkeypatch.setattr(sse, 'promote_staged_attachments', promote)
    monkeypatch.setattr(sse, 'rollback_promoted_attachments', rollback)
    await manager.action_queue.put({
        'type': 'chat_message', 'content': 'edit', 'session_id': 'session-1',
        'staged_attachments': staged,
    })

    response = await manager.sse_endpoint(Request())
    assert (await _next_json(response.body_iterator))['type'] == 'error'
    assert len(rolled_back) == 1
    assert manager.turn_output_queue.empty()
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_session_return_without_adoption_is_not_reported_as_success(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    rolled_back = []

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, **_kwargs):
            return None

    manager._resolve_session = lambda _sid: asyncio.sleep(0, result=Session())
    monkeypatch.setattr(
        sse, 'promote_staged_attachments',
        lambda *_args: asyncio.sleep(
            0, result=staging.PromotedAttachments([_ref()], [])
        ),
    )
    monkeypatch.setattr(
        sse, 'rollback_promoted_attachments',
        lambda *args: asyncio.sleep(0, result=rolled_back.append(args)),
    )
    await manager.action_queue.put({
        'type': 'chat_message', 'content': 'edit', 'session_id': 'session-1',
        'staged_attachments': staged,
    })

    response = await manager.sse_endpoint(Request())
    event = await _next_json(response.body_iterator)
    assert event['type'] == 'error'
    assert event['content'] == sse._ATTACHMENT_UNAVAILABLE
    assert len(rolled_back) == 1
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_session_failure_after_adoption_keeps_persisted_media(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    rolled_back = []

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, attachments, **_kwargs):
            attachments['_on_adopted']()
            raise RuntimeError('provider failed after durable history save')

    manager._resolve_session = lambda _sid: asyncio.sleep(0, result=Session())
    monkeypatch.setattr(
        sse, 'promote_staged_attachments',
        lambda *_args: asyncio.sleep(
            0, result=staging.PromotedAttachments([_ref()], [])
        ),
    )
    monkeypatch.setattr(
        sse, 'rollback_promoted_attachments',
        lambda *args: asyncio.sleep(0, result=rolled_back.append(args)),
    )
    await manager.action_queue.put({
        'type': 'chat_message', 'content': 'edit', 'session_id': 'session-1',
        'staged_attachments': staged,
    })
    response = await manager.sse_endpoint(Request())
    assert (await _next_json(response.body_iterator))['type'] == 'attachments_ingested'
    assert (await _next_json(response.body_iterator))['type'] == 'error'
    assert rolled_back == []
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_durable_session_adoption_releases_transferred_cleanup_capacity(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        2,
    )
    staged = await staging.stage_upload_files(
        [Upload('photo.png', _png(), 'image/png')],
        user_key='user-1',
        stream_id='durable-adoption-capacity',
    )
    assert staging._attachment_cleanup_obligation_count() == 2

    async def ingest(*_args, **_kwargs):
        return _ref('adopted-image')

    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        ingest,
    )

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, attachments, **_kwargs):
            attachments['_on_adopted']()

    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    manager._resolve_session = lambda _sid: asyncio.sleep(0, result=Session())
    await manager.action_queue.put({
        'type': 'chat_message',
        'content': 'keep this',
        'session_id': 'session-1',
        'staged_attachments': staged,
    })

    response = await manager.sse_endpoint(Request())
    assert (await _next_json(response.body_iterator))['type'] == 'attachments_ingested'
    assert staging._attachment_cleanup_obligation_count() == 0
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_promoted_cleanup_outage_retains_unit_and_blocks_new_batch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(
        staging,
        'MAX_ATTACHMENT_CLEANUP_OBLIGATION_UNITS',
        2,
    )
    monkeypatch.setattr(staging, 'ATTACHMENT_CLEANUP_ATTEMPTS', 1)
    monkeypatch.setattr(staging, '_schedule_pending_cleanup_drain', lambda: None)
    staged = await staging.stage_upload_files(
        [Upload('photo.png', _png(), 'image/png')],
        user_key='user-1',
        stream_id='promoted-outage-capacity',
    )

    async def ingest(*_args, **_kwargs):
        return _ref('retained-image')

    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        ingest,
    )
    ownership = MediaOwnership('session-1', 'user-1', 'agent-1')
    promoted = await staging.promote_staged_attachments(staged, ownership)
    assert staging._attachment_cleanup_obligation_count() == 1

    async def unavailable_delete(*_args, **_kwargs):
        raise OSError('simulated artifact-store outage')

    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', unavailable_delete)
    with pytest.raises(staging.AttachmentCleanupError):
        await staging.rollback_promoted_attachments(promoted, ownership)
    assert staging.pending_attachment_cleanup_count() == 1
    assert staging._attachment_cleanup_obligation_count() == 1

    blocked = Upload('blocked.png', _png(), 'image/png')
    with pytest.raises(__import__('fastapi').HTTPException) as exc:
        await staging.stage_upload_files(
            [blocked],
            user_key='user-1',
            stream_id='blocked-by-promoted-outage',
        )
    assert exc.value.status_code == 503
    assert blocked.offset == 0

    monkeypatch.setattr(
        staging.media_assets,
        'delete_artifacts',
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )
    assert await staging.retry_pending_attachment_cleanups() == 0
    assert staging._attachment_cleanup_obligation_count() == 0


@pytest.mark.asyncio
async def test_direct_prestart_task_cancel_uses_supervised_cleanup():
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    queue, terminal_event = manager._open_turn_output()
    action = {
        'type': 'chat_message', 'content': 'hello', 'session_id': 'session-1',
        'staged_attachments': staged,
    }

    task = manager._start_chat_action(action, queue, terminal_event)
    task.cancel()
    await asyncio.wait_for(terminal_event.wait(), timeout=1)

    assert staged.cleanup_calls >= 1
    assert manager.turn_terminal['type'] == 'turn_stopped'
    assert manager.turn_pending is False


@pytest.mark.asyncio
async def test_transient_rollback_failure_is_retried(monkeypatch):
    attempts = 0

    async def flaky_delete(_ids, _ownership):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError('temporarily busy')

    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', flaky_delete)
    await staging.rollback_promoted_attachments(
        staging.PromotedAttachments([_ref()], []),
        MediaOwnership('session-1', 'user-1', 'agent-1'),
    )
    assert attempts == 2


@pytest.mark.asyncio
async def test_permanent_rollback_failure_is_retained_and_surfaced(monkeypatch):
    async def fail_delete(_ids, _ownership):
        raise OSError('permanently busy C:/private/path')

    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', fail_delete)
    with pytest.raises(staging.AttachmentCleanupError):
        await staging.rollback_promoted_attachments(
            staging.PromotedAttachments([_ref()], []),
            MediaOwnership('session-1', 'user-1', 'agent-1'),
        )
    assert staging.pending_attachment_cleanup_count() >= 1

    async def delete_now_available(_ids, _ownership):
        return None

    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', delete_now_available)
    assert await staging.retry_pending_attachment_cleanups() == 0


@pytest.mark.asyncio
async def test_cancellation_does_not_hide_failed_rollback(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_delete(_ids, _ownership):
        started.set()
        await release.wait()
        raise OSError('rollback remained busy')

    monkeypatch.setattr(staging.media_assets, 'delete_artifacts', fail_delete)
    task = asyncio.create_task(staging.rollback_promoted_attachments(
        staging.PromotedAttachments([_ref('cancelled-cleanup')], []),
        MediaOwnership('session-1', 'user-1', 'agent-1'),
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    release.set()

    with pytest.raises(staging.AttachmentCleanupError):
        await task
    assert staging.pending_attachment_cleanup_count() >= 1
    monkeypatch.setattr(
        staging.media_assets, 'delete_artifacts',
        lambda *_args: asyncio.sleep(0),
    )
    assert await staging.retry_pending_attachment_cleanups() == 0


@pytest.mark.asyncio
async def test_cancellation_does_not_hide_failed_staging_cleanup():
    started = asyncio.Event()
    release = asyncio.Event()

    class FailingStaged:
        def __init__(self):
            self.fail = True

        async def cleanup(self):
            started.set()
            await release.wait()
            if self.fail:
                raise OSError('staging remained busy')

    staged = FailingStaged()
    task = asyncio.create_task(staging.cleanup_staged_attachments(staged))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    release.set()

    with pytest.raises(staging.AttachmentCleanupError):
        await task
    assert staging.pending_attachment_cleanup_count() >= 1
    staged.fail = False
    assert await staging.retry_pending_attachment_cleanups() == 0


@pytest.mark.asyncio
async def test_permanent_cleanup_failure_reaches_client_as_generic_error(monkeypatch):
    staged = FakeStaged()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *_args, **_kwargs):
            raise RuntimeError('provider failed C:/private/provider.log')

    manager._resolve_session = lambda _sid: asyncio.sleep(0, result=Session())
    monkeypatch.setattr(
        sse, 'promote_staged_attachments',
        lambda *_args: asyncio.sleep(
            0, result=staging.PromotedAttachments([_ref()], [])
        ),
    )

    async def fail_rollback(*_args):
        raise staging.AttachmentCleanupError([OSError('C:/private/artifact')])

    monkeypatch.setattr(sse, 'rollback_promoted_attachments', fail_rollback)
    await manager.action_queue.put({
        'type': 'chat_message', 'content': 'edit', 'session_id': 'session-1',
        'staged_attachments': staged,
    })
    response = await manager.sse_endpoint(Request())
    first = await _next_json(response.body_iterator)
    terminal = await _next_json(response.body_iterator)

    assert first['type'] == terminal['type'] == 'error'
    assert first['content'] == terminal['content'] == sse._ATTACHMENT_UNAVAILABLE
    assert 'private' not in json.dumps([first, terminal])
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_unsupported_recognizable_ico_is_not_downgraded_to_document(
    tmp_path,
):
    path = tmp_path / 'icon.ico'
    path.write_bytes(b'\x00\x00\x01\x00' + b'corrupt-ico')
    with pytest.raises(MediaValidationError, match='Unsupported'):
        await MediaAssetService().ingest_staged_image_if_recognized(
            StagedAttachment(path, 'icon.bin', 'application/octet-stream', path.stat().st_size),
            MediaOwnership('session-1', 'user-1', 'agent-1'),
        )


@pytest.mark.asyncio
async def test_expired_queued_batch_is_claimed_before_slow_session_resolution(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    cleanup_baseline = staging._attachment_cleanup_obligation_count()
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'note')], user_key='user-1', stream_id='browser-1'
    )
    with staging._ACTIVE_LOCK:
        staging._BATCH_LEASES[staged.batch_dir] = ('queued', __import__('time').time() - 1)
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    resolving = asyncio.Event()
    release = asyncio.Event()

    async def resolve(_sid):
        resolving.set()
        await release.wait()
        return None

    manager._resolve_session = resolve
    await manager.action_queue.put({
        'type': 'chat_message', 'content': 'hello', 'session_id': 'session-1',
        'staged_attachments': staged,
    })
    response = await manager.sse_endpoint(Request())
    pending = asyncio.create_task(anext(response.body_iterator))
    await asyncio.wait_for(resolving.wait(), timeout=1)
    old = __import__('time').time() - 7200
    __import__('os').utime(staged.batch_dir, (old, old))

    assert await staging.sweep_stale_staging(
        now=__import__('time').time() + staging.STAGING_LEASE_SECONDS + 1
    ) == 0
    assert staged.batch_dir.exists()
    release.set()
    assert json.loads((await pending)['data'])['type'] == 'error'
    await asyncio.wait_for(manager.turn_terminal_event.wait(), timeout=1)
    assert staging._attachment_cleanup_obligation_count() == cleanup_baseline
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_losing_double_claim_does_not_clean_the_winners_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    winner = await staging.stage_upload_files(
        [Upload('note.txt', b'note')], user_key='user-1', stream_id='browser-1'
    )
    loser = staging.StagedAttachmentSet(winner.batch_dir, list(winner.entries))
    winner.claim_now()
    manager = sse.SSEManager(_agent())
    manager.user_key = 'user-1'
    assert manager.begin_turn()
    queue, terminal_event = manager._open_turn_output()

    task = manager._start_chat_action({
        'type': 'chat_message', 'content': 'hello', 'session_id': 'session-1',
        'staged_attachments': loser,
    }, queue, terminal_event)
    await asyncio.wait_for(task, timeout=1)

    assert winner.batch_dir.exists()
    assert loser._cleaned is False
    await staging.cleanup_staged_attachments(winner)


@pytest.mark.asyncio
async def test_source_replacement_after_preflight_is_rejected(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tmp_path / 'tools')
    cleanup_baseline = staging._attachment_cleanup_obligation_count()
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'good')], user_key='user-1', stream_id='browser-1'
    )
    hook_called = False

    def replace_source(_entries):
        nonlocal hook_called
        hook_called = True
        path = staged.entries[0].path
        path.unlink()
        path.write_bytes(b'evil')

    monkeypatch.setattr(staging, '_after_preflight_hook', replace_source, raising=False)
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    with pytest.raises(MediaValidationError, match='changed|unavailable'):
        await staging.promote_staged_attachments(
            staged, MediaOwnership('session-1', 'user-1', 'agent-1')
        )
    assert hook_called is True
    # The identity replacement is intentionally quarantined fail-closed. Model
    # operator removal of that unknown replacement, then resolve the retained
    # cleanup obligation so this process-level test does not leak capacity.
    staged.entries[0].path.unlink()
    await staging.cleanup_staged_attachments(staged)
    assert staging._attachment_cleanup_obligation_count() == cleanup_baseline


@pytest.mark.asyncio
async def test_destination_reparse_swap_before_write_is_rejected(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    monkeypatch.setattr(staging.settings, 'tools_root', tmp_path / 'tools')
    staged = await staging.stage_upload_files(
        [Upload('note.txt', b'good')], user_key='user-1', stream_id='browser-1'
    )
    hook_called = False
    reject_destination = False

    def mark_reparse(_uploads, _directory, _file):
        nonlocal hook_called, reject_destination
        hook_called = True
        reject_destination = True
    original_open_root = staging.secure_fs.open_root
    uploads = tmp_path / 'tools' / 'uploads'

    def reject_swapped_destination(path):
        nonlocal reject_destination
        if reject_destination and Path(path) == uploads:
            reject_destination = False
            raise staging.secure_fs.CapabilityError()
        return original_open_root(path)

    monkeypatch.setattr(
        staging, '_before_secure_document_create_hook', mark_reparse
    )
    monkeypatch.setattr(
        staging.secure_fs, 'open_root', reject_swapped_destination
    )
    monkeypatch.setattr(
        staging.media_assets,
        'ingest_staged_image_if_recognized',
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )

    with pytest.raises(MediaValidationError, match='destination'):
        await staging.promote_staged_attachments(
            staged, MediaOwnership('session-1', 'user-1', 'agent-1')
        )
    assert hook_called is True
