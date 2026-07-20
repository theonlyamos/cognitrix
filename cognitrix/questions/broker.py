from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from cognitrix.questions.models import (
    QuestionAction,
    QuestionAnswer,
    QuestionSpec,
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
    option_id = pending.spec.recommended_option_id
    option = next(
        (item for item in pending.spec.options if item.id == option_id), None,
    )
    if option is not None:
        pending.future.set_result(QuestionAnswer.option(
            option.id, option.label, auto_submitted=True,
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
) -> bool:
    pending = _PENDING.get(str(request_id))
    if pending is None or pending.context.user_key != str(user_key) or pending.future.done():
        return False
    try:
        normalized_action = QuestionAction(action)
    except ValueError:
        return False

    if normalized_action is QuestionAction.STOP_TIMER:
        if option_id is not None or text is not None or pending.auto_submit_at is None:
            return False
        pending.auto_submit_at = None
        await pending.context.emit(pending.event())
        return True

    if normalized_action is QuestionAction.CANCEL:
        if option_id is not None or text is not None:
            return False
        pending.future.set_result(_CANCELLED)
        return True

    has_option = option_id is not None
    has_text = text is not None
    if has_option == has_text:
        return False
    if has_option:
        option = next(
            (item for item in pending.spec.options if item.id == option_id), None,
        )
        if option is None:
            return False
        pending.future.set_result(QuestionAnswer.option(option.id, option.label))
        return True

    normalized_text = str(text or '').strip()
    if (
        not pending.spec.allow_free_text
        or not normalized_text
        or len(normalized_text) > 4000
    ):
        return False
    pending.future.set_result(QuestionAnswer.free_text(normalized_text))
    return True
