"""New mail for the main assistant, sorted by plain rules before any model sees it.

When the person has Gmail connected, each check-in first asks Gmail what
arrived since last time (``tasks/mail_triggers.new_mail``: metadata only, no
model). Each message is sorted by rules:

* ``skip``: newsletters, receipts and no-reply senders. Never shown to a model.
* ``invite``: calendar invitations.
* ``reply``: likely needs an answer: a person the assistant remembers, a reply
  on an existing thread, or a direct question.
* ``fyi``: everything else.

The rest (reply, invite, fyi) is listed for the check-in, which decides on the
small model what really needs a reply and can prepare drafts (sending still
asks). New non-skip mail also counts as a change for the free change check.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

KEEP_DAYS = 3
KEEP_PER_USER = 60

_SKIP_SENDER = re.compile(
    r'(no-?reply|do-?not-?reply|notifications?@|newsletter|mailer-daemon|news@|marketing|updates@|digest|'
    r'info@|billing@|receipts?@|alerts?@)', re.IGNORECASE)
_SKIP_TEXT = re.compile(r'(unsubscribe|view (this|it) in your browser|your receipt|order confirmation)', re.IGNORECASE)
_INVITE = re.compile(r'^(updated )?invitation:|^accepted:|^declined:', re.IGNORECASE)
_REPLY_SUBJECT = re.compile(r'^\s*(re|aw|sv)\s*:', re.IGNORECASE)
_ADDRESS = re.compile(r'<([^>]+)>|([\w.+-]+@[\w-]+\.[\w.-]+)')


class AssistantMail(Model):
    user_id: str
    mail_id: str
    thread_id: str = ''
    sender: str = ''
    subject: str = ''
    snippet: str = ''
    category: str = 'fyi'
    reason: str = ''
    received: str = ''
    """Naive UTC when the assistant first saw it."""

    @field_validator('thread_id', 'sender', 'subject', 'snippet', 'reason', 'received', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)


MODELS = (AssistantMail,)


def _address(sender: str) -> str:
    match = _ADDRESS.search(sender or '')
    if not match:
        return ''
    return (match.group(1) or match.group(2) or '').strip().lower()


def _name(sender: str) -> str:
    name = re.sub(r'<[^>]*>', '', sender or '').strip().strip('"').strip()
    return name.lower()


def sort_mail(mail: dict[str, str], known: list[str]) -> tuple[str, str]:
    """(category, reason) for one message. ``known`` is lower-cased memory text."""
    sender = mail.get('from', '')
    subject = mail.get('subject', '')
    snippet = mail.get('snippet', '')
    address = _address(sender)
    if _INVITE.search(subject):
        return 'invite', 'calendar invitation'
    if _SKIP_SENDER.search(address or sender) or _SKIP_TEXT.search(snippet):
        return 'skip', 'automated sender'
    name = _name(sender)
    first = name.split()[0] if name else ''
    if address and any(address in k for k in known):
        return 'reply', 'from someone you know'
    if first and len(first) > 2 and any(re.search(rf'\b{re.escape(first)}\b', k) for k in known):
        return 'reply', 'from someone you know'
    if _REPLY_SUBJECT.search(subject):
        return 'reply', 'reply on a thread you are in'
    if '?' in snippet:
        return 'reply', 'asks a question'
    return 'fyi', ''


async def _known(user_id: str) -> list[str]:
    from cognitrix.models.memory import Memory

    try:
        rows = await Memory.find({'user_id': str(user_id)}) or []
    except Exception:
        return []
    return [r.content.lower() for r in rows if not r.pending]


async def check_new_mail(user_id: str, settings: Any, now: datetime | None = None) -> list[AssistantMail]:
    """Fetch and sort what arrived since the last check. Returns the new non-skip rows."""
    from cognitrix.mcp import oauth
    from cognitrix.tasks.mail_triggers import gmail_connection, new_mail

    from .proactivity import save_settings

    row = await gmail_connection(str(user_id))
    if row is None:
        return []
    try:
        token = await oauth.access_token(row)
        cursor, mails, _more = await new_mail(token, getattr(settings, 'mail_cursor', '') or None)
    except Exception:
        logger.info('Could not check Gmail for %s', user_id, exc_info=True)
        return []
    await save_settings(str(user_id), mail_cursor=cursor)
    known = await _known(user_id)
    stamp = (now or datetime.now(UTC)).strftime('%Y-%m-%d %H:%M:%S')
    added: list[AssistantMail] = []
    for mail in mails:
        category, reason = sort_mail(mail, known)
        if await AssistantMail.find_one({'user_id': str(user_id), 'mail_id': mail.get('id', '')}):
            continue
        item = AssistantMail(
            user_id=str(user_id), mail_id=mail.get('id', ''), thread_id=mail.get('thread_id', ''),
            sender=mail.get('from', '')[:200], subject=mail.get('subject', '')[:200],
            snippet=mail.get('snippet', '')[:300], category=category, reason=reason, received=stamp)
        await item.save()
        if category != 'skip':
            added.append(item)
    await prune(user_id)
    return added


async def recent(user_id: str, *, include_skipped: bool = False) -> list[AssistantMail]:
    try:
        rows = list(await AssistantMail.find({'user_id': str(user_id)}) or [])
    except Exception:
        return []
    rows.sort(key=lambda r: r.received, reverse=True)
    return [r for r in rows if include_skipped or r.category != 'skip']


async def prune(user_id: str) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    rows = await recent(user_id, include_skipped=True)
    for i, row in enumerate(rows):
        if i >= KEEP_PER_USER or row.received < cutoff:
            await AssistantMail.delete_many({'id': row.id})


def describe(rows: list[AssistantMail], limit: int = 12) -> str:
    """The list a check-in reads: mail sorted by rules, newest first."""
    if not rows:
        return ''
    order = {'reply': 0, 'invite': 1, 'fyi': 2}
    rows = sorted(rows, key=lambda r: (order.get(r.category, 3), r.received))[:limit]
    labels = {'reply': 'Probably needs a reply', 'invite': 'Invitation', 'fyi': 'For information'}
    lines = []
    for r in rows:
        why = f' ({r.reason})' if r.reason else ''
        lines.append(f'- [{labels.get(r.category, r.category)}{why}] From {r.sender}: "{r.subject}" '
                     f'(thread {r.thread_id}) {r.snippet[:160]}')
    return '\n'.join(lines)
