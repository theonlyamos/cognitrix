from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from cognitrix.questions.models import (
    ItemAnswer,
    QuestionAction,
    QuestionAnswer,
    QuestionSpec,
    QuestionValidationError,
)


AUTO_SUBMIT_SECONDS = 60.0


class QuestionChannelUnavailable(RuntimeError):
    pass


class QuestionTurnCancelled(asyncio.CancelledError):
    pass


@dataclass(frozen=True)
class QuestionTurnContext:
    emit: Callable[[dict[str, Any]], Awaitable[None]]
    session_id: str
    stream_id: str
    user_key: str


@dataclass
class PendingQuestion:
    request_id: str
    context: QuestionTurnContext
    spec: QuestionSpec
    future: asyncio.Future[QuestionAnswer | object]
    auto_submit_at: datetime | None

    def event(self) -> dict[str, Any]:
        deadline = self.auto_submit_at
        return {
            'type': 'question_request',
            'request_id': self.request_id,
            'session_id': self.context.session_id,
            **self.spec.to_event(),
            'auto_submit_at': (
                deadline.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
                if deadline is not None else None
            ),
        }


question_turn_ctx: ContextVar[QuestionTurnContext | None] = ContextVar(
    'question_turn_ctx', default=None,
)
_PENDING: dict[str, PendingQuestion] = {}
_PENDING_BY_STREAM: dict[tuple[str, str], str] = {}
_CANCELLED = object()


def _stream_key(user_key: str, stream_id: str) -> tuple[str, str]:
    return str(user_key), str(stream_id)


def pending_question(user_key: str, stream_id: str) -> dict[str, Any] | None:
    request_id = _PENDING_BY_STREAM.get(_stream_key(user_key, stream_id))
    pending = _PENDING.get(request_id or '')
    return pending.event() if pending is not None else None


def cancel_questions_for_stream(user_key: str, stream_id: str) -> None:
    request_id = _PENDING_BY_STREAM.get(_stream_key(user_key, stream_id))
    pending = _PENDING.get(request_id or '')
    if pending is not None and not pending.future.done():
        pending.future.set_result(_CANCELLED)


async def _auto_submit_if_due(pending: PendingQuestion) -> None:
    current = _PENDING.get(pending.request_id)
    if current is not pending or pending.future.done():
        return
    deadline = pending.auto_submit_at
    if deadline is None or deadline > datetime.now(timezone.utc):
        return
    items = pending.spec.items
    if len(items) == 1 and not items[0].multiple:
        option = items[0].option(items[0].recommended_option_id or '')
        if option is not None:
            pending.future.set_result(QuestionAnswer.option(
                option.id, option.label, auto_submitted=True,
            ))
        return
    if all(item.recommended_option_ids for item in items):
        pending.future.set_result(QuestionAnswer.of_items(
            tuple(ItemAnswer(item, item.recommended_option_ids) for item in items),
            auto_submitted=True,
        ))


async def ask_question(spec: QuestionSpec) -> QuestionAnswer:
    context = question_turn_ctx.get()
    if context is None or context.emit is None:
        raise QuestionChannelUnavailable(
            'Ask User requires an active interactive web chat channel',
        )
    key = _stream_key(context.user_key, context.stream_id)
    if key in _PENDING_BY_STREAM:
        raise RuntimeError('A question is already pending for this turn')

    request_id = f'question-{uuid.uuid4().hex}'
    future = asyncio.get_running_loop().create_future()
    deadline = (
        datetime.now(timezone.utc) + timedelta(seconds=AUTO_SUBMIT_SECONDS)
        if spec.auto_submit_recommended else None
    )
    pending = PendingQuestion(request_id, context, spec, future, deadline)
    _PENDING[request_id] = pending
    _PENDING_BY_STREAM[key] = request_id

    try:
        await context.emit(pending.event())
        while True:
            deadline = pending.auto_submit_at
            if deadline is None:
                result = await future
            else:
                remaining = max(
                    0.0, (deadline - datetime.now(timezone.utc)).total_seconds(),
                )
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(future), timeout=remaining,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    await _auto_submit_if_due(pending)
                    continue
            if result is _CANCELLED:
                raise QuestionTurnCancelled('Question cancelled by user')
            assert isinstance(result, QuestionAnswer)
            return result
    finally:
        _PENDING.pop(request_id, None)
        if _PENDING_BY_STREAM.get(key) == request_id:
            _PENDING_BY_STREAM.pop(key, None)


async def resolve_question(
    request_id: str,
    user_key: str,
    action: QuestionAction | str,
    *,
    option_id: str | None = None,
    text: str | None = None,
    answers: list[Any] | None = None,
) -> bool:
    pending = _PENDING.get(str(request_id))
    if pending is None or pending.context.user_key != str(user_key) or pending.future.done():
        return False
    try:
        normalized_action = QuestionAction(action)
    except ValueError:
        return False

    given = answers is not None
    if normalized_action is QuestionAction.STOP_TIMER:
        if given or option_id is not None or text is not None or pending.auto_submit_at is None:
            return False
        pending.auto_submit_at = None
        await pending.context.emit(pending.event())
        return True

    if normalized_action is QuestionAction.CANCEL:
        if given or option_id is not None or text is not None:
            return False
        pending.future.set_result(_CANCELLED)
        return True

    items = pending.spec.items
    if given:
        # The card's answer: one entry per question, in any order.
        if option_id is not None or text is not None or not isinstance(answers, list):
            return False
        by_id = {}
        for value in answers:
            qid = value.get('question_id') if isinstance(value, dict) else None
            if not isinstance(qid, str) or qid in by_id:
                return False
            by_id[qid] = value
        if set(by_id) != {item.id for item in items}:
            return False
        try:
            parsed = tuple(ItemAnswer.parse(item, by_id[item.id]) for item in items)
        except QuestionValidationError:
            return False
        pending.future.set_result(QuestionAnswer.of_items(parsed))
        return True

    has_option = option_id is not None
    has_text = text is not None
    if has_option == has_text:
        return False
    if has_option:
        first = items[0]
        option = first.option(str(option_id))
        if option is None:
            return False
        if len(items) == 1 and not first.multiple:
            pending.future.set_result(QuestionAnswer.option(option.id, option.label))
        else:
            # A chat app or voice answered the first question; the rest
            # were not shown there.
            pending.future.set_result(QuestionAnswer.of_items((
                ItemAnswer(first, (option.id,)),
                *(ItemAnswer(item, skipped=True) for item in items[1:]),
            )))
        return True

    normalized_text = str(text or '').strip()
    if not normalized_text or len(normalized_text) > 4000:
        return False
    # Typed words always answer: the card offers "Something else" on every
    # question, and a reply typed in the chat box answers the whole card.
    pending.future.set_result(QuestionAnswer.free_text(normalized_text))
    return True


def pending_secret(request_id: str, user_key: str) -> dict[str, Any] | None:
    """The sign-in an open Ask For Sign-in card asks this person for, or None."""
    pending = _PENDING.get(str(request_id))
    if pending is None or pending.context.user_key != str(user_key) or pending.future.done():
        return None
    return dict(pending.spec.secret) if pending.spec.secret else None
