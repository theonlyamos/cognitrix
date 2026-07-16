"""Direct and ASGI request coverage for bounded agent chat attachments."""

from __future__ import annotations

import base64
import json
import types

import httpx
import pytest
from fastapi import HTTPException
from starlette import formparsers
from starlette.requests import ClientDisconnect, Request

import cognitrix.api.routes.agents as agent_routes
import cognitrix.media.staging as staging
from cognitrix.media.staging import StagedAttachmentSet


class JsonRequest:
    headers: dict[str, str] = {}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class RecordingQueue:
    def __init__(self, error: BaseException | None = None):
        self.actions: list[dict] = []
        self.error = error

    async def put(self, action):
        if self.error is not None:
            raise self.error
        self.actions.append(action)


class BlockingQueue(RecordingQueue):
    def __init__(self):
        super().__init__()
        self.started = __import__('asyncio').Event()

    async def put(self, action):
        self.started.set()
        await __import__('asyncio').Event().wait()


class FakeManager:
    def __init__(self, *, queue_error: BaseException | None = None, begins: bool = True):
        self.action_queue = RecordingQueue(queue_error)
        self.begins = begins
        self.begin_calls = 0
        self.finish_calls = 0

    def begin_turn(self):
        self.begin_calls += 1
        return self.begins

    def finish_turn(self):
        self.finish_calls += 1


def _install_route_fakes(monkeypatch, manager):
    agent = types.SimpleNamespace(id='agent-1')

    async def resolve_agent(agent_id, _request):
        assert agent_id == agent.id
        return agent

    monkeypatch.setattr(agent_routes, '_resolve_agent', resolve_agent)
    monkeypatch.setattr(agent_routes, 'get_sse_manager', lambda *args, **kwargs: manager)
    return agent


def _multipart_request(
    payload: dict | None,
    files,
    *,
    extra_fields=(),
    payload_as_file: bool = True,
    payload_content_type: str = 'application/json',
    duplicate_payload: bool = False,
    include_content_length: bool = True,
    include_app: bool = False,
    first_chunk_size: int | None = None,
) -> Request:
    parts = []
    if payload is not None:
        payload_name = 'payload.json' if payload_as_file else None
        parts.append(('payload', (payload_name, json.dumps(payload), payload_content_type)))
        if duplicate_payload:
            parts.append(('payload', (payload_name, json.dumps(payload), payload_content_type)))
    parts.extend(('files', (name, data, mime)) for name, data, mime in files)
    parts.extend((name, (None, value, None)) for name, value in extra_fields)
    request = httpx.Request(
        'POST',
        'http://test/agents/chat',
        files=parts,
    )
    body = request.read()
    chunks = (
        [body[:first_chunk_size], body[first_chunk_size:]]
        if first_chunk_size is not None
        else [body]
    )

    async def receive():
        chunk = chunks.pop(0) if chunks else b''
        return {
            'type': 'http.request',
            'body': chunk,
            'more_body': bool(chunks),
        }

    headers = [
        (key.lower().encode(), value.encode())
        for key, value in request.headers.items()
        if include_content_length or key.lower() != 'content-length'
    ]
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/agents/chat',
        'raw_path': b'/agents/chat',
        'query_string': b'',
        'headers': headers,
        'client': ('127.0.0.1', 1234),
        'server': ('test', 80),
    }
    if include_app:
        scope['app'] = object()
    return Request(scope, receive)


def _json_asgi_request(payload, *, include_content_length: bool = True) -> Request:
    body = json.dumps(payload).encode()
    chunks = [body[: max(1, len(body) // 2)], body[max(1, len(body) // 2):]]

    async def receive():
        chunk = chunks.pop(0) if chunks else b''
        return {
            'type': 'http.request',
            'body': chunk,
            'more_body': bool(chunks),
        }

    headers = [(b'content-type', b'application/json')]
    if include_content_length:
        headers.append((b'content-length', str(len(body)).encode()))
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/agents/chat',
        'raw_path': b'/agents/chat',
        'query_string': b'',
        'headers': headers,
        'client': ('127.0.0.1', 1234),
        'server': ('test', 80),
    }
    return Request(scope, receive)


@pytest.fixture
def staging_root(tmp_path, monkeypatch):
    monkeypatch.setattr(staging.settings, 'workdir', tmp_path)
    return tmp_path / 'staging' / 'chat-media'


@pytest.mark.asyncio
async def test_text_only_json_chat_behavior_and_queue_metadata_are_preserved(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)

    result = await agent_routes.chat_endpoint(
        JsonRequest({
            'agent_id': agent.id,
            'stream_id': 'browser-a',
            'session_id': 'session-1',
            'message': 'hello',
            'bypass_permissions': 1,
        }),
        user=types.SimpleNamespace(id='user-a'),
    )

    assert result == {'status': 'Message sent'}
    assert manager.begin_calls == 1
    assert manager.finish_calls == 0
    assert manager.action_queue.actions == [{
        'type': 'chat_message',
        'content': 'hello',
        'session_id': 'session-1',
        'staged_attachments': None,
        'edit_source_artifact_id': None,
        'bypass_permissions': True,
    }]


@pytest.mark.asyncio
async def test_multipart_payload_and_repeated_files_stage_to_paths(
    staging_root, monkeypatch
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {
            'agent_id': agent.id,
            'stream_id': 'browser-a',
            'session_id': 'session-1',
            'message': 'edit this',
            'edit_source_artifact_id': 'artifact-parent',
        },
        [
            ('one.png', b'first-image', 'image/png'),
            ('../two.png', b'second-image', 'image/png'),
        ],
    )

    result = await agent_routes.chat_endpoint(
        request, user=types.SimpleNamespace(id='user-a')
    )

    assert result == {'status': 'Message sent'}
    action = manager.action_queue.actions[0]
    staged = action['staged_attachments']
    assert isinstance(staged, StagedAttachmentSet)
    assert [entry.filename for entry in staged.entries] == ['one.png', 'two.png']
    assert [entry.path.read_bytes() for entry in staged.entries] == [b'first-image', b'second-image']
    assert action['edit_source_artifact_id'] == 'artifact-parent'
    assert action['content'] == 'edit this'
    assert repr(action).find('data:image') == -1
    assert "b'first-image'" not in repr(action)
    assert 'UploadFile' not in repr(action)
    assert staged.batch_dir.exists(), 'successful queue put transfers staging ownership'

    await staged.cleanup()


@pytest.mark.asyncio
async def test_multipart_accepts_payload_as_a_string_field(staging_root, monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'string payload'},
        [('note.txt', b'note', 'text/plain')],
        payload_as_file=False,
    )

    await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    action = manager.action_queue.actions[0]
    assert action['content'] == 'string payload'
    assert action['staged_attachments'].entries[0].path.read_bytes() == b'note'
    await action['staged_attachments'].cleanup()


@pytest.mark.asyncio
async def test_multipart_accepts_twenty_files_plus_the_payload_blob(
    staging_root, monkeypatch
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'twenty'},
        [(f'{index}.txt', str(index).encode(), 'text/plain') for index in range(20)],
    )

    await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    staged = manager.action_queue.actions[0]['staged_attachments']
    assert len(staged.entries) == 20
    await staged.cleanup()


@pytest.mark.asyncio
async def test_fastapi_wrapped_parser_file_count_error_maps_to_413(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'too many'},
        [(f'{index}.txt', b'x', 'text/plain') for index in range(21)],
        include_app=True,
    )

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 413
    assert manager.begin_calls == 0


@pytest.mark.asyncio
async def test_legacy_data_urls_converge_on_the_same_staged_representation(
    staging_root, monkeypatch
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    encoded = base64.b64encode(b'legacy-file').decode()
    data_url = f'data:text/plain;base64,{encoded}'

    await agent_routes.chat_endpoint(
        JsonRequest({
            'agent_id': agent.id,
            'stream_id': 'browser-a',
            'message': 'use this',
            'attachments': [{'name': 'legacy.txt', 'dataUrl': data_url}],
            'edit_source_artifact_id': 'artifact-parent',
        }),
        user=types.SimpleNamespace(id='user-a'),
    )

    action = manager.action_queue.actions[0]
    staged = action['staged_attachments']
    assert isinstance(staged, StagedAttachmentSet)
    assert staged.entries[0].path.read_bytes() == b'legacy-file'
    assert staged.entries[0].filename == 'legacy.txt'
    assert action['edit_source_artifact_id'] == 'artifact-parent'
    serialized = repr(action)
    assert data_url not in serialized
    assert encoded not in serialized
    assert "b'legacy-file'" not in serialized

    await staged.cleanup()


@pytest.mark.asyncio
async def test_multipart_rejects_unknown_non_file_field_before_reserving_turn(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [],
        extra_fields=[('unexpected', 'value')],
    )

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 400
    assert manager.begin_calls == 0
    assert manager.finish_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'multipart_request',
    [
        _multipart_request(None, [('file.txt', b'x', 'text/plain')]),
        _multipart_request(
            {'agent_id': 'agent-1', 'stream_id': 'browser-a'},
            [],
            duplicate_payload=True,
        ),
        _multipart_request(
            {'agent_id': 'agent-1', 'stream_id': 'browser-a'},
            [],
            payload_content_type='text/plain',
        ),
    ],
    ids=['missing-payload', 'duplicate-payload', 'wrong-payload-content-type'],
)
async def test_multipart_validates_the_payload_part_before_reserving_turn(
    monkeypatch, multipart_request
):
    manager = FakeManager()
    _install_route_fakes(monkeypatch, manager)

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(
            multipart_request, user=types.SimpleNamespace(id='user-a')
        )

    assert exc.value.status_code == 400
    assert manager.begin_calls == 0
    assert manager.finish_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('payload_as_file', [True, False], ids=['file', 'string'])
async def test_multipart_payload_part_has_an_explicit_size_cap(
    monkeypatch, payload_as_file
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    monkeypatch.setattr(agent_routes, 'MAX_CHAT_PAYLOAD_BYTES', 16, raising=False)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'x' * 32},
        [],
        payload_as_file=payload_as_file,
    )

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 413
    assert manager.begin_calls == 0


@pytest.mark.asyncio
async def test_negative_content_length_is_rejected_before_parsing(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _json_asgi_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'}
    )
    request.scope['headers'] = [
        (key, b'-1' if key == b'content-length' else value)
        for key, value in request.scope['headers']
    ]

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 400
    assert manager.begin_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('include_content_length', [True, False], ids=['preflight', 'chunked'])
async def test_multipart_body_envelope_is_enforced_before_reserving_turn(
    monkeypatch, include_content_length
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    monkeypatch.setattr(agent_routes, 'MAX_MULTIPART_BODY_BYTES', 32, raising=False)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [('file.txt', b'payload', 'text/plain')],
        include_content_length=include_content_length,
    )

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 413
    assert manager.begin_calls == 0


@pytest.mark.asyncio
async def test_chunked_body_overflow_closes_already_spooled_multipart_parts(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    monkeypatch.setattr(agent_routes, 'MAX_MULTIPART_BODY_BYTES', 256)
    created = []
    original = formparsers.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        file = original(*args, **kwargs)
        created.append(file)
        return file

    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', tracked_spool)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [('file.txt', b'x' * 512, 'text/plain')],
        include_content_length=False,
        first_chunk_size=256,
    )

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 413
    assert created, 'the first chunk should have opened a spooled payload part'
    assert all(file.closed for file in created)
    assert manager.begin_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('termination', ['cancel', 'disconnect'])
async def test_interrupted_multipart_parse_closes_every_partial_spool(
    monkeypatch, termination
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    created = []
    original_spool = formparsers.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        file = original_spool(*args, **kwargs)
        created.append(file)
        return file

    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', tracked_spool)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [('file.txt', b'x' * 512, 'text/plain')],
        include_content_length=False,
        first_chunk_size=256,
    )
    original_receive = request._receive
    second_receive = __import__('asyncio').Event()
    calls = 0

    async def interrupted_receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return await original_receive()
        second_receive.set()
        if termination == 'disconnect':
            return {'type': 'http.disconnect'}
        await __import__('asyncio').Event().wait()

    request._receive = interrupted_receive
    task = __import__('asyncio').create_task(
        agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))
    )
    await __import__('asyncio').wait_for(second_receive.wait(), timeout=2)
    assert created, 'the first chunk must open the payload spool before interruption'
    if termination == 'cancel':
        task.cancel()
        expected = __import__('asyncio').CancelledError
    else:
        expected = ClientDisconnect

    with pytest.raises(expected):
        await task
    assert all(file.closed for file in created)
    assert manager.begin_calls == 0


@pytest.mark.asyncio
async def test_chunked_json_body_envelope_is_enforced_before_parsing(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    monkeypatch.setattr(agent_routes, 'MAX_JSON_BODY_BYTES', 24, raising=False)
    request = _json_asgi_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'too long'},
        include_content_length=False,
    )

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 413
    assert manager.begin_calls == 0


@pytest.mark.asyncio
async def test_oversize_stage_failure_finishes_reserved_turn_and_removes_batch(
    staging_root, monkeypatch
):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)
    monkeypatch.setattr(staging, 'MAX_UPLOAD_FILE_BYTES', 3)
    monkeypatch.setattr(staging, 'MAX_UPLOAD_TOTAL_BYTES', 3)
    data_url = 'data:application/octet-stream;base64,' + base64.b64encode(b'four').decode()

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(
            JsonRequest({
                'agent_id': agent.id,
                'stream_id': 'browser-a',
                'message': 'hello',
                'attachments': [{'name': 'large.bin', 'dataUrl': data_url}],
            }),
            user=types.SimpleNamespace(id='user-a'),
        )

    assert exc.value.status_code == 413
    assert manager.finish_calls == 1
    assert manager.action_queue.actions == []
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


@pytest.mark.asyncio
async def test_queue_failure_finishes_turn_and_cleans_staged_files(
    staging_root, monkeypatch
):
    manager = FakeManager(queue_error=RuntimeError('queue unavailable'))
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [('file.txt', b'content', 'text/plain')],
    )

    with pytest.raises(RuntimeError, match='queue unavailable'):
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert manager.finish_calls == 1
    assert manager.action_queue.actions == []
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


@pytest.mark.asyncio
async def test_queue_cancellation_finishes_turn_and_settles_cleanup(
    staging_root, monkeypatch
):
    manager = FakeManager(queue_error=__import__('asyncio').CancelledError())
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [('file.txt', b'content', 'text/plain')],
    )

    with pytest.raises(__import__('asyncio').CancelledError):
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert manager.finish_calls == 1
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


@pytest.mark.asyncio
async def test_external_cancellation_during_queue_put_cleans_before_propagating(
    staging_root, monkeypatch
):
    manager = FakeManager()
    manager.action_queue = BlockingQueue()
    agent = _install_route_fakes(monkeypatch, manager)
    request = _multipart_request(
        {'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'},
        [('file.txt', b'content', 'text/plain')],
    )
    task = __import__('asyncio').create_task(
        agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))
    )
    await __import__('asyncio').wait_for(manager.action_queue.started.wait(), timeout=2)
    assert list(staging_root.iterdir()), 'staging must exist while queue.put is pending'
    task.cancel()

    with pytest.raises(__import__('asyncio').CancelledError):
        await task
    assert manager.finish_calls == 1
    assert list(staging_root.iterdir()) == []


@pytest.mark.asyncio
async def test_busy_stream_does_not_stage_attachments(staging_root, monkeypatch):
    manager = FakeManager(begins=False)
    agent = _install_route_fakes(monkeypatch, manager)
    encoded = base64.b64encode(b'must-not-write').decode()

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(
            JsonRequest({
                'agent_id': agent.id,
                'stream_id': 'browser-a',
                'message': 'hello',
                'attachments': [{
                    'name': 'file.txt',
                    'dataUrl': f'data:text/plain;base64,{encoded}',
                }],
            }),
            user=types.SimpleNamespace(id='user-a'),
        )

    assert exc.value.status_code == 409
    assert manager.begin_calls == 1
    assert manager.finish_calls == 0
    assert not staging_root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload', 'manager', 'status'),
    [
        ({'agent_id': 'agent-1', 'stream_id': 'missing', 'message': 'hello'}, None, 409),
        ({'agent_id': 'agent-1', 'stream_id': '', 'message': 'hello'}, FakeManager(), 400),
    ],
)
async def test_pre_reservation_failures_do_not_stage_or_leave_a_turn(
    staging_root, monkeypatch, payload, manager, status
):
    _install_route_fakes(monkeypatch, manager)

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(
            JsonRequest(payload), user=types.SimpleNamespace(id='user-a')
        )

    assert exc.value.status_code == status
    assert not staging_root.exists()
    if manager is not None:
        assert manager.begin_calls == 0
        assert manager.finish_calls == 0


@pytest.mark.asyncio
async def test_malformed_multipart_payload_does_not_stage_or_reserve(
    staging_root, monkeypatch
):
    manager = FakeManager()
    _install_route_fakes(monkeypatch, manager)
    request = _multipart_request({}, [])
    request = _multipart_request({}, [], extra_fields=[])
    # Replace the generated valid JSON bytes in-place with malformed JSON while
    # preserving a real multipart Request/parser path.
    original_receive = request._receive

    async def malformed_receive():
        event = await original_receive()
        event['body'] = event['body'].replace(b'{}', b'{!')
        return event

    request._receive = malformed_receive

    with pytest.raises(HTTPException) as exc:
        await agent_routes.chat_endpoint(request, user=types.SimpleNamespace(id='user-a'))

    assert exc.value.status_code == 400
    assert manager.begin_calls == 0
    assert manager.finish_calls == 0
    assert not staging_root.exists()


@pytest.mark.asyncio
async def test_chat_endpoint_never_resolves_a_session(monkeypatch):
    manager = FakeManager()
    agent = _install_route_fakes(monkeypatch, manager)

    async def forbidden(*args, **kwargs):
        raise AssertionError('chat endpoint must leave session resolution to SSE consumption')

    monkeypatch.setattr(agent_routes.Session, 'get', forbidden)
    monkeypatch.setattr(agent_routes.Session, 'load', forbidden)

    await agent_routes.chat_endpoint(
        JsonRequest({'agent_id': agent.id, 'stream_id': 'browser-a', 'message': 'hello'}),
        user=types.SimpleNamespace(id='user-a'),
    )
