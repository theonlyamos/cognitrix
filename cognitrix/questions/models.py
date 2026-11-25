from __future__ import annotations

from dataclasses import dataclass, field
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


MAX_QUESTIONS = 4
MAX_OPTIONS = 5


def _options(raw: object) -> tuple[QuestionOption, ...]:
    raw_options = raw or []
    if not isinstance(raw_options, list):
        raise QuestionValidationError('Options must be a list')
    if len(raw_options) > MAX_OPTIONS:
        raise QuestionValidationError(f'A question may contain at most {MAX_OPTIONS} options')
    normalized = tuple(QuestionOption.from_value(value) for value in raw_options)
    ids = [option.id for option in normalized]
    if len(ids) != len(set(ids)):
        raise QuestionValidationError('Option ids must be unique')
    return normalized


def _recommended(value: object, many: object, ids: list[str]) -> tuple[str, ...]:
    picked: list[str] = []
    if many is not None:
        if not isinstance(many, list):
            raise QuestionValidationError('Recommended option ids must be a list')
        picked.extend(str(v) for v in many if str(v or '').strip())
    one = _optional_text(value, 'Recommended option id', 80)
    if one is not None and one not in picked:
        picked.insert(0, one)
    for rec in picked:
        if rec not in ids:
            raise QuestionValidationError('Recommended option id must reference a declared option')
    return tuple(picked)


@dataclass(frozen=True)
class QuestionItem:
    """One question in an Ask User card. A card holds one to four of them,
    shown one at a time. A question with no options is answered in words."""
    id: str
    prompt: str
    details: str | None
    options: tuple[QuestionOption, ...]
    multiple: bool
    allow_free_text: bool
    recommended_option_ids: tuple[str, ...]
    short: str | None = None

    @property
    def recommended_option_id(self) -> str | None:
        return self.recommended_option_ids[0] if self.recommended_option_ids else None

    @classmethod
    def build(
        cls, *, id: str, prompt: object, details: object = None, options: object = None,
        multiple: object = False, allow_free_text: object = False,
        recommended_option_id: object = None, recommended_option_ids: object = None,
        short: object = None,
    ) -> 'QuestionItem':
        if not isinstance(multiple, bool) or not isinstance(allow_free_text, bool):
            raise QuestionValidationError('Question flags must be true or false')
        normalized = _options(options)
        if not normalized and not allow_free_text:
            raise QuestionValidationError('A question requires at least one option or free-text input')
        recommended = _recommended(
            recommended_option_id, recommended_option_ids, [o.id for o in normalized],
        )
        if len(recommended) > 1 and not multiple:
            raise QuestionValidationError('Only a multiple-choice question may recommend several options')
        return cls(
            id=id,
            prompt=_required_text(prompt, 'Prompt', 2000),
            details=_optional_text(details, 'Details', 4000),
            options=normalized,
            multiple=multiple and bool(normalized),
            allow_free_text=allow_free_text,
            recommended_option_ids=recommended,
            short=_optional_text(short, 'Short label', 40),
        )

    @classmethod
    def from_value(cls, value: object, index: int) -> 'QuestionItem':
        if not isinstance(value, dict):
            raise QuestionValidationError('Each question must be an object')
        allowed = {
            'id', 'prompt', 'question', 'details', 'options', 'multiple', 'allow_free_text',
            'recommended_option_id', 'recommended_option_ids', 'short', 'label',
        }
        unknown = set(value) - allowed
        if unknown:
            raise QuestionValidationError(f'Unknown question fields: {", ".join(sorted(unknown))}')
        options = value.get('options')
        return cls.build(
            id=_optional_text(value.get('id'), 'Question id', 80) or f'q{index + 1}',
            prompt=value.get('prompt') if value.get('prompt') is not None else value.get('question'),
            details=value.get('details'),
            options=options,
            multiple=value.get('multiple', False),
            # A question without options is answered in words.
            allow_free_text=value.get('allow_free_text', not options),
            recommended_option_id=value.get('recommended_option_id'),
            recommended_option_ids=value.get('recommended_option_ids'),
            short=value.get('short') if value.get('short') is not None else value.get('label'),
        )

    def option(self, option_id: str) -> QuestionOption | None:
        return next((o for o in self.options if o.id == option_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'prompt': self.prompt,
            'details': self.details,
            'short': self.short,
            'options': [option.to_dict() for option in self.options],
            'multiple': self.multiple,
            'allow_free_text': self.allow_free_text,
            'recommended_option_id': self.recommended_option_id,
            'recommended_option_ids': list(self.recommended_option_ids),
        }


@dataclass(frozen=True)
class QuestionSpec:
    prompt: str
    details: str | None
    options: tuple[QuestionOption, ...]
    allow_free_text: bool
    recommended_option_id: str | None
    auto_submit_recommended: bool
    # Ask For Sign-in: the card shows a masked field instead of options, and
    # the browser saves what's typed straight to the person's saved sign-ins
    # ({name, kind, hosts}); the answer itself only says "saved".
    secret: dict[str, Any] | None = field(default=None)
    # Every question on the card, in order (the fields above mirror the first
    # one for chat apps, mail and voice, which show one question at a time).
    questions: tuple[QuestionItem, ...] = field(default=())

    @property
    def items(self) -> tuple[QuestionItem, ...]:
        if self.questions:
            return self.questions
        return (QuestionItem(
            id='q1', prompt=self.prompt, details=self.details, options=self.options,
            multiple=False, allow_free_text=self.allow_free_text,
            recommended_option_ids=(self.recommended_option_id,) if self.recommended_option_id else (),
        ),)

    @property
    def auto_submit_seconds(self) -> int | None:
        return 60 if self.auto_submit_recommended else None

    @classmethod
    def from_tool_args(
        cls,
        prompt: str | None = None,
        options: list[dict[str, Any]] | None = None,
        details: str | None = None,
        allow_free_text: bool = False,
        recommended_option_id: str | None = None,
        auto_submit_recommended: bool = False,
        multiple: bool = False,
        questions: list[dict[str, Any]] | None = None,
    ) -> 'QuestionSpec':
        if not isinstance(allow_free_text, bool) or not isinstance(auto_submit_recommended, bool):
            raise QuestionValidationError('Question flags must be true or false')
        if questions:
            if not isinstance(questions, list):
                raise QuestionValidationError('Questions must be a list')
            if options or recommended_option_id or multiple:
                raise QuestionValidationError(
                    'Put options inside each question when asking several questions')
            if len(questions) > MAX_QUESTIONS:
                raise QuestionValidationError(f'Ask at most {MAX_QUESTIONS} questions at once')
            items = tuple(QuestionItem.from_value(value, i) for i, value in enumerate(questions))
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise QuestionValidationError('Question ids must be unique')
            # A lone prompt next to several questions is the card's heading,
            # which the first question already gives.
        else:
            items = (QuestionItem.build(
                id='q1', prompt=prompt, details=details, options=options,
                multiple=multiple, allow_free_text=allow_free_text,
                recommended_option_id=recommended_option_id,
            ),)
        if auto_submit_recommended and not all(item.recommended_option_ids for item in items):
            raise QuestionValidationError('Automatic submission requires a recommended option')
        first = items[0]
        return cls(
            prompt=first.prompt,
            details=first.details,
            options=first.options,
            allow_free_text=first.allow_free_text,
            recommended_option_id=first.recommended_option_id,
            auto_submit_recommended=auto_submit_recommended,
            questions=items,
        )

    def to_event(self) -> dict[str, Any]:
        return {
            'prompt': self.prompt,
            'details': self.details,
            'options': [option.to_dict() for option in self.options],
            'allow_free_text': self.allow_free_text,
            'recommended_option_id': self.recommended_option_id,
            'auto_submit_seconds': self.auto_submit_seconds,
            'multiple': self.items[0].multiple,
            'questions': [item.to_dict() for item in self.items],
            **({'secret': dict(self.secret)} if self.secret else {}),
        }


@dataclass(frozen=True)
class QuestionAnswer:
    status: str
    answer_type: str
    option_id: str | None
    text: str
    auto_submitted: bool = False
    # One entry per question on the card (see ItemAnswer.to_dict).
    answers: tuple['ItemAnswer', ...] = ()

    @classmethod
    def option(
        cls, option_id: str, label: str, *, auto_submitted: bool = False,
    ) -> 'QuestionAnswer':
        return cls('answered', 'option', option_id, label, auto_submitted)

    @classmethod
    def free_text(cls, text: str) -> 'QuestionAnswer':
        return cls('answered', 'text', None, text, False)

    @classmethod
    def of_items(
        cls, answers: tuple['ItemAnswer', ...], *, auto_submitted: bool = False,
    ) -> 'QuestionAnswer':
        first = answers[0] if answers else None
        if len(answers) == 1 and first is not None and not first.skipped:
            if first.text and not first.option_ids:
                return cls('answered', 'text', None, first.text, False, answers)
            if len(first.option_ids) == 1 and not first.text:
                return cls('answered', 'option', first.option_ids[0], first.summary(),
                           auto_submitted, answers)
        summary = '\n'.join(f'{a.item.prompt} {a.summary()}' for a in answers)
        return cls('answered', 'answers', None, summary, auto_submitted, answers)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            'status': self.status,
            'answer_type': self.answer_type,
            'option_id': self.option_id,
            'text': self.text,
            'auto_submitted': self.auto_submitted,
        }
        if self.answers:
            out['answers'] = [a.to_dict() for a in self.answers]
        return out


@dataclass(frozen=True)
class ItemAnswer:
    """The person's answer to one question: options they picked, words they
    typed ("Something else"), or nothing when they skipped it."""
    item: QuestionItem
    option_ids: tuple[str, ...] = ()
    text: str = ''
    skipped: bool = False

    def summary(self) -> str:
        if self.skipped:
            return '(skipped)'
        parts = [self.item.option(o).label for o in self.option_ids if self.item.option(o)]
        if self.text:
            parts.append(self.text)
        return ', '.join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            'question_id': self.item.id,
            'question': self.item.prompt,
            'short': self.item.short,
            'option_ids': list(self.option_ids),
            'choices': [self.item.option(o).label for o in self.option_ids if self.item.option(o)],
            'text': self.text or None,
            'skipped': self.skipped,
            'recommended': bool(self.option_ids) and set(self.option_ids) == set(self.item.recommended_option_ids),
        }

    @classmethod
    def parse(cls, item: QuestionItem, value: object) -> 'ItemAnswer':
        if not isinstance(value, dict):
            raise QuestionValidationError('Each answer must be an object')
        if value.get('skipped') is True:
            return cls(item, skipped=True)
        raw_ids = value.get('option_ids') or []
        if not isinstance(raw_ids, list) or not all(isinstance(v, str) for v in raw_ids):
            raise QuestionValidationError('option_ids must be a list of ids')
        ids = tuple(dict.fromkeys(raw_ids))
        if any(item.option(o) is None for o in ids):
            raise QuestionValidationError('Unknown option')
        if len(ids) > 1 and not item.multiple:
            raise QuestionValidationError('Pick one option')
        text = value.get('text')
        if text is not None and not isinstance(text, str):
            raise QuestionValidationError('Invalid answer text')
        text = (text or '').strip()
        if len(text) > 4000:
            raise QuestionValidationError('Answer text is too long')
        if not ids and not text:
            raise QuestionValidationError('Answer every question or skip it')
        if ids and text and not item.multiple:
            raise QuestionValidationError('Pick an option or type an answer, not both')
        return cls(item, ids, text)


def batch_text(event: dict[str, Any]) -> str | None:
    """Several questions on one card, as numbered plain text for places that
    show one message at a time (chat apps, mail). None for a single question."""
    questions = [q for q in event.get('questions') or [] if isinstance(q, dict)]
    if len(questions) < 2:
        return None
    lines: list[str] = []
    for n, q in enumerate(questions, 1):
        lines.append(f'{n}. {q.get("prompt") or ""}' + (' (pick any)' if q.get('multiple') else ''))
        recommended = set(q.get('recommended_option_ids') or [])
        for o in q.get('options') or []:
            if isinstance(o, dict):
                mark = ' (recommended)' if o.get('id') in recommended else ''
                lines.append(f'   - {o.get("label")}{mark}')
    lines.append('')
    lines.append('Reply with your answers, one per line.')
    return '\n'.join(lines)
