from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class QuestionValidationError(ValueError):
    pass


class QuestionAction(StrEnum):
    ANSWER = 'answer'
    CANCEL = 'cancel'
    STOP_TIMER = 'stop_timer'


def _required_text(value: object, label: str, maximum: int) -> str:
    text = str(value or '').strip()
    if not text:
        raise QuestionValidationError(f'{label} is required')
    if len(text) > maximum:
        raise QuestionValidationError(f'{label} must be at most {maximum} characters')
    return text


def _optional_text(value: object, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise QuestionValidationError(f'{label} must be at most {maximum} characters')
    return text


@dataclass(frozen=True)
class QuestionOption:
    id: str
    label: str
    description: str | None = None

    @classmethod
    def from_value(cls, value: object) -> 'QuestionOption':
        if not isinstance(value, dict):
            raise QuestionValidationError('Each option must be an object')
        return cls(
            id=_required_text(value.get('id'), 'Option id', 80),
            label=_required_text(value.get('label'), 'Option label', 200),
            description=_optional_text(value.get('description'), 'Option description', 500),
        )

    def to_dict(self) -> dict[str, str | None]:
        return {'id': self.id, 'label': self.label, 'description': self.description}


@dataclass(frozen=True)
class QuestionSpec:
    prompt: str
    details: str | None
    options: tuple[QuestionOption, ...]
    allow_free_text: bool
    recommended_option_id: str | None
    auto_submit_recommended: bool

    @property
    def auto_submit_seconds(self) -> int | None:
        return 60 if self.auto_submit_recommended else None

    @classmethod
    def from_tool_args(
        cls,
        prompt: str,
        options: list[dict[str, Any]] | None = None,
        details: str | None = None,
        allow_free_text: bool = False,
        recommended_option_id: str | None = None,
        auto_submit_recommended: bool = False,
    ) -> 'QuestionSpec':
        if not isinstance(allow_free_text, bool) or not isinstance(auto_submit_recommended, bool):
            raise QuestionValidationError('Question flags must be true or false')
        raw_options = options or []
        if not isinstance(raw_options, list):
            raise QuestionValidationError('Options must be a list')
        if len(raw_options) > 5:
            raise QuestionValidationError('A question may contain at most 5 options')
        normalized = tuple(QuestionOption.from_value(value) for value in raw_options)
        ids = [option.id for option in normalized]
        if len(ids) != len(set(ids)):
            raise QuestionValidationError('Option ids must be unique')
        if not normalized and not allow_free_text:
            raise QuestionValidationError('A question requires at least one option or free-text input')
        recommendation = _optional_text(
            recommended_option_id, 'Recommended option id', 80,
        )
        if recommendation is not None and recommendation not in ids:
            raise QuestionValidationError('Recommended option id must reference a declared option')
        if auto_submit_recommended and recommendation is None:
            raise QuestionValidationError('Automatic submission requires a recommended option')
        return cls(
            prompt=_required_text(prompt, 'Prompt', 2000),
            details=_optional_text(details, 'Details', 4000),
            options=normalized,
            allow_free_text=allow_free_text,
            recommended_option_id=recommendation,
            auto_submit_recommended=auto_submit_recommended,
        )

    def to_event(self) -> dict[str, Any]:
        return {
            'prompt': self.prompt,
            'details': self.details,
            'options': [option.to_dict() for option in self.options],
            'allow_free_text': self.allow_free_text,
            'recommended_option_id': self.recommended_option_id,
            'auto_submit_seconds': self.auto_submit_seconds,
        }


@dataclass(frozen=True)
class QuestionAnswer:
    status: str
    answer_type: str
    option_id: str | None
    text: str
    auto_submitted: bool = False

    @classmethod
    def option(
        cls, option_id: str, label: str, *, auto_submitted: bool = False,
    ) -> 'QuestionAnswer':
        return cls('answered', 'option', option_id, label, auto_submitted)

    @classmethod
    def free_text(cls, text: str) -> 'QuestionAnswer':
        return cls('answered', 'text', None, text, False)

    def to_dict(self) -> dict[str, Any]:
        return {
            'status': self.status,
            'answer_type': self.answer_type,
            'option_id': self.option_id,
            'text': self.text,
            'auto_submitted': self.auto_submitted,
        }
