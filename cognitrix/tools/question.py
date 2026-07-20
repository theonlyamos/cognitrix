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
    prompt: str,
    options: list[dict[str, Any]] | None = None,
    details: str | None = None,
    allow_free_text: bool = False,
    recommended_option_id: str | None = None,
    auto_submit_recommended: bool = False,
) -> dict[str, Any]:
    """Ask the user one interactive question and wait for their answer.

    :param prompt: The concise question shown to the user.
    :param options: Up to five choices with id, label, and optional description.
    :param details: Optional context that helps the user choose.
    :param allow_free_text: Whether the user may enter a custom answer.
    :param recommended_option_id: The id of the recommended declared option.
    :param auto_submit_recommended: Submit the recommendation after 60 seconds.
    """
    spec = QuestionSpec.from_tool_args(
        prompt=prompt,
        options=options,
        details=details,
        allow_free_text=allow_free_text,
        recommended_option_id=recommended_option_id,
        auto_submit_recommended=auto_submit_recommended,
    )
    answer = await ask_question(spec)
    return answer.to_dict()
