"""Ask User cards with several questions, multiple choice and the answers
the agent gets back (cognitrix/questions)."""
import asyncio

import pytest

import cognitrix.questions.broker as broker
from cognitrix.questions.broker import (
    QuestionTurnContext,
    ask_question,
    question_turn_ctx,
    resolve_question,
)
from cognitrix.questions.guidance import GUIDANCE, has_ask_user
from cognitrix.questions.models import (
    QuestionAction,
    QuestionSpec,
    QuestionValidationError,
    batch_text,
)


@pytest.fixture(autouse=True)
def clean_broker():
    broker._PENDING.clear()
    broker._PENDING_BY_STREAM.clear()
    yield
    broker._PENDING.clear()
    broker._PENDING_BY_STREAM.clear()


def _batch(**extra):
    return QuestionSpec.from_tool_args(questions=[
        {'id': 'db', 'short': 'Database', 'prompt': 'Which database?',
         'options': [{'id': 'sqlite', 'label': 'SQLite'}, {'id': 'pg', 'label': 'Postgres'}],
         'recommended_option_id': 'sqlite'},
        {'id': 'auth', 'short': 'Sign-in', 'prompt': 'Which sign-ins?', 'multiple': True,
         'options': [{'id': 'google', 'label': 'Google'}, {'id': 'github', 'label': 'GitHub'},
                     {'id': 'email', 'label': 'Email'}],
         'recommended_option_ids': ['google', 'github']},
        {'id': 'name', 'short': 'Name', 'prompt': 'What should it be called?'},
    ], **extra)


async def _start(spec):
    events = []

    async def emit(event):
        events.append(event)

    token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit, session_id='s', stream_id='stream-1', user_key='user-1',
    ))
    task = asyncio.create_task(ask_question(spec))
    await asyncio.sleep(0)
    question_turn_ctx.reset(token)
    return task, events


def test_batch_spec_mirrors_the_first_question_for_one_at_a_time_places():
    spec = _batch()
    event = spec.to_event()
    assert event['prompt'] == 'Which database?'
    assert [q['id'] for q in event['questions']] == ['db', 'auth', 'name']
    assert event['questions'][1]['multiple'] is True
    assert event['questions'][1]['recommended_option_ids'] == ['google', 'github']
    # No options means a typed answer.
    assert event['questions'][2]['allow_free_text'] is True
    text = batch_text(event)
    assert '2. Which sign-ins? (pick any)' in text and 'SQLite (recommended)' in text


def test_single_question_event_has_one_entry_and_no_batch_text():
    event = QuestionSpec.from_tool_args(
        prompt='Pick', options=[{'id': 'a', 'label': 'A'}], multiple=True,
    ).to_event()
    assert len(event['questions']) == 1 and event['multiple'] is True
    assert batch_text(event) is None


@pytest.mark.parametrize('kwargs, match', [
    ({'questions': [{'prompt': f'Q{i}'} for i in range(5)]}, 'at most 4'),
    ({'questions': [{'id': 'x', 'prompt': 'A'}, {'id': 'x', 'prompt': 'B'}]}, 'unique'),
    ({'questions': [{'prompt': 'A', 'colour': 'red'}]}, 'Unknown question fields'),
    ({'questions': [{'prompt': 'A'}], 'options': [{'id': 'a', 'label': 'A'}]}, 'inside each question'),
    ({'prompt': 'A', 'options': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}],
      'recommended_option_id': 'a', 'auto_submit_recommended': True, 'multiple': False}, None),
])
def test_batch_validation(kwargs, match):
    if match is None:
        QuestionSpec.from_tool_args(**kwargs)
        return
    with pytest.raises(QuestionValidationError, match=match):
        QuestionSpec.from_tool_args(**kwargs)


def test_only_multiple_choice_may_recommend_several():
    with pytest.raises(QuestionValidationError, match='several'):
        QuestionSpec.from_tool_args(questions=[{
            'prompt': 'Pick', 'options': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}],
            'recommended_option_ids': ['a', 'b'],
        }])


@pytest.mark.asyncio
async def test_card_answers_come_back_per_question():
    task, events = await _start(_batch())
    rid = events[0]['request_id']
    # Every question must be answered or skipped, with options it offers.
    assert not await resolve_question(rid, 'user-1', QuestionAction.ANSWER, answers=[
        {'question_id': 'db', 'option_ids': ['sqlite']},
    ])
    assert not await resolve_question(rid, 'user-1', QuestionAction.ANSWER, answers=[
        {'question_id': 'db', 'option_ids': ['sqlite', 'pg']},
        {'question_id': 'auth', 'option_ids': ['google']},
        {'question_id': 'name', 'text': 'Ledgerly'},
    ])
    assert await resolve_question(rid, 'user-1', QuestionAction.ANSWER, answers=[
        {'question_id': 'name', 'text': ' Ledgerly '},
        {'question_id': 'db', 'option_ids': ['sqlite']},
        {'question_id': 'auth', 'option_ids': ['google', 'github'], 'text': 'Apple too'},
    ])
    result = (await task).to_dict()
    assert result['answer_type'] == 'answers'
    db, auth, name = result['answers']
    assert db['choices'] == ['SQLite'] and db['recommended'] is True
    assert auth['choices'] == ['Google', 'GitHub'] and auth['text'] == 'Apple too'
    assert name == {**name, 'question': 'What should it be called?', 'text': 'Ledgerly', 'skipped': False}
    assert 'Which sign-ins? Google, GitHub, Apple too' in result['text']


@pytest.mark.asyncio
async def test_skipped_questions_and_single_question_answers_keep_the_old_shape():
    task, events = await _start(QuestionSpec.from_tool_args(
        prompt='Continue?', options=[{'id': 'yes', 'label': 'Yes'}, {'id': 'no', 'label': 'No'}],
    ))
    assert await resolve_question(events[0]['request_id'], 'user-1', 'answer', answers=[
        {'question_id': 'q1', 'option_ids': ['yes']},
    ])
    result = (await task).to_dict()
    assert result['answer_type'] == 'option' and result['option_id'] == 'yes' and result['text'] == 'Yes'
    assert result['answers'][0]['choices'] == ['Yes']

    task, events = await _start(_batch())
    assert await resolve_question(events[0]['request_id'], 'user-1', 'answer', answers=[
        {'question_id': 'db', 'skipped': True},
        {'question_id': 'auth', 'skipped': True},
        {'question_id': 'name', 'text': 'Ledgerly'},
    ])
    result = (await task).to_dict()
    assert [a['skipped'] for a in result['answers']] == [True, True, False]


@pytest.mark.asyncio
async def test_a_chat_app_reply_answers_a_whole_card_in_words():
    task, events = await _start(_batch())
    assert await resolve_question(events[0]['request_id'], 'user-1', 'answer', text='sqlite, google, Ledgerly')
    result = (await task).to_dict()
    assert result['answer_type'] == 'text' and result['text'] == 'sqlite, google, Ledgerly'


@pytest.mark.asyncio
async def test_auto_submit_uses_every_recommendation(monkeypatch):
    monkeypatch.setattr(broker, 'AUTO_SUBMIT_SECONDS', 0.01)
    spec = QuestionSpec.from_tool_args(questions=[
        {'id': 'db', 'prompt': 'Which database?', 'options': [{'id': 'sqlite', 'label': 'SQLite'}],
         'recommended_option_id': 'sqlite'},
        {'id': 'auth', 'prompt': 'Which sign-ins?', 'multiple': True,
         'options': [{'id': 'google', 'label': 'Google'}, {'id': 'github', 'label': 'GitHub'}],
         'recommended_option_ids': ['google', 'github']},
    ], auto_submit_recommended=True)
    task, _ = await _start(spec)
    result = (await asyncio.wait_for(task, 2)).to_dict()
    assert result['auto_submitted'] is True
    assert [a['choices'] for a in result['answers']] == [['SQLite'], ['Google', 'GitHub']]
    with pytest.raises(QuestionValidationError, match='requires a recommended'):
        _batch(auto_submit_recommended=True)


def test_guidance_tells_agents_to_use_the_card_and_when_text_is_fine():
    assert 'ask with Ask User' in GUIDANCE and 'plain text only when Ask User' in GUIDANCE
    assert has_ask_user([type('T', (), {'name': 'Ask User'})()])
    assert not has_ask_user([type('T', (), {'name': 'Notify Me'})()])
