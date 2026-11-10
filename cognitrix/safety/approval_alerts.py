"""Approval prompts that reach the person when they aren't looking at the chat.

A chat turn that needs approval shows an Approve/Deny card in the chat. While
the chat is on screen, the page reports the card as seen every few seconds.
When nobody answers it and nobody is looking (never seen within a few
seconds, or not seen for a minute), the same prompt goes out as a
notification:

* web push to the person's phones and browsers, with Approve and Deny buttons
  where the platform has them (``webpush.send_soon``);
* the Inbox, which the app polls, so the desktop app raises a Windows toast
  (see ``pending_chat_approvals``, used by ``GET /inbox``);
* linked chat apps (Telegram, Slack, Discord), whose delivery pass calls
  ``due_chat_approvals``.

A notification's Approve/Deny buttons answer through ``POST /push/approval``
with a short-lived signed token, because a service worker has no sign-in.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger('cognitrix.log')

_background: set[asyncio.Task] = set()

ACTION_TOKEN_TTL = 60 * 60
_TOKEN_TYPE = 'approval_action'


def alert_delay() -> float:
    """Seconds an unanswered chat approval waits before it is sent elsewhere."""
    try:
        return max(0.0, float(os.getenv('COGNITRIX_APPROVAL_ALERT_DELAY', '10')))
    except ValueError:
        return 10.0


def away_delay() -> float:
    """Seconds after the chat page last showed the card before it counts as
    left unanswered. Longer than the page's 15-second report interval."""
    try:
        return max(0.0, float(os.getenv('COGNITRIX_APPROVAL_AWAY_DELAY', '60')))
    except ValueError:
        return 60.0


def _pending() -> dict[str, dict]:
    from cognitrix.safety.approval_gate import _WEB_PENDING

    return _WEB_PENDING


def _open(entry: dict | None) -> bool:
    if not entry:
        return False
    future = entry.get('future')
    return future is None or not future.done()


def tool_label(name: str) -> str:
    return str(name or 'a tool').replace('_', ' ')


def params_preview(params: Any, limit: int = 600) -> str:
    command = params.get('command') if isinstance(params, dict) else None
    if isinstance(command, str):
        text = command
    else:
        try:
            text = json.dumps(params, ensure_ascii=False, indent=1, default=str)
        except (TypeError, ValueError):
            text = str(params)
    return text if len(text) <= limit else text[:limit] + '…'


def headline(entry: dict) -> str:
    who = entry.get('agent_name') or 'An agent'
    return f'{who} wants to run {tool_label(entry.get("tool_name", ""))}'


def public_item(request_id: str, entry: dict) -> dict[str, Any]:
    """A chat approval as the Inbox lists it."""
    return {
        'kind': 'approval',
        'source': 'chat',
        'id': request_id,
        'run_id': '',
        'task_id': None,
        'step_index': None,
        'session_id': entry.get('session_id') or '',
        'href': entry.get('href') or '/inbox',
        'agent_name': entry.get('agent_name') or '',
        'tool_name': entry.get('tool_name') or '',
        'params': entry.get('params') or {},
        'risk_level': entry.get('risk_level') or '',
        'details': entry.get('details') or '',
        'full_shell': bool(entry.get('person_only')),
        'status': 'pending',
        'created_at': entry.get('created_at') or '',
        'task_title': '',
    }


def pending_chat_approvals(user_key: str) -> list[dict[str, Any]]:
    """Chat approvals still waiting on this person, newest first."""
    items = [
        public_item(rid, entry) for rid, entry in list(_pending().items())
        if _open(entry) and str(entry.get('user_key')) == str(user_key)
    ]
    items.sort(key=lambda item: item['created_at'], reverse=True)
    return items


def is_due(entry: dict) -> bool:
    """Unanswered, and nobody is looking at it on a chat page."""
    if not _open(entry):
        return False
    now = time.monotonic()
    seen_at = entry.get('seen_at')
    if seen_at is None:
        return now - float(entry.get('created') or 0) >= alert_delay()
    return now - float(seen_at) >= away_delay()


def due_chat_approvals(user_key: str) -> list[tuple[str, dict]]:
    return [(rid, entry) for rid, entry in list(_pending().items())
            if str(entry.get('user_key')) == str(user_key) and is_due(entry)]


def still_pending(request_id: str) -> bool:
    return _open(_pending().get(request_id))


def mark_seen(request_id: str, user_key: str | None) -> bool:
    """The chat page is showing the card to the person right now: hold off
    sending it elsewhere."""
    entry = _pending().get(request_id)
    if not _open(entry) or (user_key is not None and str(entry.get('user_key')) != str(user_key)):
        return False
    entry['seen_at'] = time.monotonic()
    return True


# ------------------------------------------------------------- action tokens

def _token_key() -> str:
    # Derived from, never equal to, the sign-in key, so an action token can
    # never be used to sign in.
    from cognitrix.common.constants import JWT_SECRET_KEY

    return hashlib.sha256(f'{JWT_SECRET_KEY}:approval-action'.encode()).hexdigest()


def action_token(kind: str, item_id: str, user_key: str, run_id: str = '') -> str:
    """What a notification's Approve/Deny buttons send back."""
    import jwt

    return jwt.encode({
        'typ': _TOKEN_TYPE, 'k': kind, 'id': item_id, 'run': run_id, 'u': str(user_key),
        'exp': int(time.time()) + ACTION_TOKEN_TTL,
    }, _token_key(), algorithm='HS256')


def read_action_token(token: str) -> dict[str, str] | None:
    import jwt

    try:
        data = jwt.decode(token, _token_key(), algorithms=['HS256'])
    except Exception:
        return None
    if data.get('typ') != _TOKEN_TYPE or data.get('k') not in ('chat', 'task') or not data.get('id'):
        return None
    return {'kind': data['k'], 'id': str(data['id']), 'run_id': str(data.get('run') or ''),
            'user_key': str(data.get('u') or '')}


async def _account_active(user_key: str) -> bool:
    """The token's person still has an enabled account: disabling someone
    cuts off notifications they were already sent."""
    from cognitrix.models import User

    if not user_key:
        return False
    user = await User.get(user_key) or await User.find_one({'email': user_key})
    return user is not None and not getattr(user, 'disabled', None)


async def answer_with_token(token: str, approved: bool) -> str:
    """Answer an approval from a notification button. Returns 'approved',
    'denied', 'gone' (already answered or ended) or 'invalid'."""
    data = read_action_token(token)
    if data is None or not await _account_active(data['user_key']):
        return 'invalid'
    if data['kind'] == 'chat':
        from cognitrix.safety.approval_gate import resolve_web_approval

        if not still_pending(data['id']):
            return 'gone'
        ok = resolve_web_approval(data['id'], approved=approved, user_key=data['user_key'])
    else:
        from cognitrix.tasks import approvals

        row = await approvals.TaskApproval.get(data['id'])
        if row is None or str(row.user_id) != data['user_key'] or row.run_id != data['run_id']:
            return 'invalid'
        ok = await approvals.decide(data['id'], data['run_id'], approved) is not None
    if not ok:
        return 'gone'
    return 'approved' if approved else 'denied'


def push_actions(kind: str, item_id: str, user_key: str, run_id: str = '') -> dict[str, Any]:
    """The part of a push message that adds Approve and Deny buttons."""
    return {
        'actions': [{'action': 'approve', 'title': 'Approve'}, {'action': 'deny', 'title': 'Deny'}],
        'action_token': action_token(kind, item_id, user_key, run_id),
        'require_interaction': True,
    }


# --------------------------------------------------------------- web push

async def _push_later(request_id: str) -> None:
    """Push once, as soon as the approval is due; stop when it's answered."""
    await asyncio.sleep(alert_delay())
    while True:
        entry = _pending().get(request_id)
        # A turn started from a chat app already put the buttons there.
        if not _open(entry) or entry.get('origin'):
            return
        if is_due(entry):
            break
        await asyncio.sleep(2.0)
    from cognitrix.common import webpush

    user_key = str(entry.get('user_key') or '')
    await webpush.send_to_user(
        user_key, f'Approval needed: {headline(entry)}',
        params_preview(entry.get('params'), 300),
        url=entry.get('href') or '/inbox', tag=f'approval-{request_id}',
        extra=push_actions('chat', request_id, user_key),
    )


def schedule(request_id: str) -> None:
    """Called when a chat approval starts waiting."""
    from cognitrix.common import webpush

    entry = _pending().get(request_id)
    if not entry or not entry.get('user_key') or not webpush.enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_push_later(request_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
