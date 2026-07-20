import asyncio

import pytest

import cognitrix.questions.broker as broker
from cognitrix.questions.broker import (
    QuestionChannelUnavailable,
    QuestionTurnCancelled,
    QuestionTurnContext,
    ask_question,
    cancel_questions_for_stream,
    pending_question,
    question_turn_ctx,
    resolve_question,
)
from cognitrix.questions.models import QuestionAction, QuestionSpec


@pytest.fixture(autouse=True)
def clean_broker():
    broker._PENDING.clear()
    broker._PENDING_BY_STREAM.clear()
    yield
    broker._PENDING.clear()
    broker._PENDING_BY_STREAM.clear()


async def _start(spec):
    events = []

    async def emit(event):
        events.append(event)

    token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit,
        session_id='session-1',
        stream_id='stream-1',
        user_key='user-1',
    ))
    task = asyncio.create_task(ask_question(spec))
    try:
        for _ in range(100):
            if events:
                break
            await asyncio.sleep(0)
        assert events
        return task, events
    finally:
        question_turn_ctx.reset(token)


def _choice(*, auto=False):
    return QuestionSpec.from_tool_args(
        prompt='Choose',
        options=[
            {'id': 'background', 'label': 'Background'},
            {'id': 'chat', 'label': 'Keep in chat'},
        ],
        recommended_option_id='background' if auto else None,
        auto_submit_recommended=auto,
    )


@pytest.mark.asyncio
async def test_ask_question_requires_an_interactive_channel():
    with pytest.raises(QuestionChannelUnavailable):
        await ask_question(_choice())


@pytest.mark.asyncio
async def test_option_answer_is_owner_scoped_single_use_and_cleans_up():
    task, events = await _start(_choice())
    request_id = events[0]['request_id']

    assert pending_question('user-1', 'stream-1')['request_id'] == request_id
    assert await resolve_question(
        request_id, 'intruder', QuestionAction.ANSWER, option_id='background',
    ) is False
    assert await resolve_question(
        request_id, 'user-1', QuestionAction.ANSWER, option_id='background',
    ) is True

    answer = await task
    assert answer.option_id == 'background'
    assert answer.text == 'Background'
    assert pending_question('user-1', 'stream-1') is None
    assert await resolve_question(
        request_id, 'user-1', QuestionAction.ANSWER, option_id='chat',
    ) is False


@pytest.mark.asyncio
async def test_free_text_requires_permission_and_is_bounded():
    task, events = await _start(QuestionSpec.from_tool_args(
        prompt='Explain', allow_free_text=True,
    ))
    request_id = events[0]['request_id']
    assert await resolve_question(
        request_id, 'user-1', QuestionAction.ANSWER, text='  My answer  ',
    ) is True
    assert (await task).text == 'My answer'

    task, events = await _start(_choice())
    request_id = events[0]['request_id']
    assert await resolve_question(
        request_id, 'user-1', QuestionAction.ANSWER, text='not allowed',
    ) is False
    cancel_questions_for_stream('user-1', 'stream-1')
    with pytest.raises(QuestionTurnCancelled):
        await task


@pytest.mark.asyncio
async def test_only_one_question_can_wait_per_stream():
    first, _events = await _start(_choice())
    token = question_turn_ctx.set(QuestionTurnContext(
        emit=lambda _event: asyncio.sleep(0),
        session_id='session-1', stream_id='stream-1', user_key='user-1',
    ))
    try:
        with pytest.raises(RuntimeError, match='already pending'):
            await ask_question(_choice())
    finally:
        question_turn_ctx.reset(token)
        cancel_questions_for_stream('user-1', 'stream-1')
    with pytest.raises(QuestionTurnCancelled):
        await first


@pytest.mark.asyncio
async def test_cancel_raises_turn_cancel_signal_and_cleans_up():
    task, events = await _start(_choice())
    assert await resolve_question(
        events[0]['request_id'], 'user-1', QuestionAction.CANCEL,
    ) is True
    with pytest.raises(QuestionTurnCancelled):
        await task
    assert not broker._PENDING


@pytest.mark.asyncio
async def test_stop_timer_reemits_state_and_keeps_question_pending(monkeypatch):
    monkeypatch.setattr(broker, 'AUTO_SUBMIT_SECONDS', 0.03)
    task, events = await _start(_choice(auto=True))
    request_id = events[0]['request_id']
    assert events[0]['auto_submit_at'] is not None

    assert await resolve_question(
        request_id, 'user-1', QuestionAction.STOP_TIMER,
    ) is True
    assert events[-1]['request_id'] == request_id
    assert events[-1]['auto_submit_at'] is None
    await asyncio.sleep(0.05)
    assert not task.done(), 'the expired pre-stop wait must recheck the cleared deadline'

    assert await resolve_question(
        request_id, 'user-1', QuestionAction.ANSWER, option_id='chat',
    ) is True
    assert (await task).option_id == 'chat'


@pytest.mark.asyncio
async def test_deadline_auto_submits_recommended_answer_without_cancelling_future(monkeypatch):
    monkeypatch.setattr(broker, 'AUTO_SUBMIT_SECONDS', 0.01)
    task, events = await _start(_choice(auto=True))

    answer = await asyncio.wait_for(task, timeout=1)
    assert answer.option_id == 'background'
    assert answer.auto_submitted is True
    assert pending_question('user-1', 'stream-1') is None
    assert events[0]['auto_submit_at'].endswith('Z')
