import pytest

from cognitrix.questions.models import (
    QuestionAction,
    QuestionAnswer,
    QuestionSpec,
    QuestionValidationError,
)


def test_question_spec_normalizes_valid_options_and_recommendation():
    spec = QuestionSpec.from_tool_args(
        prompt='  Run this in the background?  ',
        details='  You can keep chatting while it runs. ',
        options=[
            {'id': 'background', 'label': 'Background', 'description': 'Create a task.'},
            {'id': 'chat', 'label': 'Keep in chat'},
        ],
        allow_free_text=False,
        recommended_option_id='background',
        auto_submit_recommended=True,
    )

    assert spec.prompt == 'Run this in the background?'
    assert spec.details == 'You can keep chatting while it runs.'
    assert spec.options[0].id == 'background'
    assert spec.auto_submit_seconds == 60
    assert spec.to_event()['options'][1] == {
        'id': 'chat', 'label': 'Keep in chat', 'description': None,
    }


@pytest.mark.parametrize('prompt', ['', '   ', 'x' * 2001])
def test_question_spec_rejects_invalid_prompt(prompt):
    with pytest.raises(QuestionValidationError):
        QuestionSpec.from_tool_args(prompt=prompt, options=[{'id': 'yes', 'label': 'Yes'}])


def test_question_spec_requires_a_response_mechanism():
    with pytest.raises(QuestionValidationError, match='option or free-text'):
        QuestionSpec.from_tool_args(prompt='Choose', options=[], allow_free_text=False)


def test_question_spec_rejects_duplicate_and_excess_options():
    with pytest.raises(QuestionValidationError, match='unique'):
        QuestionSpec.from_tool_args(
            prompt='Choose',
            options=[{'id': 'same', 'label': 'A'}, {'id': 'same', 'label': 'B'}],
        )
    with pytest.raises(QuestionValidationError, match='at most 5'):
        QuestionSpec.from_tool_args(
            prompt='Choose',
            options=[{'id': str(index), 'label': str(index)} for index in range(6)],
        )


def test_question_spec_rejects_unknown_or_missing_auto_submit_recommendation():
    with pytest.raises(QuestionValidationError, match='declared option'):
        QuestionSpec.from_tool_args(
            prompt='Choose', options=[{'id': 'yes', 'label': 'Yes'}],
            recommended_option_id='no',
        )
    with pytest.raises(QuestionValidationError, match='requires a recommended option'):
        QuestionSpec.from_tool_args(
            prompt='Choose', options=[{'id': 'yes', 'label': 'Yes'}],
            auto_submit_recommended=True,
        )


def test_question_answer_serializes_option_and_text_answers():
    option = QuestionAnswer.option('background', 'Background', auto_submitted=True)
    text = QuestionAnswer.free_text('A custom answer')

    assert option.to_dict() == {
        'status': 'answered',
        'answer_type': 'option',
        'option_id': 'background',
        'text': 'Background',
        'auto_submitted': True,
    }
    assert text.answer_type == 'text'
    assert QuestionAction.STOP_TIMER.value == 'stop_timer'
