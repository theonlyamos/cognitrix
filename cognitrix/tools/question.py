from __future__ import annotations

from typing import Any

from cognitrix.questions.broker import ask_question
from cognitrix.questions.models import QuestionSpec
from cognitrix.tools.tool import tool


@tool(
    category='system',
    retryable=False,
    max_attempts=1,
    supported_interfaces=['web'],
    occupies_execution_slot=False,
    approval_mode='assigned_only',
)
async def ask_user(
    prompt: str | None = None,
    options: list[dict[str, Any]] | None = None,
    details: str | None = None,
    allow_free_text: bool = False,
    recommended_option_id: str | None = None,
    auto_submit_recommended: bool = False,
    multiple: bool = False,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the person something and wait for the answer: the way to ask any question you need answered before you go on (a choice, a confirmation or a detail only they know). It shows a question card in the chat. Ask one question with prompt and options, or up to 4 related questions on one card with questions (leave prompt and options out then). Give 2-5 options, each with id, a short label and a one-line description, and recommend the one you would pick. Set multiple when several options can apply. A question with no options is answered in words, and the card always lets the person type their own answer instead.

    :param prompt: One question, short and specific (when asking a single question).
    :param options: Up to five choices for that question, each {id, label, description}.
    :param details: Optional context that helps the person choose.
    :param allow_free_text: Ask for a typed answer (no options needed).
    :param recommended_option_id: The id of the option you recommend.
    :param auto_submit_recommended: Use the recommendations if there is no answer in 60 seconds.
    :param multiple: The person may pick several options.
    :param questions: Up to 4 questions shown one after another, each {id, prompt, details, short (1-2 word label), options, multiple, recommended_option_id or recommended_option_ids}.
    """
    spec = QuestionSpec.from_tool_args(
        prompt=prompt,
        options=options,
        details=details,
        allow_free_text=allow_free_text,
        recommended_option_id=recommended_option_id,
        auto_submit_recommended=auto_submit_recommended,
        multiple=multiple,
        questions=questions,
    )
    answer = await ask_question(spec)
    return answer.to_dict()
