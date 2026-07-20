import asyncio
import json
import types

import pytest

import cognitrix.api.routes.agents as agent_routes
from cognitrix.questions.broker import (
    QuestionTurnContext,
    ask_question,
    question_turn_ctx,
    resolve_question,
)
from cognitrix.questions.models import QuestionSpec
from cognitrix.utils.sse import SSEManager, get_sse_manager


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class SSERequest:
    async def is_disconnected(self):
        return False


async def _pending_question(user_key='user-1', stream_id='stream-1'):
    emitted = []

    async def emit(event):
        emitted.append(event)

    token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit,
        session_id='session-1',
        stream_id=stream_id,
        user_key=user_key,
    ))
    task = asyncio.create_task(ask_question(QuestionSpec.from_tool_args(
        prompt='Continue?',
        options=[{'id': 'yes', 'label': 'Yes'}, {'id': 'no', 'label': 'No'}],
        recommended_option_id='yes',
    )))
    await asyncio.sleep(0)
    question_turn_ctx.reset(token)
    return task, emitted[0]


@pytest.mark.asyncio
async def test_question_endpoint_resolves_only_the_owners_answer():
    task, event = await _pending_question()
    try:
        with pytest.raises(agent_routes.HTTPException) as denied:
            await agent_routes.question_endpoint(
                JsonRequest({
                    'request_id': event['request_id'],
                    'action': 'answer',
                    'option_id': 'yes',
                }),
                user=types.SimpleNamespace(id='intruder'),
            )
        assert denied.value.status_code == 404

        result = await agent_routes.question_endpoint(
            JsonRequest({
                'request_id': event['request_id'],
                'action': 'answer',
                'option_id': 'yes',
            }),
            user=types.SimpleNamespace(id='user-1'),
        )
        assert result == {'status': 'resolved'}
        assert (await task).option_id == 'yes'
    finally:
        if not task.done():
            await resolve_question(event['request_id'], 'user-1', 'cancel')


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'request_id': 'x', 'action': 'answer'},
    {'request_id': 'x', 'action': 'answer', 'option_id': 'a', 'text': 'both'},
    {'request_id': 'x', 'action': 'cancel', 'text': 'not allowed'},
    {'request_id': 'x', 'action': 'unknown'},
    {'request_id': 'x', 'action': 'cancel', 'extra': True},
])
async def test_question_endpoint_rejects_malformed_actions(payload):
    with pytest.raises(agent_routes.HTTPException) as exc:
        await agent_routes.question_endpoint(
            JsonRequest(payload), user=types.SimpleNamespace(id='user-1'),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_stop_current_turn_cancels_pending_question():
    task, _ = await _pending_question()
    manager = SSEManager(types.SimpleNamespace(id='agent-1'))
    manager.user_key = 'user-1'
    manager.stream_id = 'stream-1'
    manager.begin_turn()

    assert manager.stop_current_turn()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_reconnecting_stream_replays_pending_question_snapshot():
    task, event = await _pending_question()
    manager = get_sse_manager(
        'user-1', 'agent-1', types.SimpleNamespace(id='agent-1'),
        stream_id='stream-1',
    )
    response = await manager.sse_endpoint(SSERequest())
    try:
        replay = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
        assert json.loads(replay['data']) == event
    finally:
        await resolve_question(event['request_id'], 'user-1', 'cancel')
        with pytest.raises(asyncio.CancelledError):
            await task
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_direct_sse_chat_binds_question_context(monkeypatch):
    captured = {}

    class Session:
        id = 'session-1'
        agent_id = 'agent-1'

        async def __call__(self, *args, **kwargs):
            captured['context'] = question_turn_ctx.get()

    manager = SSEManager(types.SimpleNamespace(id='agent-1', llm=object()))
    manager.user_key = 'user-1'
    manager.stream_id = 'stream-1'
    monkeypatch.setattr(manager, '_resolve_session', lambda _sid: asyncio.sleep(0, result=Session()))
    monkeypatch.setattr(
        'cognitrix.utils.sse.load_turn_document_capabilities',
        lambda *args, **kwargs: asyncio.sleep(0, result=()),
    )
    output_queue, terminal_event = manager._open_turn_output()

    await manager._process_chat_action(
        {
            'content': 'hello',
            'session_id': 'session-1',
            'execution_mode': 'chat',
        },
        output_queue,
        terminal_event,
    )

    assert captured['context'].session_id == 'session-1'
    assert captured['context'].stream_id == 'stream-1'
    assert captured['context'].user_key == 'user-1'
